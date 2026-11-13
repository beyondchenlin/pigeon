"""Durable BK task receipts in PIGEON's existing PostgreSQL database."""
import time
import hashlib
import json
from sqlalchemy import Column, Integer, String, Text, Float, Boolean, ForeignKey, UniqueConstraint, inspect, text
from sqlalchemy.types import JSON
from executor.models import Base, Device, Task

TERMINAL = frozenset(('done', 'failed', 'cancelled', 'rejected'))


class AiTask(Base):
    __tablename__ = 'bk_ai_task'
    id = Column(String(36), primary_key=True)
    device_id = Column(Integer, ForeignKey('device.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('user.id'), nullable=False)
    request_id = Column(String(36), nullable=False)
    goal = Column(Text, nullable=False)
    status = Column(String(32), nullable=False)
    snapshot = Column(JSON, nullable=False, default=dict)
    # Nullable additive fields keep historical receipts readable. Never trust a
    # phone response to replace the immutable contract or server receive times.
    execution = Column(JSON)
    sync_meta = Column(JSON)
    history = Column(JSON, nullable=False, default=list)
    error = Column(String(64))
    created = Column(Float, nullable=False, default=time.time)
    updated = Column(Float, nullable=False, default=time.time)
    deadline = Column(Float, nullable=False)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    __table_args__ = (UniqueConstraint('device_id', 'request_id'),)


class AiAnswer(Base):
    __tablename__ = 'bk_ai_answer'
    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), ForeignKey('bk_ai_task.id'), nullable=False, index=True)
    question_key = Column(String(64), nullable=False)
    text_sha256 = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False)
    created = Column(Float, nullable=False, default=time.time)
    __table_args__ = (UniqueConstraint('task_id', 'question_key'),)


def question_key(body):
    if body.get('status') != 'waiting_for_answer' or body.get('answerable') is not True or not body.get('question'):
        return None
    value = {k: body.get(k) for k in ('task_id', 'conversation', 'started_at_ms', 'question', 'options')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def active(session, device_id):
    return session.query(AiTask).filter(AiTask.device_id == device_id,
                                      AiTask.status.notin_(TERMINAL)).first()


def locked_device(session, device_id):
    return session.query(Device).filter(Device.id == device_id).with_for_update().one()


def script_busy(session, device_id):
    return session.query(Task.id).filter(Task.device_id == device_id,
        Task.state.in_(('pending', 'issued', 'running'))).first() is not None


def change(row, status, error=None, snapshot=None):
    now = time.time()
    if row.status != status or row.error != error:
        row.history = (row.history + [{'at': now, 'status': status, 'error': error}])[-100:]
    row.status, row.error, row.updated = status, error, now
    if snapshot is not None:
        row.snapshot = snapshot


def update_sync(row, **values):
    row.sync_meta = dict(row.sync_meta or {}, **values)


def request_cancel(row, reason='user_stop'):
    if row.status in TERMINAL:
        return
    now = time.time()
    row.cancel_requested = True
    meta = row.sync_meta or {}
    if not meta.get('cancel_requested_at'):
        update_sync(row, cancel_requested_at=now, cancel_reason=reason)


def ensure_schema(engine):
    """Add only nullable receipt metadata; no existing task is rewritten."""
    AiTask.__table__.create(engine, checkfirst=True)
    with engine.begin() as connection:
        columns = {column['name'] for column in inspect(connection).get_columns(AiTask.__tablename__)}
        for name in ('execution', 'sync_meta'):
            if name not in columns:
                guard = ' IF NOT EXISTS' if connection.dialect.name == 'postgresql' else ''
                connection.execute(text(f'ALTER TABLE bk_ai_task ADD COLUMN{guard} {name} JSON'))


def describe(row, session=None):
    key = question_key(row.snapshot) if row.status == 'waiting_for_answer' else None
    answer = session.query(AiAnswer).filter_by(task_id=row.id, question_key=key).first() if session and key else None
    meta = row.sync_meta or {}
    execution = row.execution or None
    received = meta.get('phone_received_at')
    stop_at = meta.get('cancel_requested_at')
    stop_budget = (execution or {}).get('stop_timeout_s', 30)
    return dict(task_id=row.id, request_id=row.request_id, goal=row.goal, status=row.status,
        result=row.snapshot, error=row.error, created_at=row.created, updated_at=row.updated,
        deadline=row.deadline, cancel_requested=row.cancel_requested, history=row.history,
        execution=execution, phone_received_at=received, source_event_at_ms=meta.get('source_event_at_ms'),
        last_contact_at=meta.get('last_contact_at'), cancel_requested_at=stop_at,
        stop_confirmation_overdue=bool(row.cancel_requested and row.status not in TERMINAL
                                      and stop_at and time.time() - stop_at >= stop_budget),
        stale=bool(row.status not in TERMINAL and (row.status == 'unknown' or not received
                   or time.time() - received > (execution or {}).get('progress_interval_s', 15)
                   + (execution or {}).get('event_latency_s', 15))),
        question_key=key, answer_submission=dict(request_id=answer.id, status=answer.status) if answer else None)


def install_script_guard():
    """Same device row lock as task creation, held until scheduler commit."""
    from executor.handlers import event
    if getattr(event, '_bk_ai_guard', False):
        return
    original = event.cb_task_status_prepared_imp

    def dispatch(session, dt):
        device = locked_device(session, dt.device_id)
        if device.controlling or active(session, device.id):
            return None
        return original(session, dt)

    event.cb_task_status_prepared_imp = dispatch
    event._bk_ai_guard = True