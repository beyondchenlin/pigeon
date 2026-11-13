"""Versioned, deterministic browse contract shared by task admission and recovery."""
import hashlib
import json

DEFAULT_TIMEOUT_S = 900
MIN_TIMEOUT_S = 15
MAX_TIMEOUT_S = 10800
START_ALLOWANCE_S = 120
FIELDS = frozenset((
    'version', 'kind', 'duration_s', 'clock', 'finish_timeout_s',
    'stop_timeout_s', 'progress_interval_s', 'event_latency_s',
    'budget', 'progress_requirements',
))


class ContractError(ValueError):
    pass


def integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ContractError(f'{name} must be an integer between {minimum} and {maximum}')
    return value


def validate_execution(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ContractError('execution requires exactly the version 1 browse fields')
    if type(value['version']) is not int or value['version'] != 1:
        raise ContractError('unsupported execution version')
    if value['kind'] != 'browse' or value['clock'] != 'elapsed':
        raise ContractError('only browse with elapsed time is supported')
    duration = integer(value['duration_s'], 'duration_s', 1, 10000)
    for name, maximum in (('finish_timeout_s', 120), ('stop_timeout_s', 120),
                          ('progress_interval_s', 3600), ('event_latency_s', 30)):
        integer(value[name], name, 1, maximum)
    budget = value['budget']
    if not isinstance(budget, dict) or set(budget) != {'max_tokens'}:
        raise ContractError('budget requires exactly max_tokens')
    integer(budget['max_tokens'], 'max_tokens', 1, 10000000)
    progress = value['progress_requirements']
    if not isinstance(progress, dict) or set(progress) != {'min_observations', 'min_active_s', 'max_stall_s'}:
        raise ContractError('progress_requirements fields are required')
    integer(progress['min_observations'], 'min_observations', 2, 1000000)
    integer(progress['min_active_s'], 'min_active_s', 1, duration)
    integer(progress['max_stall_s'], 'max_stall_s', 1, 10800)
    return json.loads(json.dumps(value, sort_keys=True))


def supervision_timeout(execution, value=None, supplied=False):
    minimum = (START_ALLOWANCE_S + execution['duration_s'] + execution['finish_timeout_s']
               + execution['event_latency_s'] + execution['stop_timeout_s']) if execution else MIN_TIMEOUT_S
    if not supplied:
        value = minimum if execution else DEFAULT_TIMEOUT_S
    integer(value, 'timeout_s', MIN_TIMEOUT_S, MAX_TIMEOUT_S)
    if value < minimum:
        raise ContractError(f'timeout_s must cover startup, execution and finalization: at least {minimum}')
    return value


def request_signature(goal, timeout, execution):
    value = {'goal': goal, 'timeout_s': timeout, 'execution': execution or None}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':')).encode()).hexdigest()


def supports_execution(reply):
    body = reply.get('body')
    return (reply.get('http') == 200 and isinstance(body, dict)
            and isinstance(body.get('execution_versions'), list)
            and any(type(version) is int and version == 1 for version in body['execution_versions'])
            and body.get('task_wait_revision') is True
            and body.get('terminal_evidence') is True)
