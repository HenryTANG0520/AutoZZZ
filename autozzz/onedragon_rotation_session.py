"""多账号轮换协调；每个账号重新建立 SDK 进程，整轮共用预算与任务锁。"""
from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path
import queue
import threading
import uuid

from .onedragon_runtime import OneDragonClient,RequestPending,RemoteError
from .rotation_snapshot import snapshot
from .task_lease import RunLeaseOwner
from .onedragon_control import validate_runtime_timeout


class RotationStopped(RuntimeError):pass
class RotationControlChanged(RuntimeError):pass


class RotationSession:
    def __init__(self,paths,emit,report_root=None,*,client_factory=None,lease_factory=None,read_key=None):
        self.paths,self.emit=paths,emit
        from .onedragon_trace_session import TraceSubscription
        self.trace_subscription=TraceSubscription(emit)
        self.client_factory=client_factory or OneDragonClient;self.lease_factory=lease_factory or RunLeaseOwner
        if read_key is None:
            from .windows import user32
            read_key=user32.GetAsyncKeyState
        self.read_key=read_key;self.commands=queue.Queue();self.lock=threading.RLock()
        self.closing=threading.Event();self.stop_requested=threading.Event();self.manual=False;self.version=0;self.f9_down=False
        self.client=None;self.owner=None;self.bound=False;self.job=None;self.game_active=False;self.stop_request=None
        self.busy=False;self.prepared=False;self.plan=None;self.manifest=None;self.confirmation='';self.source_state={}
        self.state='idle';self.error='';self.phase='';self.accounts=[];self.current=None;self.returning=False
        self.report=None;self.report_dir=None;self.last_identity=None
        self.accepted=False;self.budget_state={}
        from .config import ROOT
        self.report_root=Path(report_root) if report_root is not None else ROOT/'runs/onedragon'
        self.worker=threading.Thread(target=self._work,name='AutoZZZ-Account-Rotation',daemon=False);self.worker.start()

    def send(self,command,**params):
        if command not in ('prepare','start'):raise ValueError('轮换会话命令无效。')
        with self.lock:
            if self.closing.is_set() or self.busy:return
            if command=='prepare':self.manual=False;self.version+=1
            self.busy=True;self.stop_requested.clear();self.commands.put((command,params))

    def pause(self,paused):
        with self.lock:self.manual=bool(paused);self.version+=1

    def trace(self,enabled,clear=False):return self.trace_subscription.configure(enabled,clear)
    def take_trace(self):return self.trace_subscription.take()

    def stop(self):
        with self.lock:self.stop_requested.set()

    def close(self):
        self.stop();self.closing.set();self.commands.put(('close',{}))

    def _keys(self):
        if self.read_key(0x79)&0x8000:self.stop()
        if not self.game_active:
            pressed=bool(self.read_key(0x78)&0x8000)
            if pressed and not self.f9_down:self.pause(not self.manual)
            self.f9_down=pressed

    def _gate(self):
        while True:
            self._keys()
            if self.stop_requested.is_set() or self.closing.is_set():raise RotationStopped('轮换已停止。')
            if not self.manual:return
            self._state();self._trace_poll();self.stop_requested.wait(.05)

    def _trace_poll(self):
        if self.client is not None and self.client.alive and not self.closing.is_set():
            self.trace_subscription.pump(self.client,lambda method,**params:self._rpc(method,**params))

    def _state(self):
        resources=[] if self.plan is None else [dict(item,text=row['name']+'：'+item['text']) for row in self.plan['instances'] for item in row['resources']]
        source=copy.deepcopy(self.source_state)
        if self.budget_state:source['budget']=copy.deepcopy(self.budget_state)
        source.update(state=self.state,busy=self.busy,ready=self.prepared,prepared_app='_rotation',
                      uses_charge=bool(self.plan and self.plan['uses_charge']),error=self.error,preparation_step=self.phase,
                      resources=dict(actions=resources,confirmation_id=self.confirmation,
                                     confirmation_required=any(item['requires_confirmation'] for item in resources),
                                     confirmed=[item['id'] for item in resources if item['requires_confirmation']] if self.accepted else []),
                      control=dict(manual_paused=self.manual,focus_waiting=source.get('control',{}).get('focus_waiting',False)),
                      rotation=dict(accounts=copy.deepcopy(self.accounts),current=self.current,returning=self.returning))
        self.emit(dict(type='state',state=source))

    def _log(self,message):
        self.emit(dict(type='log',message=str(message)))
        if self.report_dir is not None and self.report is not None and self.report['status']=='running':
            with (self.report_dir/'run.log').open('a',encoding='utf-8') as stream:stream.write(str(message)+'\n')

    def _save(self):
        if self.report is None:return
        target=self.report_dir/'report.json';temporary=target.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.report,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(target)
        self.emit(dict(type='report',path=str(target)))

    def _rpc(self,method,*,action=False,handoff_version=None,**params):
        with self.lock:
            if action and self.stop_requested.is_set():raise RotationStopped('轮换已停止，未发送后续操作。')
            if handoff_version is not None:
                if self.version!=handoff_version or self.manual:raise RotationControlChanged()
                self.game_active=True
            request=self.client.submit(method,**params)
        while True:
            self._keys()
            if method!='stop_execution' and self.stop_requested.is_set() and self.job is not None and self.stop_request is None:
                self.stop_request=self.client.submit('stop_execution')
            try:return self.client.result(request,timeout=.2)
            except RequestPending:continue

    def _connect(self,leased):
        self._gate()
        self.client=self.client_factory(self.paths,log=self._log,**(dict(run_lease=self.owner) if leased else {}))
        self.bound=False;self.stop_request=None
        try:self.client.start(timeout=.2)
        except RequestPending:
            while True:
                self._keys()
                if self.stop_requested.is_set():raise RotationStopped('启动等待已停止。')
                try:self.client.finish_start(timeout=.2);break
                except RequestPending:continue
        if leased:
            self._rpc('rotation_bind',manifest=self.manifest,action=True);self.bound=True

    def _disconnect(self,transfer=True):
        if self.client is None:return
        error=None;client=self.client
        try:
            if self.bound and transfer:
                try:self.manifest=self._rpc('rotation_release')
                except Exception as exc:error=exc
            while True:
                try:client.close(timeout=.5);break
                except RequestPending:
                    self.phase='等待原工作进程退出';self._state()
            if transfer and self.manifest is not None and snapshot(self.paths.data)!=self.manifest:
                raise ValueError('工作进程退出期间资料已变化，请重新预检。')
            if error is not None:raise error
        finally:
            self.trace_subscription.disconnected()
            self.client=None;self.bound=False;self.job=None;self.game_active=False

    def _job(self,method,expected,*,game=False,**params):
        self._gate();self.source_state={};self.game_active=False;self.stop_request=None
        while True:
            self._gate()
            with self.lock:applied=self.version;control=dict(paused=self.manual,f9_down=self.f9_down)
            if game:self._rpc('rotation_control',**control,action=True)
            self._gate();self.job='starting'
            try:start=self._rpc(method,action=True,handoff_version=applied if game else None,**params);break
            except RotationControlChanged:self.job=None;continue
        self.job=start['job_id']
        while True:
            self._keys()
            if game and self.version!=applied and not self.stop_requested.is_set():
                version=self.version
                try:self._rpc('pause_execution',paused=self.manual)
                except RemoteError:pass  # 任务可能已在暂停请求前结束，仍查询原编号。
                applied=version
            try:state=self._rpc('execution_status')
            except RemoteError as exc:
                if not self.client.protocol_available or not self.client.alive:raise
                self._log('继续查询原轮换阶段：'+str(exc));self.stop_requested.wait(.1);continue
            if state['job_id']!=self.job:raise RuntimeError('轮换阶段编号变化，停止后续账号。')
            self.source_state=state
            if state.get('budget'):self.budget_state=state['budget']
            with self.lock:
                if game and self.version==applied and state.get('handoff_control'):
                    self.manual=state['handoff_control']['paused'];self.f9_down=state['handoff_control']['f9_down']
            self._state()
            self._trace_poll()
            if not state['busy']:break
            self.stop_requested.wait(.1)
        self.job=None;self.game_active=False
        if self.stop_request is not None:
            while True:
                try:self.client.result(self.stop_request,timeout=.2);break
                except RequestPending:continue
            self.stop_request=None
        if self.report is not None:
            self.report['stages'].append(dict(instance_idx=self.current,phase=self.phase,method=method,execution=copy.deepcopy(state)))
            self._save()
        if self.stop_requested.is_set() or state['state']=='stopped':
            if state.get('budget',{}).get('limited'):raise RotationStopped('已达到整轮电量或场次预算。')
            raise RotationStopped('轮换阶段已停止。')
        if state['state'] not in expected:raise RuntimeError(state.get('error') or '轮换阶段未完成。')
        if state.get('window_identity'):self.last_identity=state['window_identity']
        return state

    def _preview(self,params):
        self.state='preparing';self.error='';self.prepared=False;self.plan=None;self.manifest=None;self.accepted=False;self.budget_state={};self.phase='读取逐账号配置';self._state()
        self._connect(False)
        try:
            selection={key:params[key] for key in ('instance_idx','app_id') if key in params}
            self.plan=self._rpc('rotation_preview',server=params['server'],**selection)
            self.accounts=[dict(idx=row['idx'],name=row['name'],state='pending') for row in self.plan['instances']]
            if not self.plan['configuration_valid']:raise ValueError('；'.join(item['message'] for item in self.plan['issues']))
            binding={'expected_intent':params['expected_intent']} if 'expected_intent'in params else {}
            self.manifest=self._rpc('rotation_snapshot',plan_id=self.plan['plan_id'],**binding)
        finally:self._disconnect()
        if self.stop_requested.is_set() or self.closing.is_set():raise RotationStopped('轮换预检已停止。')
        self.confirmation=uuid.uuid4().hex;self.state='prepared';self.prepared=True;self.phase=''
        for row in self.plan['instances']:self._log(row['name']+'：'+' → '.join(item['name'] for item in row['applications']))

    def _select_initialize(self,row,current,server):
        self._gate();self._rpc('rotation_select',idx=row['idx'],expected_current=current,action=True)
        self.current=row['idx'];self.last_identity=None;self.phase=row.get('name',str(row['idx']))+' · 初始化'
        self._job('initialize_execution',{'ready'},server=server,game_path=row['game_path'],region=row['region'],instance_idx=row['idx'])

    def _login(self,row,mode,timeout):
        self.phase=row.get('name',str(row['idx']))+' · 登录'
        self._job('prepare_login',{'prepared'},mode=mode)
        result=self._job('start_login',{'success'},game=True,mode=mode,timeout_seconds=timeout,auto_launch=True)
        if not result.get('login',{}).get('world_verified'):raise RuntimeError('登录未核实进入大世界，未启动该账号应用。')

    def _close_game(self,identity,timeout):
        self.phase='关闭原客户端'
        self._job('prepare_client_close',{'prepared'},expected_window=identity)
        result=self._job('start_client_close',{'success'},game=True,timeout_seconds=min(300,timeout))
        if not result.get('client_exit',{}).get('process_exited'):raise RuntimeError('未核实原客户端退出，未启动下一客户端。')

    @staticmethod
    def _actions(row):
        return [{key:(value.split(':',1)[1] if key=='id' else value) for key,value in item.items() if key not in ('instance_idx','instance_name')} for item in row['resources']]

    def _run(self,params):
        self._gate()
        if not self.prepared or self.manifest is None:raise ValueError('请先完成轮换预检。')
        if snapshot(self.paths.data)!=self.manifest:raise ValueError('轮换资料已变化，请重新预检。')
        required=any(item['requires_confirmation'] for row in self.plan['instances'] for item in row['resources'])
        if required and params.get('resource_confirmation')!=self.confirmation:raise ValueError('请确认本轮逐账号消费或拆解清单。')
        timeout=params.get('timeout_seconds',600)
        validate_runtime_timeout(timeout)
        budget=params.get('budget')
        if self.plan['uses_charge']:
            from .battle_budget import BattleBudget,budget_directory,validate_runtime_budget
            validate_runtime_budget(budget)
            BattleBudget(budget_directory(),budget['id'],self.plan['server'],budget['allowance'],budget['battle_limit'])
        elif budget is not None:raise ValueError('本轮无需电量预算。')
        self.prepared=False;self.state='running';self.error='';self.confirmation=''
        self.accepted=required;self.budget_state={}
        self.report_dir=self.report_root/(datetime.now().strftime('%Y%m%d_%H%M%S_')+uuid.uuid4().hex[:8]);self.report_dir.mkdir(parents=True)
        self.report=dict(operation='account_rotation',status='running',server=self.plan['server'],started_at=datetime.now().astimezone().isoformat(),
                         stages=[],accounts=self.accounts,budget=copy.deepcopy(budget),returned_to_start=False,game_input_verified=False)
        self.report['authorized_resources']=[dict(item) for row in self.plan['instances'] for item in row['resources'] if item['requires_confirmation']]
        self._save();server=self.plan['server'];current=self.plan['original_current'];previous=self.plan['origin'];rows=self.plan['instances']
        with self.lease_factory() as owner:
            self.owner=owner
            try:
                self._connect(True)
                initial_window=self._rpc('rotation_window')
                first_transition=next((item for item in self.plan['transitions'] if item['from_idx']==current and item['to_idx']==rows[0]['idx'] and not item['returning']),None)
                if first_transition and first_transition['restart_client'] and initial_window:
                    self._select_initialize(previous,current,server);self._close_game(initial_window,timeout)
                    self._disconnect();self._connect(True)
                for index,row in enumerate(rows+[rows[0]] if len(rows)>1 else rows):
                    self.returning=index==len(rows);record=next(item for item in self.accounts if item['idx']==row['idx'])
                    if not self.returning:record['state']='running'
                    if index:
                        transition=next(item for item in self.plan['transitions'] if item['from_idx']==previous['idx'] and item['to_idx']==row['idx'])
                        if transition['restart_client']:
                            if self.last_identity is None:raise RuntimeError('缺少原客户端身份，未启动另一客户端。')
                            self._close_game(self.last_identity,timeout)
                        self._disconnect();self._connect(True)
                    self._select_initialize(row,current,server);current=row['idx']
                    self._login(row,'account' if row['login_required'] else 'enter',timeout)
                    if self.returning:
                        self.report['returned_to_start']=True;break
                    record['state']='running';self.phase=row['name']+' · 准备应用组';self._state()
                    execution_app=row.get('execution_app','one_dragon')
                    prepared=self._job('prepare_application',{'prepared'},app_id=execution_app)
                    if [item['id'] for item in prepared.get('prepared_applications',[])]!=[item['id'] for item in row['applications']]:raise ValueError('应用顺序已变化，请重新预检。')
                    resources=prepared.get('resources',{})
                    if resources.get('actions',[])!=self._actions(row):raise ValueError('资源操作清单已变化，请重新预检。')
                    self.phase=row['name']+' · 运行应用组'
                    result=self._job('start_application',{'success','failed'},game=True,app_id=execution_app,
                                     budget=budget if prepared['uses_charge'] else None,timeout_seconds=timeout,
                                     resource_confirmation=resources.get('confirmation_id') if resources.get('confirmation_required') else None)
                    record['state']=result['state'];record['applications']=result.get('applications',[])
                    if result.get('budget',{}).get('pending'):raise RuntimeError('存在未核实的战斗消耗，停止后续账号。')
                    previous=row;self._save()
                if len(rows)==1:self.report['returned_to_start']=True
                self._disconnect()
                self.state='failed' if any(item['state']=='failed' for item in self.accounts) else 'success'
                if self.state=='failed':self.error='未完成账号：'+'、'.join(item['name'] for item in self.accounts if item['state']=='failed')
            finally:
                if self.client is not None:
                    self.stop_requested.set()
                    if self.job is not None:
                        try:self._rpc('stop_execution')
                        except Exception:pass
                    self._disconnect()
                self.owner=None

    def _work(self):
        self.emit(dict(type='connected'))
        try:
            while not self.closing.is_set():
                command,params=self.commands.get()
                if command=='close':break
                try:
                    if command=='prepare':self._preview(params)
                    else:self._run(params)
                except RotationStopped as exc:self.state='stopped';self.error=str(exc)
                except Exception as exc:self.state='failed';self.error=str(exc)
                finally:
                    if self.client is not None:
                        try:self._disconnect(transfer=False)
                        except Exception as exc:self.error+='；退出：'+str(exc)
                    self.busy=False;self.prepared=self.state=='prepared';self.phase=''
                    for row in self.accounts:
                        if row['state']=='running':row['state']=self.state
                    if self.report is not None and self.report['status']=='running':
                        self.report.update(status=self.state,error=self.error,finished_at=datetime.now().astimezone().isoformat());self._save()
                    self._state()
        finally:self.emit(dict(type='closed'))
