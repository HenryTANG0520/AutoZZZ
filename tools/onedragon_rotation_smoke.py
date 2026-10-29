"""实际多账号顺序和逐账号依赖预检，不打开游戏或执行登录。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import platform
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args()
    data=args.data.resolve();marker=data/'.autozzz-rotation-test'
    if data.exists() and any(data.iterdir()) and not marker.is_file():raise SystemExit('请使用空目录或本工具拥有的目录。')
    data.mkdir(parents=True,exist_ok=True);marker.write_text('isolated rotation preview test',encoding='utf-8')
    from autozzz.onedragon_runtime import RuntimePaths
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);RuntimePaths(defaults.source,defaults.python,data).prepare()
    alias=data/'.fixture-junction-target';alias.mkdir();link=data/'config/04'
    assert alias.resolve().is_relative_to(data) and link.absolute().is_relative_to(data)
    subprocess.run(['cmd.exe','/c','mklink','/J',str(link),str(alias)],check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
    platform.uname();sys.path.insert(0,str(defaults.source/'src'))
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_rotation import rotation_revision
    from autozzz.onedragon_requirements import ApplicationRequirements
    service=OneDragonService(defaults.source,data);checks=[]
    def forbidden(*_,**__):raise AssertionError('轮换预检禁止实际外部操作')
    def action(operation,**params):
        current=service.account_list()
        return service.account_action(action=operation,revision=current['revision'],**params)
    def login(idx,**values):
        description=service.account_describe(idx,'cn')
        return service.account_save(idx,'cn',description['revision'],values)
    def group(idx,enabled):
        action('activate',idx=idx)
        description=service.describe_configuration('_group')
        rows=next(field['value'] for field in description['fields'] if field['name']=='app_list')
        for row in rows:row['enabled']=row['app_id'] in enabled
        assert set(enabled)<={row['app_id'] for row in rows},(enabled,[row['app_id'] for row in rows])
        service.save_configuration('_group',dict(app_list=rows),description['revision'])
    with ExitStack() as blocks:
        for name in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo','winreg.SetValueEx','winreg.DeleteValue','autozzz.windows.find_game','pynput.keyboard.Controller.press','pynput.mouse.Controller.press'):
            blocks.enter_context(patch(name,side_effect=forbidden))
        try:
            service.ctx
            assert len(service.account_list()['instances'])==1
            action('create',name='测试账号2');action('create',name='测试账号3')
            common=data/'clients/common/ZenlessZoneZero.exe';other=data/'clients/other/ZenlessZoneZero.exe'
            for path in (common,other):path.parent.mkdir(parents=True);path.write_bytes(b'not an executable; test fixture')
            for idx in (1,2,3):
                login(idx,game_path=str(other if idx==3 else common),game_region='cn',game_language='cn',
                      account=f'fixture-private-account-{idx}',password=f'fixture-private-password-{idx}')
                action('options',idx=idx,enabled=True,force_login=False)
            for idx,apps in ((1,['daily_signin']),(2,['email']),(3,['charge_plan'])):group(idx,apps)
            charge=service.describe_configuration('charge_plan')
            service.save_configuration('charge_plan',dict(loop=False,restore_charge='不使用',plan_list=[dict(category_name='实战模拟室',mission_type_name='代理人晋升',mission_name='异常演练',plan_times=1,run_times=0,card_num='1',predefined_team_idx=0)]),charge['revision'])
            action('activate',idx=2)
            plan=service.rotation_preview('cn')
            assert plan['configuration_valid'],plan['issues']
            assert [row['idx'] for row in plan['instances']]==[2,3,1]
            assert [row['applications'][0]['id'] for row in plan['instances']]==['email','charge_plan','daily_signin']
            assert [row['uses_charge'] for row in plan['instances']]==[False,True,False] and plan['uses_charge']
            assert [(row['from_idx'],row['to_idx'],row['restart_client']) for row in plan['transitions']]==[(2,3,True),(3,1,True),(1,2,False)]
            assert plan['transitions'][-1]['returning'] and plan['return_idx']==2
            assert service.ctx.current_instance_idx==service.account_list()['current']==2
            assert service.ctx.controller is None and service.ctx.ocr._model is None and not service.ctx._btn_listener_started
            assert 'fixture-private' not in json.dumps(plan)
            assert service.rotation_validate(plan['plan_id'])==plan
            (data/'preview.json').write_text(json.dumps(plan,ensure_ascii=False),encoding='utf-8')
            checks.append('actual_instance_start_order_per_account_app_groups_charge_forecast_and_client_transitions_without_input_or_credential_disclosure')

            snapshot={path:path.read_bytes() for path in (data/'config').rglob('*.yml')}
            second=service.rotation_preview('cn');assert second['configuration_valid']
            assert all(path.read_bytes()==raw for path,raw in snapshot.items())
            try:service.rotation_validate(plan['plan_id']);raise AssertionError('old preview handle accepted')
            except ValueError as exc:assert '编号' in str(exc)
            checks.append('repeated_preview_preserves_all_existing_configs_and_invalidates_previous_plan_handle')

            policy=next(row for row in plan['instances'] if row['idx']==3)['requirements']['policies'][0]
            hidden=[]
            try:
                for path in (data/'config'/policy['directory']).glob(policy['name']+'.*'):
                    moved=path.with_name('fixture-hidden-'+path.name)
                    assert path.resolve().is_relative_to(data) and moved.resolve().is_relative_to(data)
                    path.replace(moved);hidden.append((path,moved))
                assert hidden
                missing=service.rotation_preview('cn')
                assert not missing['configuration_valid'] and any(item['instance_idx']==3 and '策略不存在' in item['message'] for item in missing['issues'])
            finally:
                for path,moved in hidden:
                    assert path.resolve().is_relative_to(data) and moved.resolve().is_relative_to(data)
                    moved.replace(path)
            second=service.rotation_preview('cn')
            checks.append('missing_team_policy_is_rejected_by_same_actual_file_lookup_used_before_model_preparation')

            digest=rotation_revision(data);action('activate',idx=1)
            assert rotation_revision(data)==digest
            try:service.rotation_validate(second['plan_id']);raise AssertionError('external active account change ignored')
            except ValueError as exc:assert '当前账号' in str(exc)
            action('activate',idx=2);second=service.rotation_preview('cn')
            action('options',idx=3,enabled=True,force_login=True)
            try:service.rotation_validate(second['plan_id']);raise AssertionError('login mode change ignored')
            except ValueError as exc:assert '变化' in str(exc)
            checks.append('scope_ignores_only_active_marker_but_start_guard_rejects_external_selection_and_force_login_changes')

            login(3,game_region='cn_b',bilibili_account_name='fixture-private-bilibili',account='',clear_password=True)
            third=service.rotation_preview('cn');assert third['configuration_valid'],third['issues']
            assert next(row for row in third['instances'] if row['idx']==3)['login_configured']
            assert 'fixture-private' not in json.dumps(third)
            checks.append('actual_bilibili_login_readiness_uses_saved_account_selection_without_returning_login_identity')

            login(3,bilibili_account_name='')
            invalid=service.rotation_preview('cn');assert not invalid['configuration_valid']
            assert any(row['instance_idx']==3 and '登录资料' in row['message'] for row in invalid['issues'])
            try:service.rotation_validate(invalid['plan_id']);raise AssertionError('missing login info accepted')
            except ValueError as exc:assert '预检' in str(exc)
            login(3,bilibili_account_name='fixture-private-bilibili')
            login(1,password='x')
            short=service.rotation_preview('cn');assert not short['configuration_valid'] and any('3 个字符' in row['message'] for row in short['issues'])
            login(1,password='fixture-private-password-1')
            checks.append('missing_required_login_information_is_reported_for_its_instance_and_blocks_plan_validation')

            action('activate',idx=1);action('options',idx=1,enabled=False,force_login=False)
            excluded=service.rotation_preview('cn');assert [row['idx'] for row in excluded['instances']]==[2,3]
            assert excluded['transitions'][0]['from_idx']==1 and excluded['transitions'][0]['to_idx']==2
            assert excluded['start_idx']==excluded['return_idx']==2 and service.account_list()['current']==1
            checks.append('disabled_current_account_uses_first_participating_account_and_explicit_initial_login_transition')

            action('options',idx=2,enabled=False,force_login=False);action('options',idx=3,enabled=False,force_login=False)
            single=service.rotation_preview('cn');assert [row['idx'] for row in single['instances']]==[1]
            assert not single['transitions'] and not single['instances'][0]['login_required']
            assert single['configuration_valid']
            checks.append('actual_no_enabled_instance_fallback_runs_current_only_without_extra_login_or_client_transition')

            group(1,[]);empty=service.rotation_preview('cn')
            assert not empty['configuration_valid'] and any('没有启用' in row['message'] for row in empty['issues'])
            group(1,['daily_signin'])
            secret='fixture-private-password-1'
            with patch.object(ApplicationRequirements,'collect',side_effect=ValueError(secret)):
                failed=service.rotation_preview('cn')
            assert not failed['configuration_valid'] and secret not in json.dumps(failed)
            assert service.ctx.current_instance_idx==service.account_list()['current']==1
            checks.append('empty_groups_and_dependency_failures_keep_current_instance_and_redact_credentials_in_diagnostics')

            accepted=service.rotation_preview('cn');assert accepted['configuration_valid']
            versions=data/'config/model_files.yml';versions.write_text('version: fixture-new\n',encoding='utf-8')
            try:service.rotation_validate(accepted['plan_id']);raise AssertionError('model replacement ignored')
            except ValueError as exc:assert '变化' in str(exc)
            accepted=service.rotation_preview('cn')
            pending=data/'config/autozzz-model-transaction.json';pending.write_text('{}',encoding='utf-8')
            try:
                for call in (lambda:service.rotation_preview('cn'),lambda:service.rotation_validate(accepted['plan_id'])):
                    try:call();raise AssertionError('pending model installation ignored')
                    except ValueError as exc:assert '未完成' in str(exc)
            finally:pending.unlink()
            checks.append('model_preparation_revision_and_interrupted_installs_block_stale_rotation_plans')

            with patch.object(ApplicationRequirements,'collect',side_effect=SystemExit('fixture interrupted preview')):
                try:service.rotation_preview('cn');raise AssertionError('preview interruption not propagated')
                except SystemExit:pass
            assert service._rotation.plan is None and service.ctx.current_instance_idx==1 and service.account_list()['current']==1
            checks.append('interrupted_preview_restores_instance_and_discards_previous_plan_without_sending_input')

            accepted=service.rotation_preview('cn')
            from one_dragon.base.config.one_dragon_config import OneDragonConfig
            registry=OneDragonConfig();saved=copy.deepcopy(registry.dict_instance_list)
            changed=copy.deepcopy(saved);changed[1]['active']=True;registry.dict_instance_list=changed
            try:
                try:service.rotation_validate(accepted['plan_id']);raise AssertionError('ambiguous active account accepted')
                except ValueError as exc:assert '一个当前账号' in str(exc)
            finally:registry.dict_instance_list=saved
            checks.append('ambiguous_current_account_is_rejected_even_though_only_active_flags_changed')

            action('create',name='重定向测试账号')
            try:
                try:service.rotation_preview('cn');raise AssertionError('redirected instance directory accepted')
                except ValueError as exc:assert '重定向' in str(exc)
                assert not any(alias.iterdir())
            finally:
                registry=OneDragonConfig();registry.dict_instance_list=[row for row in registry.dict_instance_list if row['idx']!=4]
            checks.append('actual_windows_junction_is_rejected_before_upstream_account_config_can_create_files_in_target')
        finally:service.close()
    result=dict(checks=checks,count=len(checks),game_accessed=False,network_blocked=True,rotation_executed=False)
    (data/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
