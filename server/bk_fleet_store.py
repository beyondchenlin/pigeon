"""Durable building blocks for BK fleet jobs, not yet wired to a scheduler.

The native executor only processes jobs in its existing running/paused states.
BK jobs use a distinct state until the complete dispatcher is installed.
"""
import time
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.types import JSON

from executor.models import Base, Device, DeviceJob, Group, Job, Task, User, UserDevice
from server.bk_ai_store import AiTask, TERMINAL, active, locked_device, request_cancel, script_busy
from server.bk_execution import supervision_timeout
from server.bk_plan import PlanError, execution_id, local_instant, next_daily, plan_fingerprint

BK_RUNNING = 'bk_running'
BK_STOPPED = 'bk_stopped'
BK_FINISHED = 'bk_finished'
BK_DONE = 'bk_done'
BK_PAUSED = 'bk_paused'
BK_STATES = (BK_RUNNING, BK_STOPPED, BK_FINISHED, BK_DONE, BK_PAUSED)
MISFIRE_GRACE_S = 30


class FleetDraft(Base):
    __tablename__ = 'bk_fleet_draft'
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id'), nullable=False, index=True)
    message = Column(Text, nullable=False)
    model_id = Column(Integer, ForeignKey('modelresource.id'), nullable=False)
    timezone = Column(String(100), nullable=False)
    execution = Column(JSON)
    question = Column(Text)
    plan = Column(JSON)
    state = Column(String(24), nullable=False)
    revision = Column(Integer, nullable=False, default=0)
    job_id = Column(Integer, ForeignKey('job.id'))
    updated = Column(Float, nullable=False, default=time.time)


def first_slot(schedule, now):
    instant = datetime.fromtimestamp(now, timezone.utc)
    if schedule['kind'] == 'now':
        return int(now)
    if schedule['kind'] == 'once':
        local = datetime.strptime(schedule['at'], '%Y-%m-%dT%H:%M')
        due = int(local_instant(local, schedule['timezone']).timestamp())
        if due <= now:
            raise PlanError('schedule_in_past')
        return due
    return int(next_daily(schedule, instant).timestamp())


def create_job(session, user_id, request_id, plan, now=None):
    """Create one parent and frozen bindings atomically, or return same request.

    The caller must validate the plan against current authorized devices first.
    This function repeats the device ownership check within the transaction.
    """
    now = time.time() if now is None else now
    try:
        if str(uuid.UUID(request_id)) != request_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise PlanError('request_id_invalid') from None
    user = session.query(User).filter_by(id=user_id).with_for_update().one_or_none()
    if user is None:
        raise PlanError('user_not_found')
    signature = plan_fingerprint(plan)
    jobs = session.query(Job).filter(Job.owner_id == user_id, Job.state.in_(BK_STATES)).all()
    for old in jobs:
        meta = (old.config or {}).get('bk_fleet') or {}
        if meta.get('request_id') == request_id:
            if meta.get('fingerprint') != signature:
                raise PlanError('request_reused')
            return old, False
    domains = plan.get('devices')
    if not isinstance(domains, list) or not domains or len(domains) != len(set(domains)):
        raise PlanError('device_identity_invalid')
    devices = (session.query(Device).join(UserDevice, UserDevice.device_id == Device.id)
               .filter(UserDevice.user_id == user_id, Device.is_deleted == False,
                       Device.domain.in_(domains)).all())
    if {device.domain for device in devices} != set(domains):
        raise PlanError('device_access_changed')
    group_id = plan.get('group_id')
    if group_id is not None and not session.query(Group.id).filter_by(id=group_id, owner_id=user_id).first():
        raise PlanError('group_access_changed')
    next_at = first_slot(plan['schedule'], now)
    meta = {'request_id': request_id, 'fingerprint': signature, 'plan': plan,
            'next_at': next_at, 'created_at': now}
    # The BK-only state keeps the native script executor from adding members;
    # group_id remains linked for the existing job list and provenance.
    job = Job(owner_id=user_id, name=plan['goal'][:255], group_id=group_id, script_id=None,
              state=BK_RUNNING, mode='bk_managed', config={'bk_fleet': meta},
              start_time=int(now), create_time=int(now))
    session.add(job)
    session.flush()
    for device in sorted(devices, key=lambda item: item.id):
        session.add(DeviceJob(job_id=job.id, device_id=device.id))
    session.flush()
    return job, True


