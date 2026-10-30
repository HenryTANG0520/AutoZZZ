"""实际 SDK 定时核对与指定账号应用运行；游戏、设备和识别均用隔离夹具。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.onedragon_runtime import RuntimePaths
from autozzz.onedragon_rotation_session import RotationSession
from autozzz.schedule_runtime import execute
from autozzz.schedules import Schedule
from onedragon_rotation_flow_smoke import FixtureClient,close


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args()
    data=args.data.resolve()
    if data.exists():raise ValueError('请使用新的隔离验证目录。')
    project=Path(__file__).resolve().parents[1];defaults=RuntimePaths.discover(project)
    paths=RuntimePaths(defaults.source,defaults.python,data);paths.prepare()
    (data/'.autozzz-rotation-flow-test').write_text('offline sdk only')
    setup=FixtureClient(paths);checks=[]
    def rpc(method,**params):return setup.call(method,timeout=30,**params)
    def action(operation,**params):
        current=rpc('account_list');return rpc('account_action',action=operation,revision=current['revision'],**params)
    try:
        setup.start()
        action('create',name='参与默认轮换的账号2');action('create',name='指定但未参与轮换的账号3')
        for idx in (1,2,3):
            game=data/f'clients/{3 if idx==3 else 1}/ZenlessZoneZero.exe';game.parent.mkdir(parents=True,exist_ok=True);game.write_bytes(b'fixture; not executable')
            account=rpc('account_describe',idx=idx,server='cn')
            rpc('account_save',idx=idx,server='cn',revision=account['revision'],values=dict(game_path=str(game),game_region='cn',game_language='cn',account=f'private-fixture-account-{idx}',password=f'private-fixture-password-{idx}'))
            action('activate',idx=idx);action('options',idx=idx,enabled=idx==2,force_login=False)
            group=rpc('describe_configuration',app_id='_group');rows=next(field['value'] for field in group['fields'] if field['name']=='app_list')
            for row in rows:row['enabled']=row['app_id']=='daily_signin'
            rpc('save_configuration',app_id='_group',revision=group['revision'],values=dict(app_list=rows))
            charge=rpc('describe_configuration',app_id='charge_plan')
            rpc('save_configuration',app_id='charge_plan',revision=charge['revision'],values=dict(loop=False,restore_charge='不使用',plan_list=[dict(category_name='实战模拟室',mission_type_name='代理人晋升',mission_name='异常演练',plan_times=2,run_times=0,card_num='1',predefined_team_idx=0)]))
        action('activate',idx=1)
        first=rpc('schedule_review',server='cn',instance_idx=3,app_id='email')
        assert first['configuration_valid'] and not first['uses_charge'],first
        assert [row['idx'] for row in first['instances']]==[3]
        assert [row['id'] for row in first['instances'][0]['applications']]==['email']
        assert first['selection']==dict(instance_idx=3,app_id='email')
        assert 'private-fixture' not in json.dumps(first)
        existing={path:path.read_bytes() for path in (data/'config').rglob('*.yml')}
        (data/'autozzz/fixture-notify-before.json').write_text(json.dumps({path.relative_to(data).as_posix():raw.decode('utf-8') for path,raw in existing.items() if path.name=='notify.yml'},ensure_ascii=False,indent=2),encoding='utf-8')
        assert rpc('schedule_review',server='cn',instance_idx=3,app_id='email')['intent']==first['intent']
        assert all(path.read_bytes()==raw for path,raw in existing.items())
        assert rpc('account_list')['current']==1
        state=rpc('execution_status');assert not state['ready'] and not state['busy']
        checks.append('actual_sdk_review_selects_disabled_instance_and_independent_application_without_credentials_or_game_initialization')
    finally:close(setup)

    reports=[]
    for selected in (3,1):
        selected_review=first
        if selected==1:
            setup=FixtureClient(paths)
            try:
                setup.start();selected_review=rpc('schedule_review',server='cn',instance_idx=selected,app_id='email')
            finally:close(setup)
        logs=[];sessions=[]
        def emit(event):
            if event['type']=='log':logs.append(event['message'])
        def factory(_paths,callback,report_root):
            session=RotationSession(paths,callback,report_root=report_root,client_factory=FixtureClient,read_key=lambda _:0)
            sessions.append(session);return session
        try:
            with patch('autozzz.onedragon_rotation_session.RotationSession',side_effect=factory):
                terminal=execute(Schedule(mode='native',instance=selected,app_id='email',timeout=30),selected_review,threading.Event(),emit,data/'reports')
            assert terminal['state']=='success',terminal
            session=sessions[0];assert [row['idx'] for row in session.plan['instances']]==[selected]
            report=session.report;assert [row['idx'] for row in report['accounts']]==[selected]
            starts=[stage for stage in report['stages'] if stage['method']=='start_application']
            assert len(starts)==1 and starts[0]['execution']['prepared_app']=='email'
            if selected==1:assert any(stage['method']=='start_client_close' for stage in report['stages'])
            reports.append(str(session.report_dir/'report.json'))
        finally:
            for session in sessions:
                session.close();session.worker.join(15);assert not session.worker.is_alive()
            (data/f'parent-{selected}.log').write_text('\n'.join(logs),encoding='utf-8')
    state=json.loads((data/'autozzz/fixture-game.json').read_text());assert state['done']==[3,1],state
    checks.append('actual_rotation_coordinator_login_client_handoff_and_single_application_factory_use_selected_instances_in_order')
    setup=FixtureClient(paths)
    try:
        setup.start();after=rpc('schedule_review',server='cn',instance_idx=3,app_id='email')
        before_entries=json.loads((data/'autozzz/fixture-intent-0.json').read_text())
        after_entries=json.loads((data/'autozzz/fixture-intent-3.json').read_text())
        changes=[key for key in set(before_entries)|set(after_entries) if before_entries.get(key)!=after_entries.get(key)]
        assert after['intent']==first['intent'],changes
        assert rpc('account_list')['current']==1
        action('activate',idx=3);charge=rpc('describe_configuration',app_id='charge_plan')
        rpc('save_configuration',app_id='charge_plan',revision=charge['revision'],values=dict(restore_charge='使用以太电池'))
        changed=rpc('schedule_review',server='cn',instance_idx=3,app_id='email')
        assert changed['intent']!=first['intent']
        plan=rpc('rotation_preview',server='cn',instance_idx=3,app_id='email')
        from autozzz.onedragon_runtime import RemoteError
        try:rpc('rotation_snapshot',plan_id=plan['plan_id'],expected_intent=first['intent'])
        except RemoteError as exc:assert '变化'in str(exc)
        else:raise AssertionError('changed configuration accepted for scheduled handoff')
        assert not rpc('execution_status')['ready']
        checks.append('native_progress_and_current_marker_preserve_intent_but_changed_restore_permission_changes_it')
    finally:close(setup)
    result=dict(checks=checks,actual_sdk=True,actual_worker_handoffs=True,actual_original_login_operations=True,
                actual_scheduled_execution_adapter=True,
                application_factory_is_fixture=True,models_devices_and_game_are_fixtures=True,reports=reports,
                game_accessed=False,game_input_sent=False,energy_spent=0)
    (data/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
