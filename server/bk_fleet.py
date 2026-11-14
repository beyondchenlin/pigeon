"""Authenticated preview of BK fleet plans; this endpoint never dispatches."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import time
import uuid
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from executor.models import Device, DeviceJob, Group, Job, ModelResource, Task, User, UserDevice, session_scope
from server.bk_ai import AiHandler, perform_cancel
from server.bk_fleet_store import (BK_PAUSED, BK_RUNNING, BK_STATES, FleetDraft, cancel_job, create_job,
                                   materialize_due, sync_job_results)
from server.bk_plan import PlanError, plan_fingerprint, validate_plan
from server.bk_interpreter import InterpretationError, interpret

FLEET_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='bk-fleet')
INTERPRETER_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='bk-plan')
FLEET_POLLING = False


def install_legacy_action_guards():
    """Protect BK jobs on deployments where server.service is compiled."""
    from server import service
    for cls, method in ((service.PlatformSpecificDeviceJobHandler, 'post'),
                        (service.PlatformSpecificJobScriptVersionHandler, 'post'),
                        (service.PlatformSpecificJobTaskListHandler, 'delete')):
        original = getattr(cls, method)
        if getattr(original, '_bk_fleet_guard', False):
            continue
        def make_guard(original_method):
            def guarded(handler, job_id, *args, **kwargs):
                with session_scope() as session:
                    user = handler.get_login_user(session=session)
                    _, job = handler.get_login_user_job_owner(job_id, user=user, session=session)
                    if job.mode == 'bk_managed':
                        handler.throw(409, 'BK_JOB_ACTION', 'Use the BK task controls for this job')
                return original_method(handler, job_id, *args, **kwargs)
            guarded._bk_fleet_guard = True
            return guarded
        setattr(cls, method, make_guard(original))


def _poll_once():
    """Commit each round before the existing BK receipt poll can send it."""
    with session_scope() as session:
        running = {row.id for row in session.query(Job.id).filter_by(mode='bk_managed', state=BK_RUNNING)}
        unfinished = {row.job_id for row in session.query(Task.job_id).join(Job, Job.id == Task.job_id)
                      .filter(Job.mode == 'bk_managed', Task.state.in_(('bk_pending', 'bk_active')))}
    for job_id in sorted(running | unfinished):
        try:
            with session_scope() as session:
                materialize_due(session, job_id)
                sync_job_results(session, job_id)
        except PlanError as error:
            with session_scope() as session:
                job = session.query(Job).filter_by(id=job_id).with_for_update().one_or_none()
                if job and job.state == BK_RUNNING:
                    meta = dict((job.config or {}).get('bk_fleet') or {})
                    meta['schedule_error'] = error.code
                    job.config = dict(job.config or {}, bk_fleet=meta)
                    job.state = BK_PAUSED
        except Exception:
            # One problematic job must not prevent other devices from being checked.
            logging.exception('Unable to process BK fleet job %s', job_id)


async def poll():
    global FLEET_POLLING
    if FLEET_POLLING:
        return
    FLEET_POLLING = True
    try:
        await asyncio.get_running_loop().run_in_executor(FLEET_POOL, _poll_once)
    finally:
        FLEET_POLLING = False


class FleetPlanPreviewHandler(AiHandler):
    def post(self):
        candidate = self.body()
        with session_scope() as session:
            user = self.get_login_user(session=session)
            rows = (session.query(Device, UserDevice)
                    .join(UserDevice, UserDevice.device_id == Device.id)
                    .filter(UserDevice.user_id == user.id, Device.is_deleted == False)
                    .all())
            devices = [{'domain': device.domain, 'name': device.comment,
                        'group_id': binding.group_id} for device, binding in rows]
            groups = [{'id': group.id, 'name': group.name}
                      for group in session.query(Group).filter(Group.owner_id == user.id)]
            try:
                plan = validate_plan(candidate, devices, groups)
            except PlanError as error:
                self.throw(400, 'BK_PLAN', error.code)
            self.tell({'plan': plan, 'submitted': False})


class FleetChatPlanHandler(AiHandler):
    async def post(self):
        body = self.body()
        if (set(body) - {'message', 'model_id', 'timezone', 'execution', 'draft_id'}
                or not {'message', 'model_id', 'timezone'}.issubset(body)
                or type(body['model_id']) is not int or not isinstance(body['message'], str)
                or not 1 <= len(body['message'].strip()) <= 4000):
            self.throw(400, 'BK_PLAN', 'message, model_id and timezone are required')
        draft_id = body.get('draft_id') or str(uuid.uuid4())
        try:
            if str(uuid.UUID(draft_id)) != draft_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            self.throw(400, 'BK_PLAN', 'draft_id_invalid')
        try:
            zone = ZoneInfo(body['timezone'])
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            self.throw(400, 'BK_PLAN', 'timezone_invalid')
        with session_scope() as session:
            user = self.get_login_user(session=session)
            saved = session.query(ModelResource).filter_by(owner_id=user.id, id=body['model_id']).first()
            if saved is None:
                self.throw(404, 'BK_MODEL', 'model_not_found')
            model = SimpleNamespace(provider=saved.provider, api_base=saved.api_base,
                                    model_name=saved.model_name, api_key=saved.api_key)
            draft = session.query(FleetDraft).filter_by(id=draft_id).with_for_update().first()
            if draft and (draft.user_id != user.id or draft.state == 'submitted'):
                self.throw(409, 'BK_DRAFT', 'draft_unavailable')
            if draft is None:
                draft = FleetDraft(id=draft_id, user_id=user.id, message=body['message'],
                                   model_id=saved.id, timezone=body['timezone'], state='processing', revision=0)
                session.add(draft)
            draft.message = body['message'].strip()
            draft.model_id = saved.id
            draft.timezone = body['timezone']
            draft.execution = body.get('execution')
            draft.question = None
            draft.plan = None
            draft.state = 'processing'
            draft.revision = (draft.revision or 0) + 1
            draft.updated = time.time()
            revision = draft.revision
            rows = (session.query(Device, UserDevice).join(UserDevice, UserDevice.device_id == Device.id)
                    .filter(UserDevice.user_id == user.id, Device.is_deleted == False).all())
            devices = [{'domain': d.domain, 'name': d.comment, 'group_id': link.group_id} for d, link in rows]
            groups = [{'id': g.id, 'name': g.name} for g in session.query(Group).filter_by(owner_id=user.id)]
        try:
            candidate = await asyncio.get_running_loop().run_in_executor(
                INTERPRETER_POOL, interpret, model, body['message'], body['timezone'],
                {'devices': devices, 'groups': groups}, datetime.now(zone).isoformat())
            if 'clarification' in candidate:
                self.save_result(draft_id, user.id, revision, question=candidate['clarification'])
                self.tell({'draft_id': draft_id, 'clarification': candidate['clarification'], 'submitted': False})
                return
            if 'execution' in body:
                candidate['execution'] = body['execution']
            plan = validate_plan(candidate, devices, groups)
            target_name = plan['target']['name'].strip()
            if target_name not in body['message']:
                question = '请在任务内容中写出要使用的设备名或分组名。'
                self.save_result(draft_id, user.id, revision, question=question)
                self.tell({'draft_id': draft_id, 'clarification': question, 'submitted': False})
                return
            if plan['schedule']['kind'] != 'now' and plan['schedule']['timezone'] != body['timezone']:
                question = '识别出的时区与页面选择的不一致，请明确使用的时区后重新描述。'
                self.save_result(draft_id, user.id, revision, question=question)
                self.tell({'draft_id': draft_id, 'clarification': question, 'submitted': False})
                return
        except InterpretationError as error:
            self.save_result(draft_id, user.id, revision, error=True)
            self.throw(503, 'BK_MODEL', str(error))
        except PlanError as error:
            self.save_result(draft_id, user.id, revision, question=error.code)
            self.tell({'draft_id': draft_id, 'clarification': error.code, 'submitted': False})
            return
        self.save_result(draft_id, user.id, revision, plan=plan)
        self.tell({'draft_id': draft_id, 'plan': plan, 'submitted': False})

    def save_result(self, draft_id, user_id, revision, plan=None, question=None, error=False):
        with session_scope() as session:
            draft = session.query(FleetDraft).filter_by(id=draft_id, user_id=user_id).with_for_update().one_or_none()
            if not draft or draft.revision != revision or draft.state != 'processing':
                self.throw(409, 'BK_DRAFT', 'draft_changed')
            draft.plan = plan
            draft.question = question
            draft.state = 'error' if error else 'clarify' if question else 'ready'
            draft.updated = time.time()


class FleetDraftHandler(AiHandler):
    def get(self, draft_id):
        with session_scope() as session:
            user = self.get_login_user(session=session)
            draft = session.query(FleetDraft).filter_by(id=draft_id, user_id=user.id).first()
            if draft is None:
                self.throw(404, 'BK_DRAFT', 'draft_not_found')
            self.tell({'id':draft.id, 'message':draft.message, 'model_id':draft.model_id,
                       'timezone':draft.timezone, 'execution':draft.execution, 'question':draft.question,
                       'plan':draft.plan, 'state':draft.state, 'job_id':draft.job_id})


class FleetModelListHandler(AiHandler):
    def get(self):
        with session_scope() as session:
            user = self.get_login_user(session=session)
            models = session.query(ModelResource).filter_by(owner_id=user.id).order_by(ModelResource.id).all()
            self.tell({'user_id': user.id, 'models': [{'id': model.id, 'name': model.name, 'provider': model.provider}
                                                       for model in models]})


def describe_job(session, job, include_runs=False):
    meta = (job.config or {}).get('bk_fleet') or {}
    authorized = {row.device_id for row in session.query(UserDevice.device_id).filter_by(user_id=job.owner_id)}
    devices = []
    for binding in session.query(DeviceJob).filter_by(job_id=job.id).order_by(DeviceJob.device_id):
        device = session.get(Device, binding.device_id)
        permitted = binding.device_id in authorized and device is not None and not device.is_deleted
        last = (session.query(Task).filter_by(job_id=job.id, device_id=binding.device_id)
                .order_by(Task.create_time.desc(), Task.id.desc()).first())
        devices.append({'domain': device.domain if permitted else None, 'authorized': permitted,
                        'state': binding.task_state, 'success': binding.success, 'failed': binding.failed,
                        'last_task': ({'task_id': last.task_id, 'state': last.state, 'reason': last.reason,
                                       'answer': (last.result or {}).get('answer'),
                                       'scheduled_at': (last.params or {}).get('scheduled_at')}
                                      if permitted and last else None)})
    result = {'id': job.id, 'state': job.state, 'goal': meta.get('plan', {}).get('goal'),
            'schedule': meta.get('plan', {}).get('schedule'), 'next_at': meta.get('next_at'),
            'schedule_error': meta.get('schedule_error'), 'created_at': job.create_time,
            'devices': devices, 'success': job.success, 'failed': job.failed}
    if include_runs:
        runs = session.query(Task).filter_by(job_id=job.id).order_by(Task.id.desc()).limit(100).all()
        result['runs'] = [{'task_id': task.task_id, 'device': session.get(Device, task.device_id).domain,
                           'state': task.state, 'reason': task.reason, 'answer': (task.result or {}).get('answer'),
                           'scheduled_at': (task.params or {}).get('scheduled_at')}
                          for task in runs if task.device_id in authorized
                          and session.get(Device, task.device_id) is not None
                          and not session.get(Device, task.device_id).is_deleted]
    return result


class FleetJobHandler(AiHandler):
    def get(self, job_id=None, action=None):
        if action:
            self.throw(405, 'BK_JOB_ACTION', 'POST required')
        with session_scope() as session:
            user = self.get_login_user(session=session)
            query = session.query(Job).filter_by(owner_id=user.id, mode='bk_managed')
            if job_id:
                job = query.filter_by(id=int(job_id)).first()
                if job is None:
                    self.throw(404, 'BK_JOB_NOT_FOUND', 'No such BK job')
                self.tell(describe_job(session, job, include_runs=True))
            else:
                self.tell({'jobs': [describe_job(session, row) for row in query.order_by(Job.id.desc()).limit(100)]})

    async def post(self, job_id=None, action=None):
        body = self.body()
        if job_id:
            if action != 'cancel' or set(body) != {'stop_current'} or type(body['stop_current']) is not bool:
                self.throw(400, 'BK_JOB_ACTION', 'Expected stop_current boolean')
            try:
                with session_scope() as session:
                    user = self.get_login_user(session=session)
                    ids = cancel_job(session, int(job_id), user.id, body['stop_current'])
            except PlanError as error:
                self.throw(404, 'BK_JOB_NOT_FOUND', error.code)
            await asyncio.gather(*(perform_cancel(task_id) for task_id in ids))
            self.get(job_id)
            return
        if action or set(body) != {'request_id', 'plan'} or not isinstance(body['plan'], dict):
            self.throw(400, 'BK_PLAN', 'request_id and preview plan required')
        plan = body['plan']
        try:
            with session_scope() as session:
                user = self.get_login_user(session=session)
                session.query(User).filter_by(id=user.id).with_for_update().one()
                existing = session.query(Job).filter(Job.owner_id == user.id, Job.state.in_(BK_STATES)).all()
                prior = next((row for row in existing if ((row.config or {}).get('bk_fleet') or {})
                              .get('request_id') == body['request_id']), None)
                draft = session.query(FleetDraft).filter_by(id=body['request_id']).with_for_update().first()
                if draft and (draft.user_id != user.id or (not prior and
                              (draft.state != 'ready' or draft.plan != plan))):
                    raise PlanError('draft_changed')
                if prior:
                    if ((prior.config or {})['bk_fleet']).get('fingerprint') != plan_fingerprint(plan):
                        raise PlanError('request_reused')
                    job = prior
                else:
                    candidate = {key: plan.get(key) for key in ('goal', 'target', 'schedule')}
                    if 'execution' in plan:
                        candidate['execution'] = plan['execution']
                    rows = (session.query(Device, UserDevice).join(UserDevice, UserDevice.device_id == Device.id)
                            .filter(UserDevice.user_id == user.id, Device.is_deleted == False).all())
                    devices = [{'domain': d.domain, 'name': d.comment, 'group_id': link.group_id} for d, link in rows]
                    groups = [{'id': g.id, 'name': g.name} for g in session.query(Group).filter_by(owner_id=user.id)]
                    checked = validate_plan(candidate, devices, groups)
                    if checked != plan:
                        raise PlanError('preview_changed')
                    job, _ = create_job(session, user.id, body['request_id'], checked)
                if draft:
                    draft.state = 'submitted'
                    draft.job_id = job.id
                    draft.updated = time.time()
                result = describe_job(session, job)
        except (PlanError, TypeError, ValueError) as error:
            self.throw(400, 'BK_PLAN', getattr(error, 'code', 'plan_invalid'))
        self.set_status(202)
        self.tell(result)