def materialize_due(session, job_id, now=None):
    """Record one due round and its per-device receipts before any network call.

    Returns admitted AiTask ids to dispatch after the transaction commits.
    Missing, busy, revoked and late devices receive a terminal missed record.
    A caller must serialize cancellation using the same locked parent row.
    """
    now = time.time() if now is None else now
    job = session.query(Job).filter_by(id=job_id).with_for_update().one_or_none()
    if job is None or job.state != BK_RUNNING:
        return []
    meta = dict((job.config or {}).get('bk_fleet') or {})
    due = meta.get('next_at')
    if type(due) is not int or due > now:
        return []
    plan = meta['plan']
    slot = datetime.fromtimestamp(due, timezone.utc).isoformat()
    dispatch = []
    bindings = (session.query(DeviceJob).filter_by(job_id=job.id)
                .order_by(DeviceJob.device_id).all())
    bound_domains = {session.get(Device, binding.device_id).domain for binding in bindings
                     if session.get(Device, binding.device_id) is not None}
    if len(bindings) != len(plan['devices']) or bound_domains != set(plan['devices']):
        raise PlanError('device_binding_missing')
    for binding in bindings:
        device = locked_device(session, binding.device_id)
        request_id = execution_id(job.id, device.domain, slot)
        task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'pigeon-bk:' + device.domain + ':' + request_id))
        if session.query(Task).filter_by(task_id=task_id).first():
            continue
        authorized = session.query(UserDevice.id).filter_by(user_id=job.owner_id, device_id=device.id).first()
        reason = ('missed_schedule' if now - due > MISFIRE_GRACE_S else
                  'access_revoked' if not authorized or device.is_deleted else
                  'device_offline' if device.state != 'online' else
                  'device_busy' if device.controlling or script_busy(session, device.id)
                      or active(session, device.id) else None)
        task = Task(task_id=task_id, device_id=device.id, job_id=job.id, script_id=None,
                    state='failed' if reason else 'bk_pending', reason=reason,
                    params={'scheduled_at': due, 'bk_request_id': request_id},
                    create_time=int(now), finish_time=int(now) if reason else 0)
        session.add(task)
        if not reason:
            execution = plan.get('execution')
            timeout = supervision_timeout(execution)
            row = AiTask(id=task_id, device_id=device.id, user_id=job.owner_id, request_id=request_id,
                goal=plan['goal'], status='submitting', created=now, updated=now, deadline=now + timeout,
                history=[{'at': now, 'status': 'submitting', 'error': None}], snapshot={},
                execution=execution, sync_meta={'admitted': True, 'job_id': job.id, 'scheduled_at': due})
            session.add(row)
            dispatch.append(task_id)
        session.flush()
    if plan['schedule']['kind'] == 'daily':
        try:
            next_slot = next_daily(plan['schedule'], datetime.fromtimestamp(due, timezone.utc))
            meta['next_at'] = int(next_slot.timestamp())
        except PlanError as error:
            # The current round is valid; pause only future ambiguous rounds.
            meta['next_at'] = None
            meta['schedule_error'] = error.code
            job.state = BK_PAUSED
    else:
        meta['next_at'] = None
        job.state = BK_FINISHED
    job.config = dict(job.config or {}, bk_fleet=meta)
    return dispatch


def cancel_job(session, job_id, user_id, stop_current=False):
    """Prevent later rounds and optionally record stop intents for this round."""
    job = (session.query(Job).filter_by(id=job_id, owner_id=user_id)
           .with_for_update().one_or_none())
    if job is None or job.state not in BK_STATES:
        raise PlanError('job_not_found')
    job.state = BK_STOPPED
    meta = dict((job.config or {}).get('bk_fleet') or {})
    meta['next_at'] = None
    job.config = dict(job.config or {}, bk_fleet=meta)
    if not stop_current:
        return []
    rows = (session.query(AiTask).join(Task, Task.task_id == AiTask.id)
            .filter(Task.job_id == job.id, AiTask.status.notin_(TERMINAL))
            .with_for_update().all())
    for row in rows:
        request_cancel(row)
    return [row.id for row in rows]


def sync_job_results(session, job_id, now=None):
    """Reflect actual phone receipts into existing Task and DeviceJob rows."""
    now = time.time() if now is None else now
    job = session.query(Job).filter_by(id=job_id).with_for_update().one_or_none()
    if job is None or job.state not in BK_STATES:
        return
    pairs = (session.query(Task, AiTask).join(AiTask, Task.task_id == AiTask.id)
             .filter(Task.job_id == job.id).all())
    for task, receipt in pairs:
        if task.state in ('success', 'failed', 'revoked'):
            continue
        if receipt.status == 'done':
            task.state = 'success'
        elif receipt.status in ('failed', 'rejected'):
            task.state = 'failed'
        elif receipt.status == 'cancelled':
            task.state = 'revoked'
        else:
            # Unknown transport does not release the existing script guard.
            task.state = 'bk_active'
            continue
        task.finish_time = int(now)
        detail = receipt.error or (receipt.snapshot or {}).get('reason') or (receipt.snapshot or {}).get('message')
        task.reason = detail[:500] if isinstance(detail, str) else None
        task.result = receipt.snapshot
    session.flush()
    tasks = session.query(Task).filter_by(job_id=job.id).all()
    job.issued = len(pairs)
    job.success = sum(task.state == 'success' for task in tasks)
    job.failed = sum(task.state == 'failed' for task in tasks)
    job.revoked = sum(task.state == 'revoked' for task in tasks)
    if job.state == BK_FINISHED and tasks and all(task.state in ('success', 'failed', 'revoked') for task in tasks):
        job.state = BK_DONE
        job.stop_time = int(now)
    for binding in session.query(DeviceJob).filter_by(job_id=job.id):
        own = [task for task in tasks if task.device_id == binding.device_id]
        binding.issued = sum(task.device_id == binding.device_id for task, _ in pairs)
        binding.success = sum(task.state == 'success' for task in own)
        binding.failed = sum(task.state == 'failed' for task in own)
        binding.revoked = sum(task.state == 'revoked' for task in own)
        binding.task_state = 'running' if any(task.state in ('bk_pending', 'bk_active') for task in own) else 'idle'
