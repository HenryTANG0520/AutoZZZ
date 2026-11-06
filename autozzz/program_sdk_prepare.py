"""Prepare stable SDK snapshots with the verified staged release before swapping it in."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryFile
from time import monotonic, sleep
from types import SimpleNamespace

from .onedragon_bundle import digest
from .program_package import verify_package
from .program_sdk_binding import PROFILES, stage_sdk_receipt, verify_sdk_transition
from .program_sdk_commit import ProgramSdkCommit
from .runtime_selection import identifier
from .sdk_stable_migration import sha256_token
from .task_lease import JoinedRunLease, LEASE_ENV


_BOOTSTRAP = (
    'import sys; sys.path.insert(0, sys.argv.pop(1)); '
    'from autozzz.program_sdk_prepare import worker; worker()'
)


def _snapshot(proof, revision):
    identifier(proof['data_id'])
    sha256_token(proof['hash'])
    if (proof['hello']['revision'] != revision or proof['hello']['application_count'] < 30
            or proof['control']['revision'] != revision or proof['control']['task_lease'] is not True
            or proof['control']['input_enabled'] is not False):
        raise ValueError('候选 SDK 区服快照缺少有效的工作进程握手。')
    return proof


def _prepare_profile(tx, profile, grant):
    python = tx.candidate/'onedragon/python/python.exe'
    bridge = tx.candidate/'onedragon/bridge'
    environment = dict(os.environ, **{LEASE_ENV: grant})
    command = [str(python), '-I', '-B', '-X', 'utf8', '-c', _BOOTSTRAP,
               str(bridge), str(tx.job), profile, digest(tx.journal)]
    with TemporaryFile() as output:
        result = subprocess.run(command, cwd=bridge, env=environment, stdin=subprocess.DEVNULL,
                                stdout=output, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError(f'候选 SDK 区服 {profile} 准备失败，退出码 {result.returncode}。')
        output.seek(0)
        raw = output.read(64*1024+1)
    if len(raw) > 64*1024:
        raise ValueError('候选 SDK 区服快照回执过大。')
    return json.loads(raw)


def advance_sdk_update(tx, grant=None):
    """The caller holds the total task lock until both release gates are resolved."""
    from .program_update import verify_preflight

    if 'sdk_commit' in tx.data:
        ProgramSdkCommit(tx).after()
        return
    if tx.data['state'] != 'prepared' or grant is None:
        raise ValueError('更新事务缺少只准备快照的工作进程授权。')
    verify_preflight(tx)
    binding = verify_sdk_transition(tx)
    if verify_package(tx.candidate, strict=True)['runtime_revision'] != binding['new']['revision']:
        raise ValueError('待验证的候选 SDK 发行版本已变化。')
    snapshots = dict(tx.data.get('sdk_prepared', {}))
    for profile in PROFILES:
        if binding['profiles'][profile]['kind'] != 'stable':
            continue
        revision = binding['new']['revision']
        if profile in snapshots:
            _snapshot(snapshots[profile], revision)
            continue
        snapshots[profile] = _snapshot(_prepare_profile(tx, profile, grant), revision)
        tx.state('prepared', sdk_prepared=snapshots.copy())
    ProgramSdkCommit.prepare(tx, snapshots).after()


def worker():
    from .onedragon_runtime import OneDragonClient, RequestPending, RuntimePaths
    from .program_update import ProgramUpdate

    job, profile, expected = sys.argv[1:]
    tx = ProgramUpdate(job)
    bridge = tx.candidate/'onedragon/bridge'
    if (tx.data['state'] != 'prepared' or 'sdk_commit' in tx.data or digest(tx.journal) != expected
            or bridge != Path(__file__).resolve().parent.parent
            or Path(sys.executable).resolve() != tx.candidate/'onedragon/python/python.exe'):
        raise ValueError('候选 SDK 工作进程与更新事务身份不符。')
    grant = os.environ.get(LEASE_ENV)
    lease = JoinedRunLease.from_environment(program_update=True)
    if lease is None:
        raise RuntimeError('候选 SDK 缺少更新助手授予的任务锁。')

    def export():
        lease.check()
        return grant

    client = None
    try:
        binding = verify_sdk_transition(tx)
        row = stage_sdk_receipt(tx, profile, installed=False)
        paths = RuntimePaths.discover_sdk_control(tx.candidate, profile)
        if row['kind'] != 'stable' or paths.revision != binding['new']['revision']:
            raise ValueError('区服或 SDK 发行版本不适用于准备快照。')
        client = OneDragonClient(paths, log=lambda line: print(line, file=sys.stderr),
                                 run_lease=SimpleNamespace(export=export))
        try:
            control = client.start(timeout=60)
        except RequestPending:
            control = client.finish_start(timeout=60)
        task = client.call('sdk_prepare_stable', old_revision=binding['old']['revision'],
                           old_defaults_digest=binding['receipt'], selection_revision=row['selector'],
                           source_proof={key: row[key] for key in ('marker', 'data')})
        deadline = monotonic()+900
        while task['busy']:
            lease.check()
            if monotonic() >= deadline:
                client.call('sdk_cancel')
                raise TimeoutError('候选 SDK 区服快照超过等待时限，已请求取消。')
            sleep(.25)
            task = client.call('sdk_status', job_id=task['job_id'])
        if task['state'] != 'success':
            raise RuntimeError(task['error'] or '候选 SDK 区服快照未完成。')
        proof = dict(task['result'], control={key: control[key] for key in ('revision','pid','task_lease','input_enabled')})
    finally:
        try:
            while client is not None:
                try:
                    client.close(timeout=5)
                    break
                except RequestPending:
                    continue
        finally:
            lease.close()
    print(json.dumps(_snapshot(proof, binding['new']['revision'])))
