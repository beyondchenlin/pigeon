"""Durable BK task receipts in PIGEON's existing PostgreSQL database."""
import time
import hashlib
import json
from sqlalchemy import Column, Integer, String, Text, Float, Boolean, ForeignKey, UniqueConstraint
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


def describe(row, session=None):
    key = question_key(row.snapshot) if row.status == 'waiting_for_answer' else None
    answer = session.query(AiAnswer).filter_by(task_id=row.id, question_key=key).first() if session and key else None
    return dict(task_id=row.id, request_id=row.request_id, goal=row.goal, status=row.status,
        result=row.snapshot, error=row.error, created_at=row.created, updated_at=row.updated,
        deadline=row.deadline, cancel_requested=row.cancel_requested, history=row.history,
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
