import copy
from contextlib import nullcontext
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from autozzz.battle_budget import BattleBudget
from autozzz.onedragon_rotation_session import RotationSession
from autozzz.onedragon_runtime import RequestPending,RemoteError
from autozzz.rotation_snapshot import snapshot


class Broker:
    def __init__(self,root):
        self.root=root;self.current=1;self.clients=[];self.calls=[];self.identity=None;self.game_path=None
        self.hold=threading.Event();self.hold.set();self.failed_group=None;self.failed_login=None;self.unknown_exit=False;self.pending_energy=False
        self.change_resources=False;self.transient=False;self.once_error=False;self.hook=lambda *args:None
        self.app_starts=[];self.budgets=[];self.closed=[];self.keys={};self.budget_dir=root/'budgets'
        (root/'config').mkdir();(root/'config/current.yml').write_text('active: 1')
        game=root/'game/ZenlessZoneZero.exe';game.parent.mkdir();game.write_bytes(b'fixture')
        resource=lambda idx:dict(id=f'{idx}:drive_disc_dismantle:dismantle',app_id='drive_disc_dismantle',text='测试拆解',requires_confirmation=True,instance_idx=idx,instance_name=str(idx))
        rows=[dict(idx=idx,name=f'账号{idx}',game_path=str(game),region='cn',language='cn',login_required=True,
                   applications=[dict(id='charge_plan',name='刷本')],resources=[resource(idx)],uses_charge=True) for idx in (1,2)]
        self.plan=dict(server='cn',original_current=1,start_idx=1,return_idx=1,plan_id='fixture-plan',configuration_valid=True,
                       issues=[],uses_charge=True,origin=dict(idx=1,game_path=str(game),region='cn',language='cn'),instances=rows,
                       transitions=[dict(from_idx=1,to_idx=2,restart_client=False,returning=False),dict(from_idx=2,to_idx=1,restart_client=False,returning=True)])
    def factory(self,paths,log=None,**kwargs):return Client(self,paths)


class Client:
    def __init__(self,broker,paths):
        assert not broker.clients or not broker.clients[-1].alive,'new worker created before old worker exit'
        self.broker=broker;self.number=len(broker.clients);broker.clients.append(self)
        self.alive=True;self.protocol_available=True;self.answers={};self.observed=set();self.close_count=0;self.worker_exit_code=0
        self.state=dict(job_id='',state='idle',busy=False,ready=True,uses_charge=False);self.next_control=dict(paused=False,f9_down=False);self.guard=None
    def start(self,timeout=30):return dict(task_lease=True)
    def submit(self,method,**params):
        b=self.broker;b.calls.append((self.number,method,b.current,copy.deepcopy(params)));b.hook(self,method,params)
        row=next((row for row in b.plan['instances'] if row['idx']==b.current),b.plan['instances'][0])
        result=None
        if method=='rotation_preview':result=copy.deepcopy(b.plan)
        elif method=='rotation_snapshot':result=snapshot(b.root)
        elif method=='rotation_bind':
            assert params['manifest']==snapshot(b.root);self.guard=copy.deepcopy(params['manifest']);result=dict(bound=True)
        elif method=='rotation_select':
            assert params['expected_current']==b.current;b.current=params['idx']
            (b.root/'config/current.yml').write_text('active: '+str(b.current));self.guard=snapshot(b.root);result=dict(current=b.current)
        elif method=='rotation_control':self.next_control=params;result=dict(accepted=True)
        elif method=='rotation_window':result=copy.deepcopy(b.identity)
        elif method=='rotation_release':
            if snapshot(b.root)!=self.guard:raise RemoteError('外部修改')
            result=copy.deepcopy(self.guard)
        elif method=='initialize_execution':
            assert params['instance_idx']==b.current
            self.state=self.new('ready',method);result=self.state
        elif method in ('prepare_login','prepare_client_close'):
            self.state=self.new('prepared',method);result=self.state
        elif method=='start_login':
            self.state=self.new('failed' if b.failed_login==b.current else 'success',method)
            if b.failed_login!=b.current:
                if b.identity is None:b.identity=dict(window_handle=100+self.number,process_id=200+self.number)
                b.game_path=row['game_path']
                self.state.update(login=dict(world_verified=True),window_identity=copy.deepcopy(b.identity))
            else:self.state['error']='fixture login failed'
            result=self.state
        elif method=='prepare_application':
            self.state=self.new('prepared',method);self.state.update(uses_charge=True,prepared_applications=copy.deepcopy(row['applications']))
            actions=[{key:(item[key].split(':',1)[1] if key=='id' else item[key]) for key in ('id','app_id','text','requires_confirmation')} for item in row['resources']]
            if b.change_resources:actions[0]['text']='changed resources'
            self.state['resources']=dict(actions=actions,confirmation_id='consent-'+str(b.current),confirmation_required=True)
            result=self.state
        elif method=='start_application':
            assert params['resource_confirmation']=='consent-'+str(b.current)
            b.app_starts.append(b.current);b.budgets.append(copy.deepcopy(params['budget']))
            ledger=BattleBudget(b.budget_dir,params['budget']['id'],'cn',params['budget']['allowance'],params['budget']['battle_limit'])
            if ledger.remaining<20:
                self.state=self.new('stopped',method);self.state['budget']=dict(limited=True,pending=0)
            else:
                entry=ledger.reserve(20,'fixture-account-'+str(b.current))
                if not b.pending_energy:ledger.settle(entry,None)
                self.state=self.new('running' if not b.hold.is_set() else 'failed' if b.failed_group==b.current else 'success',method)
                self.state.update(uses_charge=True,window_identity=copy.deepcopy(b.identity),budget=dict(pending=len(ledger.pending),remaining=ledger.remaining,id=ledger.data['id']))
            result=self.state
        elif method=='start_client_close':
            self.state=self.new('success',method);self.state['client_exit']=dict(process_exited=not b.unknown_exit,requested=True)
            if not b.unknown_exit:b.identity=None;b.game_path=None
            result=self.state
        elif method=='pause_execution':self.state['handoff_control'].update(paused=params['paused']);result=self.state
        elif method=='stop_execution':self.state.update(state='stopped',busy=False);result=self.state
        elif method=='execution_status':
            if self.state['state']=='running' and b.hold.is_set():self.state.update(state='success',busy=False)
            result=copy.deepcopy(self.state)
        else:raise AssertionError(method)
        key=str(len(self.answers));self.answers[key]=(method,copy.deepcopy(result));return key
    def new(self,state,method):
        return dict(job_id=f'{self.number}-{len(self.answers)}',state=state,busy=state=='running',ready=True,
                    handoff_control=dict(self.next_control),uses_charge=False,method=method)
    def result(self,key,timeout):
        if key not in self.observed:self.observed.add(key);raise RequestPending(key)
        method,result=self.answers[key]
        if method=='execution_status' and self.broker.transient and not self.broker.once_error:
            self.broker.once_error=True;raise RemoteError('temporary status fault')
        return copy.deepcopy(result)
    def close(self,timeout):
        self.close_count+=1
        if self.close_count==1:raise RequestPending('owned-client-close')
        self.alive=False;self.broker.closed.append(self.number)


class RotationSessionTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name);self.broker=Broker(self.root);self.events=queue.Queue()
        self.budget_patch=patch('autozzz.battle_budget.budget_directory',return_value=self.broker.budget_dir);self.budget_patch.start();self.addCleanup(self.budget_patch.stop)
        self.session=RotationSession(SimpleNamespace(data=self.root),self.events.put,report_root=self.root/'reports',
                                     client_factory=self.broker.factory,lease_factory=lambda:nullcontext(object()),read_key=lambda key:self.broker.keys.get(key,0))
        self.addCleanup(self.close)
    def close(self):
        self.broker.hold.set();self.session.close();self.session.worker.join(5)
        self.assertFalse(self.session.worker.is_alive())
    def wait(self,predicate):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            event=self.events.get(timeout=2)
            if predicate(event):return event
        self.fail('rotation fixture timed out')
    def preview(self):
        self.session.send('prepare',server='cn');self.wait(lambda e:e['type']=='state' and e['state']['state']=='prepared')
    def start(self,allowance=40,**extra):
        params=dict(budget=dict(id='rotation-fixture',allowance=allowance,battle_limit=2,max_per_battle=20),timeout_seconds=30,resource_confirmation=self.session.confirmation)
        params.update(extra);self.session.send('start',**params)
    def terminal(self):return self.wait(lambda e:e['type']=='state' and not e['state']['busy'] and e['state']['state']!='prepared')['state']

    def test_accounts_rebuild_workers_share_budget_and_return_to_start(self):
        self.preview();self.start();state=self.terminal()
        self.assertEqual(state['state'],'success');self.assertEqual(self.broker.app_starts,[1,2]);self.assertEqual(self.broker.current,1)
        self.assertEqual(len(self.broker.clients),4);self.assertTrue(all(not client.alive for client in self.broker.clients))
        self.assertEqual(self.broker.budgets[0],self.broker.budgets[1])
        ledger=json.loads((self.broker.budget_dir/'rotation-fixture.json').read_text());self.assertEqual(sum(row['cost'] for row in ledger['entries']),40)
        self.assertTrue(self.session.report['returned_to_start']);self.assertEqual(len([stage for stage in self.session.report['stages'] if stage['method']=='start_application']),2)

    def test_transient_status_error_and_pending_shutdown_never_restart_stage(self):
        self.broker.transient=True;self.preview();self.start();self.assertEqual(self.terminal()['state'],'success')
        self.assertEqual(self.broker.app_starts,[1,2]);self.assertTrue(all(client.close_count==2 for client in self.broker.clients))

    def test_missing_aggregate_resource_consent_starts_no_login(self):
        self.preview();self.start(resource_confirmation='wrong');self.assertEqual(self.terminal()['state'],'failed')
        self.assertFalse(any(method=='start_login' for _,method,_,_ in self.broker.calls))

    def test_external_change_after_preview_requires_new_preview(self):
        self.preview();(self.root/'config/current.yml').write_text('changed')
        self.start();self.assertEqual(self.terminal()['state'],'failed');self.assertEqual(len(self.broker.clients),1)

    def test_failed_login_never_runs_account_group_or_next_account(self):
        self.broker.failed_login=1;self.preview();self.start();self.assertEqual(self.terminal()['state'],'failed')
        self.assertFalse(self.broker.app_starts);self.assertEqual(self.broker.current,1)

    def test_partial_group_failure_is_reported_and_later_account_still_runs(self):
        self.broker.failed_group=1;self.preview();self.start();self.assertEqual(self.terminal()['state'],'failed')
        self.assertEqual(self.broker.app_starts,[1,2]);self.assertTrue(self.session.report['returned_to_start'])

    def test_pending_battle_consumption_prevents_next_account(self):
        self.broker.pending_energy=True;self.preview();self.start();self.assertEqual(self.terminal()['state'],'failed')
        self.assertEqual(self.broker.app_starts,[1])

    def test_shared_budget_exhaustion_does_not_get_fresh_allowance(self):
        self.preview();self.start(allowance=20);state=self.terminal();self.assertEqual(state['state'],'stopped')
        ledger=json.loads((self.broker.budget_dir/'rotation-fixture.json').read_text());self.assertEqual(len(ledger['entries']),1)
        self.assertFalse(self.session.report['returned_to_start'])

    def test_changed_prepared_resource_actions_prevent_consumption(self):
        self.broker.change_resources=True;self.preview();self.start();self.assertEqual(self.terminal()['state'],'failed')
        self.assertFalse(self.broker.app_starts)

    def test_stop_while_group_runs_keeps_original_task_and_prevents_next_login(self):
        self.broker.hold.clear();self.preview();self.start()
        self.wait(lambda e:e['type']=='state' and e['state']['busy'] and e['state'].get('budget'))
        self.session.stop();self.assertEqual(self.terminal()['state'],'stopped')
        self.assertEqual(self.broker.app_starts,[1]);self.assertEqual(len([1 for _,method,_,_ in self.broker.calls if method=='start_login']),1)

    def test_client_restart_waits_for_verified_exit_before_next_worker(self):
        for item in self.broker.plan['transitions']:item['restart_client']=True
        self.preview();self.start();self.assertEqual(self.terminal()['state'],'success')
        self.assertEqual(len([1 for _,method,_,_ in self.broker.calls if method=='start_client_close']),2)

    def test_unverified_client_exit_stops_before_next_account(self):
        self.broker.unknown_exit=True
        for item in self.broker.plan['transitions']:item['restart_client']=True
        self.preview();self.start();self.assertEqual(self.terminal()['state'],'failed')
        self.assertEqual(self.broker.app_starts,[1]);self.assertEqual(self.broker.current,1)

    def test_stop_during_preview_does_not_report_prepared(self):
        self.broker.hook=lambda client,method,params:self.session.stop() if method=='rotation_snapshot' else None
        self.session.send('prepare',server='cn');self.assertEqual(self.terminal()['state'],'stopped')
        self.assertFalse(self.session.prepared);self.assertFalse(self.broker.app_starts)

    def test_invalid_budget_is_rejected_before_any_login(self):
        self.preview();self.start(budget=dict(id='bad',allowance=True,battle_limit=1,max_per_battle=20))
        self.assertEqual(self.terminal()['state'],'failed');self.assertEqual(len(self.broker.clients),1)

    def test_manual_pause_is_not_cleared_at_next_stage(self):
        requested=threading.Event()
        def pause(client,method,params):
            if method=='start_login' and not requested.is_set():
                requested.set();self.session.pause(True)
        self.broker.hook=pause;self.preview();self.start()
        self.wait(lambda e:e['type']=='state' and e['state']['control']['manual_paused'])
        self.assertFalse(self.broker.app_starts)
        self.session.pause(False);self.assertEqual(self.terminal()['state'],'success')
