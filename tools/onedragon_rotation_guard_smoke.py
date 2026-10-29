"""实际 SDK 的跨账号资料交接与准备；不启动游戏、不产生真实键鼠。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import platform
import shutil
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args()
    data=args.data.resolve()
    if data.exists():raise SystemExit('请使用新的隔离验证目录。')
    from autozzz.onedragon_runtime import RuntimePaths
    from autozzz.task_lease import JoinedRunLease
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);RuntimePaths(defaults.source,defaults.python,data).prepare()
    cached=Path(__file__).resolve().parents[1]/'runs/onedragon-20261028/initialize-cn/assets/models'
    if cached.is_dir():shutil.copytree(cached,data/'assets/models',dirs_exist_ok=True)
    platform.uname();sys.path.insert(0,str(defaults.source/'src'))
    from autozzz.onedragon_service import OneDragonService
    from autozzz.rotation_snapshot import snapshot
    from zzz_od.application.charge_plan.charge_plan_config import ChargePlanItem
    lease=JoinedRunLease.from_environment()
    if lease is None:raise RuntimeError('本工具需要父进程交接任务锁。')
    services=[];checks=[]
    def create():
        service=OneDragonService(defaults.source,data,run_lease=lease);services.append(service);return service
    def forbidden(*_,**__):raise AssertionError('轮换资料验证禁止实际外部操作')
    with ExitStack() as blocks:
        for name in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo',
                     'winreg.SetValueEx','winreg.DeleteValue','autozzz.windows.find_game','autozzz.windows.user32.PostMessageW',
                     'pynput.keyboard.Controller.press','pynput.mouse.Controller.press'):
            blocks.enter_context(patch(name,side_effect=forbidden))
        blocks.enter_context(patch('autozzz.windows.user32.GetAsyncKeyState',return_value=0))
        try:
            setup=create()
            def action(operation,**params):
                current=setup.account_list();return setup.account_action(action=operation,revision=current['revision'],**params)
            action('create',name='资料交接账号2')
            game=data/'client/ZenlessZoneZero.exe';game.parent.mkdir();game.write_bytes(b'fixture only')
            for idx in (1,2):
                description=setup.account_describe(idx,'cn')
                setup.account_save(idx,'cn',description['revision'],dict(game_path=str(game),game_region='cn',game_language='cn',account=f'fixture-account-{idx}',password=f'fixture-password-{idx}'))
                action('options',idx=idx,enabled=True,force_login=False);action('activate',idx=idx)
                group=setup.describe_configuration('_group');rows=next(field['value'] for field in group['fields'] if field['name']=='app_list')
                for row in rows:row['enabled']=row['app_id']=='email'
                setup.save_configuration('_group',dict(app_list=rows),group['revision'])
            action('activate',idx=1)
            plan=setup.rotation_preview('cn');assert plan['configuration_valid'],plan['issues']
            manifest=setup.rotation_snapshot(plan['plan_id']);setup.close();assert snapshot(data)==manifest
            checks.append('actual_preview_and_snapshot_cover_all_participating_accounts_without_credential_values')

            first=create();first.rotation_bind(manifest);first.rotation_select(2,expected_current=1)
            assert first.ctx.current_instance_idx==2
            counter=first.ctx.run_context.get_config('charge_plan',2,'one_dragon')
            counter.plan_list=[ChargePlanItem(plan_times=2)];counter.save();counter.add_plan_run_times(counter.plan_list[0])
            assert counter.plan_list[0].run_times==1
            assert first.rotation_checkpoint()!=manifest
            checks.append('actual_yaml_save_hook_records_account_activation_and_charge_plan_progress_without_loosening_other_files')
            first.initialize_execution(server='cn',game_path=str(game),region='cn',instance_idx=2);first._execution.worker.join(30)
            state=first.execution_status();assert not state['busy'] and state['state']=='ready',state
            first.prepare_application('one_dragon');first._execution.worker.join(10)
            state=first.execution_status();assert not state['busy'] and state['state']=='prepared',state
            assert [item['id'] for item in state['prepared_applications']]==['email']
            assert state['window_handle'] is None and not first.ctx._btn_listener_started
            manifest=first.rotation_release();assert snapshot(data)==manifest and first._ctx is None
            checks.append('actual_ocr_and_account_group_prepare_complete_with_snapshot_guard_then_release_final_cleanup_version_without_binding_window')

            second=create();second.rotation_bind(manifest);second.rotation_select(1,expected_current=2)
            loaded=second.ctx.run_context.get_config('charge_plan',2,'one_dragon')
            assert loaded.plan_list[0].run_times==1
            checks.append('fresh_sdk_context_accepts_only_transferred_version_and_reads_saved_previous_account_progress')
            target=Path(second.ctx.game_account_config._get_write_path());target.write_text(target.read_text(encoding='utf-8')+'\n# external change\n',encoding='utf-8')
            original=target.read_bytes()
            try:second.rotation_checkpoint();raise AssertionError('external edit accepted')
            except ValueError as exc:assert '外部修改' in str(exc)
            try:second.ctx.game_account_config.save();raise AssertionError('external edit overwritten')
            except ValueError:pass
            assert target.read_bytes()==original
            try:second.rotation_release();raise AssertionError('failed snapshot transferred')
            except ValueError:pass
            assert second._rotation_sealed and second._rotation_guard is None
            checks.append('external_edit_latches_failure_preserves_bytes_and_prevents_final_transfer')
        finally:
            for service in reversed(services):service.close()
            lease.close()
    result=dict(checks=checks,count=len(checks),game_accessed=False,game_input_sent=False,network_blocked=True)
    (data/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
