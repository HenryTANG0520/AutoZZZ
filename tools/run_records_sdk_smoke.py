"""Exercise the actual three OneDragon run-record classes without a game or GUI."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import date, timedelta
import json
from pathlib import Path
import platform
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def rejected(call, fragment):
    try:
        call()
    except (ValueError, RuntimeError) as exc:
        assert fragment in str(exc), exc
    else:
        raise AssertionError('unexpectedly accepted ' + fragment)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    args = parser.parse_args()
    root = args.data.resolve()
    if root.exists():
        raise ValueError('Use a new fixture directory.')
    root.mkdir(parents=True)
    platform.uname()
    from autozzz.onedragon_runtime import RuntimePaths

    repo = Path(__file__).resolve().parents[1]
    defaults = RuntimePaths.discover(repo, use_selection=False)
    RuntimePaths(defaults.source, defaults.python, root).prepare()
    from autozzz.onedragon_run_records import RUN_RECORD_APPS, RunRecordStore
    from autozzz.onedragon_service import OneDragonService
    from autozzz.run_lock import RunLock, profile_lock
    from autozzz.runtime_generation import RuntimeGeneration

    def forbidden(*_, **__):
        raise AssertionError('offline record check attempted external input or communication')

    checks = []
    with ExitStack() as blocks:
        for name in ('subprocess.Popen', 'winreg.SetValueEx', 'winreg.DeleteValue', 'requests.sessions.Session.request',
                     'socket.socket.connect', 'socket.socket.connect_ex', 'socket.getaddrinfo'):
            blocks.enter_context(patch(name, side_effect=forbidden))
        service = OneDragonService(defaults.source, root)
        try:
            from one_dragon.base.operation.application_run_record import AppRunRecord
            from one_dragon.utils.yaml_utils import safe_load
            import yaml

            service.ctx
            store = RunRecordStore(service)
            registry = root / 'config/one_dragon.yml'
            idx = service.ctx.current_instance_idx
            folder = root / 'config' / f'{idx:02d}'
            account_record = folder / 'app_run_record'
            other_account = root / 'config' / f'{idx+1:02d}' / 'app_run_record/lost_void.yml'
            other_account.parent.mkdir(parents=True)
            other_account.write_bytes(b'weekly_run_times: 87\n')
            today = date(2026, 11, 5)
            old_day = (today - timedelta(days=1)).strftime('%Y%m%d')
            old_week = (today - timedelta(days=7)).strftime('%Y%m%d')
            day = today.strftime('%Y%m%d')
            with patch.object(AppRunRecord, 'get_current_dt', return_value=day):
                missing = store.describe('world_patrol')
                assert not missing['exists'] and missing['status'] == 'waiting'
                assert store.reset('world_patrol', missing['revision'], idx)['archive'] is None
                assert not (account_record/'world_patrol.yml').exists()
                checks.append('missing record read and reset create no record file')

                old_plan = folder/'world_patrol.yml'
                old_plan.write_bytes(b'daily_loop_count: 4\n')
                assert store.describe('world_patrol')['progress']['daily_plan_rounds'] == 4
                assert not (folder/'one_dragon/world_patrol.yml').exists()
                checks.append('source legacy plan read without migration or write')

                for app_id in RUN_RECORD_APPS:
                    record = account_record / f'{app_id}.yml'
                    raw = dict(dt=day, run_status=1, run_time='11-05 04:32', run_time_float=12.25, unknown_fixture={'keep': ['text']})
                    if app_id == 'world_patrol':
                        raw.update(finished=['a', 'b'], completed_rounds=2, routes_per_round=4)
                        changed = dict(finished=[], completed_rounds=0, run_status=0)
                    elif app_id == 'lost_void':
                        raw.update(daily_run_times=2, weekly_run_times=5, bounty_commission_complete=True,
                                   eval_point_complete=True, period_reward_complete=True, complete_task_force_with_up=True)
                        changed = dict(daily_run_times=0, weekly_run_times=0, run_status=0, bounty_commission_complete=False,
                                       eval_point_complete=False, period_reward_complete=False, complete_task_force_with_up=False)
                    else:
                        raw.update(daily_run_times=2, weekly_run_times=5, no_eval_point=True, period_reward_complete=True)
                        changed = dict(daily_run_times=0, weekly_run_times=0, no_eval_point=False, period_reward_complete=False)
                    record.parent.mkdir(parents=True, exist_ok=True)
                    original = b'# exact archived bytes\n' + yaml.safe_dump(raw, sort_keys=False).encode('utf-8')
                    record.write_bytes(original)
                    snapshot = store.describe(app_id)
                    assert snapshot['instance_idx'] == idx and snapshot['exists'] and snapshot['status'] == 'success'
                    if app_id == 'world_patrol':
                        assert snapshot['progress']['daily_progress'] == 2.5
                        assert snapshot['progress']['finished_routes'] == 2
                    else:
                        assert snapshot['progress']['weekly_run_times'] == 5 and snapshot['progress']['daily_run_times'] == 2
                    assert record.read_bytes() == original
                    reset = store.reset(app_id, snapshot['revision'], idx)
                    assert Path(reset['archive']).read_bytes() == original and reset['status'] == 'waiting'
                    assert safe_load(record.read_bytes()) == dict(raw, **changed)
                    assert store.describe(app_id)['revision'] == reset['revision'] != snapshot['revision']
                    rejected(lambda: store.reset(app_id, snapshot['revision'], idx), '已变化')
                    checks.append(app_id + ' source reset, exact archive, unknown data preservation and stale request rejection')

                record = account_record/'lost_void.yml'
                record.write_text(yaml.safe_dump(dict(dt=old_day, daily_run_times=4, weekly_run_times=7)), encoding='utf-8')
                old = store.describe('lost_void')
                assert old['progress']['daily_run_times'] == 0 and old['progress']['weekly_run_times'] == 7
                record.write_text(yaml.safe_dump(dict(dt=old_week, daily_run_times=4, weekly_run_times=7, period_reward_complete=True)), encoding='utf-8')
                old = store.describe('lost_void')
                assert old['progress']['weekly_run_times'] == 0 and not old['progress']['period_reward_complete']
                checks.append('read-only day and week rollovers use upstream dates')

                with RunLock():
                    rejected(lambda: store.reset('lost_void', old['revision'], idx), '另一个')
                with profile_lock(root):
                    rejected(lambda: store.reset('lost_void', old['revision'], idx), '另一个')
                rejected(lambda: store.reset('lost_void', old['revision'], idx+1), '已变化')
                rejected(lambda: store.describe('charge_plan'), '不支持')
                record_before = record.read_bytes()
                registry_before = registry.read_bytes()
                switched = safe_load(registry_before)
                switched['instance_list'][0]['active'] = False
                switched['instance_list'].append(dict(idx=idx+1, name='Other', active=True, active_in_od=True))
                registry.write_text(yaml.safe_dump(switched), encoding='utf-8')
                rejected(lambda: store.reset('lost_void', old['revision'], idx), '当前账号已变化')
                registry.write_bytes(registry_before)
                assert record.read_bytes() == record_before and other_account.read_bytes() == b'weekly_run_times: 87\n'
                checks.append('real global/profile mutexes, account identity and app allowlist protect writes')

                previous_guard = service.runtime_guard
                service.runtime_guard = RuntimeGeneration(root, '-', management_only=True)
                rejected(lambda: store.reset('lost_void', old['revision'], idx), '仅用于 SDK 管理')
                service.runtime_guard = RuntimeGeneration(root, '-')
                selection = root/'autozzz/sdk-selection.json'
                selection.parent.mkdir(parents=True, exist_ok=True)
                selection.write_bytes(b'{}')
                rejected(lambda: store.reset('lost_void', old['revision'], idx), 'SDK 已切换')
                selection.unlink()
                service.runtime_guard = previous_guard
                checks.append('stale SDK generation and maintenance-only connection cannot write')

                real_archive = store._archive
                def archive_and_race(path, data):
                    archived = real_archive(path, data)
                    record.write_bytes(b'daily_run_times: 42\n')
                    return archived
                with patch.object(store, '_archive', side_effect=archive_and_race):
                    rejected(lambda: store.reset('lost_void', old['revision'], idx), '保存期间')
                assert record.read_bytes() == b'daily_run_times: 42\n'
                assert record_before in [path.read_bytes() for path in (root/'autozzz/run-record-archives').rglob('*.yml')]
                checks.append('concurrent external edit retained and exact archive remains')

                record.write_bytes(b'- this is not a mapping\n')
                rejected(lambda: store.describe('lost_void'), '须为 YAML 映射')
                assert record.read_bytes() == b'- this is not a mapping\n'
                redirect = root/'config/fixture-redirect'
                resolve = Path.resolve
                with patch.object(Path, 'resolve', lambda self, *a, **k: other_account if self == redirect else resolve(self, *a, **k)):
                    rejected(lambda: store._read(redirect), '被重定向')
                checks.append('malformed records and redirected paths fail without writing')
            assert service.ctx.controller is None
        finally:
            service.close()
    result = dict(success=True, game_started=False, game_input_sent=False, checks=checks)
    (root/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
