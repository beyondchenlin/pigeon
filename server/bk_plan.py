"""Validation for BK fleet plans, without issuing tasks or calling a model.

The caller supplies only devices and groups visible to the authenticated user.
Natural-language interpretation is untrusted until it passes this boundary.
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import hashlib
import json
import uuid
from server.bk_execution import ContractError, supervision_timeout, validate_execution


class PlanError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def validate_plan(candidate, devices, groups):
    """Validate model output into an immutable, non-executable plan snapshot."""
    if (not isinstance(candidate, dict) or set(candidate) not in
            ({'goal', 'target', 'schedule'}, {'goal', 'target', 'schedule', 'execution'})):
        raise PlanError('plan_invalid')
    goal = candidate['goal']
    if not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 4000:
        raise PlanError('goal_invalid')
    target = resolve_target(candidate['target'], devices, groups)
    schedule = parse_schedule(candidate['schedule'])
    group_id = None
    if candidate['target']['kind'] == 'group':
        group_id = next(group['id'] for group in groups if group['name'] == candidate['target']['name'].strip())
    result = {'goal': goal.strip(), 'target': dict(candidate['target']), 'devices': target,
              'group_id': group_id, 'schedule': schedule, 'membership': 'snapshot', 'missed': 'skip'}
    if 'execution' in candidate:
        try:
            result['execution'] = validate_execution(candidate['execution'])
            supervision_timeout(result['execution'])
        except ContractError:
            raise PlanError('execution_invalid') from None
    return result


def plan_fingerprint(plan):
    """Stable content hash used to reject a reused submission with new intent."""
    encoded = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def execution_id(job_id, domain, scheduled_at):
    """One stable identity per parent job, device, and scheduled occurrence."""
    if (type(job_id) is not int or job_id <= 0 or not isinstance(domain, str) or not domain
            or not isinstance(scheduled_at, str) or not scheduled_at):
        raise PlanError('execution_identity_invalid')
    encoded = json.dumps([job_id, domain, scheduled_at], ensure_ascii=True, separators=(',', ':'))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'pigeon-bk-scheduled:' + encoded))


def resolve_target(target, devices, groups):
    """Resolve an exact, user-visible name or domain to a frozen device set."""
    if not isinstance(target, dict) or set(target) != {'kind', 'name'}:
        raise PlanError('target_invalid')
    kind, name = target['kind'], target['name']
    if kind not in ('device', 'group') or not isinstance(name, str) or not name.strip():
        raise PlanError('target_invalid')
    name = name.strip()
    if kind == 'device':
        matches = [d for d in devices if name in (d.get('domain'), d.get('name'))]
        if len(matches) != 1:
            raise PlanError('target_ambiguous' if matches else 'target_not_found')
        chosen = matches
    else:
        matches = [g for g in groups if g.get('name') == name]
        if len(matches) != 1:
            raise PlanError('target_ambiguous' if matches else 'target_not_found')
        group = matches[0]
        chosen = [d for d in devices if d.get('group_id') == group.get('id')]
        if not chosen:
            raise PlanError('group_empty')
    domains = [d.get('domain') for d in chosen]
    if any(not isinstance(x, str) or not x for x in domains) or len(set(domains)) != len(domains):
        raise PlanError('device_identity_invalid')
    return sorted(domains)


def parse_schedule(value):
    """Validate an explicit one-off or daily schedule and timezone."""
    if not isinstance(value, dict) or value.get('kind') not in ('now', 'once', 'daily'):
        raise PlanError('schedule_invalid')
    kind = value['kind']
    allowed = {'kind'} if kind == 'now' else {'kind', 'at', 'timezone'}
    if set(value) != allowed:
        raise PlanError('schedule_invalid')
    if kind == 'now':
        return {'kind': kind}
    try:
        zone_name = value['timezone']
        if not isinstance(zone_name, str) or not zone_name:
            raise ValueError()
        ZoneInfo(zone_name)
        at = value['at']
        if not isinstance(at, str):
            raise ValueError()
        if kind == 'once':
            parsed = datetime.strptime(at, '%Y-%m-%dT%H:%M')
            if parsed.strftime('%Y-%m-%dT%H:%M') != at:
                raise ValueError()
            local_instant(parsed, zone_name)
        else:
            parsed = datetime.strptime(at, '%H:%M')
            if parsed.strftime('%H:%M') != at:
                raise ValueError()
    except PlanError:
        raise
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise PlanError('schedule_invalid') from None
    return {'kind': kind, 'at': at, 'timezone': zone_name}


def local_instant(local, zone_name):
    """Reject ambiguous or nonexistent local times instead of guessing a fold."""
    zone = ZoneInfo(zone_name)
    candidates = set()
    for fold in (0, 1):
        aware = local.replace(tzinfo=zone, fold=fold)
        utc = aware.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == local:
            candidates.add(utc)
    if len(candidates) != 1:
        raise PlanError('schedule_ambiguous_time')
    return candidates.pop()


def next_daily(schedule, after):
    """Return the next valid daily slot; DST gaps/folds require review."""
    plan = parse_schedule(schedule)
    if plan['kind'] != 'daily' or after.tzinfo is None:
        raise PlanError('schedule_invalid')
    zone = ZoneInfo(plan['timezone'])
    local_after = after.astimezone(zone)
    hour, minute = map(int, plan['at'].split(':'))
    day = local_after.date()
    for _ in range(2):
        instant = local_instant(datetime.combine(day, time(hour, minute)), plan['timezone'])
        if instant > after.astimezone(timezone.utc):
            return instant
        day += timedelta(days=1)
    raise PlanError('schedule_invalid')
