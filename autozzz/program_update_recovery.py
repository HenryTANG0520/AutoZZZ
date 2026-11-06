"""Read durable program-update state without connecting to a selected SDK."""
import json
from pathlib import Path
import re

from .onedragon_bundle import filesystem_path
from .program_update import ProgramUpdate
from .runtime_selection import plain, read_json


_JOB = re.compile(r'\.autozzz-update-[0-9a-f]{32}')
_SCRIPTS = {'before': '恢复原版本.cmd', 'after': '继续SDK更新.cmd'}
_EXCHANGE = {'copying_user_data', 'moving_old', 'old_moved', 'moving_new', 'installed'}


def _file(path):
    try:
        return filesystem_path(plain(path)).is_file()
    except (OSError, ValueError):
        return False


def _saved_script(path, choice):
    from .program_update_handoff import RECOVERY_COMMAND

    if not _file(path):
        return False
    expected = RECOVERY_COMMAND.format(' --after' if choice == 'after' else '').encode('ascii')
    with filesystem_path(path).open('rb') as stream:
        actual = stream.read(4096)
    return actual in (expected, expected.replace(b'\r\n', b'\r\r\n'))


def _result(job, name):
    path = job/name
    if not _file(path):
        return None
    try:
        record = read_json(path, 64*1024)
        return {'path': str(path), 'state': record.get('state'), 'error': record.get('error')}
    except (AttributeError, OSError, ValueError):
        return {'path': str(path), 'state': 'unreadable', 'error': None}


def _remaining_gates(tx):
    from .program_sdk_commit import ProgramSdkCommit, PROFILES, _read

    commit = ProgramSdkCommit(tx)
    remaining = []
    for profile in PROFILES:
        path = commit._gate(profile)
        raw = _read(path, 256*1024)
        if raw is None:
            continue
        if raw == commit._gate_bytes(profile):
            remaining.append(profile)
        elif json.loads(raw).get('job') == tx.job.name:
            raise ValueError('当前事务的区服门槛与联合计划不符。')
        # An earlier terminal job can share profiles with a later update.
    return remaining


def _stage(tx):
    data = tx.data
    state, phase = data['state'], data.get('sdk_phase')
    if phase == 'restoring' and state != 'completed':
        return 'restoring', ('before',), []
    if state == 'rolled_back' and phase == 'arming':
        return 'restoring', ('before',), []
    if state in ('completed', 'rolled_back'):
        if 'sdk_commit' in data:
            if (state, phase) not in (('completed', 'committed'), ('rolled_back', 'restored')):
                return 'needs_review', (), []
            try:
                remaining = _remaining_gates(tx)
            except (AttributeError, KeyError, OSError, TypeError, ValueError):
                return 'needs_review', (), []
            if remaining:
                return 'finishing', ('after' if state == 'completed' else 'before',), remaining
        return state, (), []
    if state == 'needs_recovery':
        return 'needs_recovery', ('before',), []
    if state == 'installed' and phase == 'armed':
        return 'publishing', ('before', 'after'), []
    if state in _EXCHANGE:
        return 'exchanging', ('before',), []
    if state == 'prepared':
        if _file(tx.job/'helper-cancel') and phase is None:
            return 'cancelled', (), []
        if phase in ('arming', 'armed'):
            return 'arming', ('before', 'after'), []
        if 'sdk_commit' in data:
            return 'needs_review', (), []
        if 'sdk_transition' in data:
            return 'preparing_sdk', ('before', 'after'), []
        return 'handoff', ('before',), []
    return 'needs_review', (), []


def inspect_transaction(job, *, target=None):
    job = plain(job)
    if not _JOB.fullmatch(job.name):
        raise ValueError('请选择原程序更新事务目录。')
    journal = filesystem_path(plain(job/'transaction.json'))
    if not journal.is_file() or journal.stat().st_size > 64*1024**2:
        raise ValueError('程序更新事务清单缺失或过大。')
    tx = ProgramUpdate(job)
    if target is not None and tx.target != Path(target).resolve():
        raise ValueError('程序更新事务属于其他安装目录。')
    created, old_version, new_version = (tx.data[key] for key in ('created','old_version','new_version'))
    prepared = tx.data.get('sdk_prepared', {})
    if not all(type(value) is str and value for value in (created, old_version, new_version)) or type(prepared) is not dict:
        raise ValueError('程序更新事务的阶段资料无效。')
    state = tx.data['state']
    if (state == 'prepared' and 'sdk_transition' not in tx.data
            and not _file(job/'helper-start.json')):
        return None  # An imported package may never have been approved for handoff.

    stage, choices, remaining = _stage(tx)
    actions = {}
    if choices and _file(job/'helper/start.py') and _file(job/'helper/python/python.exe'):
        for choice in choices:
            script = job/_SCRIPTS[choice]
            if _saved_script(script, choice):
                actions[choice] = str(script)
    return dict(transaction=str(job), target=str(tx.target), created=created,
                old_version=old_version, new_version=new_version,
                state=state, sdk_phase=tx.data.get('sdk_phase'), stage=stage,
                prepared_profiles=sorted(prepared), remaining_gates=remaining,
                actions=actions, helper_result=_result(job, 'helper-result.json'),
                recovery_result=_result(job, 'recovery-result.json'),
                helper_log=str(job/'helper.log') if _file(job/'helper.log') else None)


def discover_transactions(target):
    target = Path(target).resolve()
    found = []
    for job in target.parent.glob('.autozzz-update-*'):
        try:
            row = inspect_transaction(job, target=target)
        except (KeyError, OSError, TypeError, ValueError):
            continue  # Other installations and invalid entries stay out of this list.
        if row is not None:
            found.append(row)
    return sorted(found, key=lambda row: row['created'], reverse=True)
