"""MCP action sessions on the original execution worker, RunSlot and controls."""
from __future__ import annotations
from concurrent.futures import Future
import base64
import copy
import hashlib
import json
import threading
import time
import uuid
import zlib

from .onedragon_mcp_compat import load_registrations, to_plain
from .onedragon_mcp_wait import PendingTool

INPUT_TOOLS=frozenset(('click_game','key_tap','drag','input_text'))
ACTION_TOOLS=INPUT_TOOLS|{'open_game','close_game','run_operation'}


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def report_value(value):
    if isinstance(value,dict):
        return {key:'[已脱敏]'if any(word in key.casefold()for word in ('password','token','secret'))or key.casefold()in('text','account','email')else report_value(item)
                for key,item in value.items()}
    if isinstance(value,list):return [report_value(item)for item in value]
    return copy.deepcopy(value)


class InputBackend:
    def __init__(self,execution):self.execution=execution
    def __getattr__(self,name):
        if name not in ('click_game','drag'):raise ValueError('未授权的底层方法。')
        return getattr(self.execution.backend,name)
    def key_tap(self,key,press_time=0.):
        if key.startswith(('xbox_','ds4_')):return self.execution.backend.key_tap(key,press_time)
        from .onedragon_background import physical_input_scope
        controller=self.execution.ctx.controller
        with physical_input_scope(controller):
            controller.screenshot()
            if press_time>0:controller.keyboard_controller.press(key,press_time=press_time)
            else:controller.keyboard_controller.tap(key)
        return dict(success=True,key=key,press_time=press_time)
    def input_text(self,text,use_clipboard=None):
        from .clipboard_input import ClipboardPaste
        from .onedragon_background import physical_input_scope
        from one_dragon.utils.log_utils import mask_text
        controller=self.execution.ctx.controller
        use_cb=self.execution.backend._resolve_use_clipboard(use_clipboard)
        with physical_input_scope(controller):
            controller.screenshot()
            if use_cb:ClipboardPaste().paste(self.execution.control,controller.keyboard_controller,text)
            else:controller.input_str(text)
        return dict(success=True,method='clipboard'if use_cb else'keyboard',masked_text=mask_text(text))


class McpActions:
    LIMIT=64
    BYTE_LIMIT=1024*1024
    def __init__(self,manager):
        self.manager=manager;self.service=manager.service
        self.session=None;self.descriptor=None;self.current=None
        self.history=[];self.sequence=0;self.session_id=uuid.uuid4().hex
        self.registry=None;self.finalizing=False

    def events(self):
        self.finalize()
        return copy.deepcopy(self.history)

    def ack(self,sequence):
        if type(sequence)is not int or not 0<=sequence<=self.sequence:raise ValueError('MCP事件确认编号无效。')
        self.history=[row for row in self.history if row['sequence']>sequence]
        return dict(acknowledged=sequence)

    def add_event(self,record,kind,state=None):
        self.sequence+=1
        row={key:copy.deepcopy(record[key])for key in ('session_id','parent_job','job_id','app_id','descriptor','budget','timeout_seconds','is_prepare')}
        row.update(sequence=self.sequence,kind=kind)
        if state is not None:
            snapshot=copy.deepcopy(state);snapshot.get('resources',{}).pop('confirmation_id',None)
            raw=json.dumps(snapshot,ensure_ascii=False).encode()
            if len(raw)>65536:row['state_zlib']=base64.b64encode(zlib.compress(raw)).decode('ascii')
            else:row['state']=snapshot
        self.history.append(row)

    def capacity(self):
        self.finalize()
        if len(self.history)+2>self.LIMIT:raise ValueError('MCP报告事件尚未确认，请保留原运行窗口并等待报告写入。')
        execution=self.service._execution
        state=execution.status()if execution is not None and hasattr(execution,'status')else{}
        expected=max(256*1024,2*len(json.dumps(state,ensure_ascii=False).encode())+128*1024)
        if len(json.dumps(self.history,ensure_ascii=False).encode())+expected>self.BYTE_LIMIT:
            raise ValueError('MCP报告事件容量已预留，请等待原窗口写入报告后继续。')

    def note(self,result,parent,tool,values=None,is_prepare=False,output=None,restore=None,budget=None,timeout_seconds=None):
        execution=self.service._execution
        descriptor=dict(tool=tool)
        if values:
            descriptor.update(report_value({key:value for key,value in values.items()if key!='text'}))
            if 'text'in values:descriptor['text']='[输入文本已脱敏]'
        record=dict(session_id=self.session_id,parent_job=parent,job_id=result['job_id'],app_id=execution.prepared_app or 'operation_debug',
            descriptor=descriptor,budget=copy.deepcopy(budget if budget is not None else(self.session or {}).get('budget')),
            timeout_seconds=timeout_seconds or(self.session or {}).get('timeout_seconds',600),is_prepare=is_prepare,
            worker=execution.worker,future=Future(),output=output,restore=restore,started=time.time())
        record['execution']=execution
        self.current=record;self.add_event(record,'prepare'if is_prepare else'start')
        self.manager.owned_job=result['job_id']
        self.manager.owned_run=dict(job_id=result['job_id'],prepared_job=parent,app_id=record['app_id'],
            budget=record['budget'],timeout_seconds=record['timeout_seconds'],source='mcp',session_id=self.session_id)
        if tool in ACTION_TOOLS:
            self.manager.log('MCP操作已提交 · '+tool)
        def observe():
            record['worker'].join()
            with self.manager.lock:self.finalize()
        threading.Thread(target=observe,name='AutoZZZ-MCP-Completion',daemon=True).start()
        return record

    def finalize(self):
        record=self.current
        if self.finalizing or record is None or record['future'].done()or record['worker'].is_alive():return
        self.finalizing=True
        try:
            execution=record['execution']
            try:state=execution.status()
            except Exception as exc:
                state=dict(job_id=record['job_id'],busy=False,state=execution.state,error=execution.error,
                    status_observation_error=str(exc))
            if record['restore']is not None:record['restore']()
            state['mcp_run']=copy.deepcopy(self.manager.owned_run)
            record['state']=copy.deepcopy(state)
            self.add_event(record,'finish',state)
            outcome={'prepared':'MCP操作准备完成。','success':'MCP任务已完成。',
                     'failed':'MCP任务失败，可在原任务报告查看。','stopped':'MCP任务已停止。'}
            self.manager.log(outcome.get(state.get('state'),'MCP任务已结束。'))
            record['future'].set_result(state)
        finally:self.finalizing=False

    def status(self):
        self.finalize()
        session=self.session
        return dict(prepared=copy.deepcopy(self.descriptor),session=None if session is None else
            {key:copy.deepcopy(session[key])for key in ('scopes','instance_idx','budget','expires_at','timeout_seconds')},
            pending_events=len(self.history),event_limit=self.LIMIT)

    def prepare(self,descriptor):
        self.capacity();self.service._assert_idle()
        execution=self.service._execution
        if execution is None or not execution.ctx.ready_for_application:raise ValueError('请先在原运行窗口初始化环境。')
        if not isinstance(descriptor,dict)or set(descriptor)-{'mode','op_id','args'}:raise ValueError('操作准备参数无效。')
        mode=descriptor.get('mode','input')
        if mode not in ('input','operation'):raise ValueError('准备模式须为input或operation。')
        request=copy.deepcopy(descriptor);request['mode']=mode
        if len(json.dumps(request,ensure_ascii=False,allow_nan=False).encode())>65536:raise ValueError('操作准备参数超过64 KiB。')
        if mode=='operation':
            values=self.manager.compatibility.validate('run_operation',dict(op_id=request.get('op_id'),args=request.get('args')))
            request=dict(mode=mode,op_id=values['op_id'],args=values['args']or{})
            self.resolve(request)
        self.session=None;self.descriptor=None;self.manager.grant=None;self.manager.editing.session=None
        parent=execution.job_id
        def prepare():
            from .onedragon_requirements import ApplicationRequirements
            from .onedragon_config import ConfigurationStore
            execution.backend._refresh_runtime_config()
            account=execution.ctx.game_account_config
            if execution.connection is not None and execution.connection!=(execution.ctx.current_instance_idx,account.game_path,account.game_region,account.game_language):
                raise ValueError('账号或登录设置已变化，请重新初始化。')
            store=ConfigurationStore(self.service);requirements=ApplicationRequirements(execution.ctx,self.service.data,store)
            requirements.collect([])
            action=dict(id='mcp:manual',app_id='operation_debug',kind='plugin',requires_confirmation=True,
                text='低层输入：消费取决于操作，刷本预算不限制低层输入。')
            if mode=='operation':
                cls,args=self.resolve(request)
                action['text']='已核对operation：'+request['op_id']+'；参数：'+json.dumps(report_value(request['args']),ensure_ascii=False)+'。未知操作的消费由操作决定；不伪称全面计账。'
                self.requirements(requirements,cls,args,store)
            requirements.resource_actions.append(action)
            before=execution.configuration_digest()
            requirements.load(execution.cancelled.is_set,lambda label:setattr(execution,'preparation_step',label))
            if before!=execution.configuration_digest():raise ValueError('操作准备期间配置变化。')
            execution.prepared_hash=before;execution.prepared_app='operation_debug'
            execution.prepared_applications=[];execution.route_debug=None
            execution.prepared_requirements=requirements.summary();execution.uses_charge=requirements.uses_charge
            execution.resource_confirmation.prepare(requirements.resource_actions,before)
            execution.ctx.push_service.protect_transient(account.account,account.password)
            execution.state='prepared';self.descriptor=dict(request,hash=digest(request))
        result=execution._submit('preparing',prepare)
        self.note(result,parent,'prepare_mcp_action',request,is_prepare=True)
        return result

    def resolve(self,request):
        registry=self.manager.compatibility.load()
        inventory=registry.tools['list_operations'][0]()
        if request['op_id']not in {item.op_id for item in inventory.operations}:raise ValueError('operation不在固定上游扫描清单中。')
        from zzz_od.backend import operation_registry
        cls=operation_registry.resolve_op_class(request['op_id'])
        error=operation_registry.validate_args(cls,request['args'])
        if error:raise ValueError(error)
        return cls,operation_registry.coerce_dataclass_params(cls,request['args'])

    def requirements(self,requirements,cls,args,store):
        module=cls.__module__
        plan=args.get('plan')
        if plan is not None and hasattr(plan,'category_name'):
            from zzz_od.application.charge_plan.charge_plan_config import ChargePlanConfig
            error=ChargePlanConfig.validate_item(self.service.ctx,plan)
            if error:raise ValueError(error)
            if plan.category_name!='合成电池':requirements.team_policy(plan.predefined_team_idx,plan.auto_battle_config)
            requirements.uses_charge=not (cls.__name__=='NotoriousHunt'and args.get('use_charge_power',False)is False)
            requirements.uses_hunt=cls.__name__=='NotoriousHunt'
            if requirements.uses_hunt:requirements.models.add('lost_void_det')
            if requirements.uses_charge:self.charge_requirements(requirements,store,plan)
        if module.startswith('zzz_od.operation.battle.'):
            requirements.team_policy(args.get('predefined_team_idx',-1),args.get('auto_battle_config','全配队通用'))
        if module in ('zzz_od.operation.restore_charge','zzz_od.operation.exchange_ether_battery'):
            requirements.uses_charge=True;self.charge_requirements(requirements,store,plan)
        if 'suibian_temple'in module:requirements.collect(['suibian_temple'])
        if 'drive_disc_dismantle'in module:requirements.collect(['drive_disc_dismantle'])

    def charge_requirements(self,requirements,store,plan):
        from .onedragon_restore import restore_action
        from .onedragon_synthesis import synthesis_action
        config=store.get('charge_plan');config.save()
        requirements.restore_charge=config.restore_charge
        action=restore_action(config.restore_charge)
        if action is not None and action['id']not in {row['id']for row in requirements.resource_actions}:requirements.resource_actions.append(action)
        if plan is not None:
            action=synthesis_action([plan],False)
            if action is not None and action['id']not in {row['id']for row in requirements.resource_actions}:requirements.resource_actions.append(action)

    def enable(self,scopes,budget=None,resource_confirmation=None,timeout_seconds=600):
        from .onedragon_control import validate_runtime_timeout
        validate_runtime_timeout(timeout_seconds)
        self.finalize();self.service._assert_idle()
        if not isinstance(scopes,list)or not scopes or set(scopes)-{'input','login','close','operation'}:raise ValueError('操作权限范围无效。')
        execution=self.service._execution
        if self.descriptor is None or execution.state!='prepared'or execution.prepared_hash!=execution.configuration_digest():raise ValueError('请先准备并核对本次操作。')
        if 'operation'in scopes and self.descriptor['mode']!='operation':raise ValueError('请先在本地填写operation及参数并准备。')
        execution.resource_confirmation.verify(resource_confirmation,execution.prepared_hash)
        if execution.uses_charge or budget is not None:self.manager.validate_existing_budget(budget)
        self.session=dict(scopes=sorted(set(scopes)),instance_idx=execution.ctx.current_instance_idx,
            hash=execution.prepared_hash,descriptor_hash=self.descriptor['hash'],budget=copy.deepcopy(budget),
            actions=copy.deepcopy(execution.resource_confirmation.actions),timeout_seconds=timeout_seconds,expires_at=time.time()+3600)
        self.manager.log('已在本地启用MCP操作会话：'+','.join(self.session['scopes']))

    def disable(self):self.session=None

    def check(self,scope):
        self.capacity();self.service._assert_idle();self.manager.compatibility.verify_source()
        session=self.session;execution=self.service._execution
        if session is None or scope not in session['scopes']:raise ValueError('请在本地MCP窗口启用对应操作会话。')
        if (time.time()>session['expires_at']or execution.ctx.current_instance_idx!=session['instance_idx']
            or execution.configuration_digest()!=session['hash']or self.descriptor['hash']!=session['descriptor_hash']):
            self.session=None;raise ValueError('操作会话的账号、配置或参数已变化，请重新准备。')
        if scope=='operation'and execution.uses_charge:self.manager.validate_existing_budget(session['budget'])
        return execution,session

    def call(self,name,values):
        scope='input'if name in INPUT_TOOLS else {'open_game':'login','close_game':'close','run_operation':'operation'}[name]
        execution,session=self.check(scope)
        if name=='run_operation'and digest(dict(mode='operation',op_id=values['op_id'],args=values.get('args')or{}))!=session['descriptor_hash']:
            raise ValueError('远端operation或参数与本地核对项不一致。')
        parent=execution.job_id;output={};original=execution._run_bound_app;prepared_uses_charge=execution.uses_charge
        if self.registry is None:self.registry=load_registrations(self.manager.compatibility.source,InputBackend(execution))
        if name in INPUT_TOOLS:
            if name=='input_text':execution.ctx.push_service.protect_transient(values['text'])
            factory=self.input_operation(name,values,output)
        elif name=='run_operation':
            cls,args=self.resolve(dict(op_id=values['op_id'],args=values.get('args')or{}));factory=lambda ctx:cls(ctx,**args)
        elif name=='open_game':
            from zzz_od.operation.enter_game.enter_game import EnterGame
            factory=lambda ctx:EnterGame(ctx)
        else:factory=None
        def bound(app_id,*unused,**kwargs):
            if name=='run_operation' and cls.__module__=='zzz_od.operation.enter_game.auto_hdr':
                control=execution.startup_control
                if cls.__name__=='DisableAutoHDR':output['message']=control.disable_hdr()
                elif cls.__name__=='EnableAutoHDR':output['message']=control.restore_hdr()
                else:raise ValueError('未知 HDR 操作。')
                execution.state='success'
                return
            raw_launch=name=='run_operation'and cls.__module__ in ('zzz_od.operation.enter_game.open_game','zzz_od.operation.enter_game.open_and_enter_game')
            if name=='open_game'or raw_launch:
                identity=self.startup(execution)
                if identity is None:execution.state='stopped';return
                enter=values['enter']if name=='open_game'else cls.__name__=='OpenAndEnterGame'
                if not enter:
                    execution.ctx.controller.bind(execution.ctx.game_account_config.game_path)
                    execution.state='success';return
                execution.login_control.reset('enter');kwargs['expected_window']=identity
                if raw_launch:
                    from zzz_od.operation.enter_game.enter_game import EnterGame
                    original(app_id,lambda ctx:EnterGame(ctx),display_name=name,**kwargs)
                    if execution.state not in ('failed','stopped')and not execution.login_control.status()['world_verified']:
                        execution.state='failed';execution.error='登录流程未核实进入大世界。'
                    return
            if name=='close_game':
                self.close_client(execution)
                return
            if execution.charge_control is not None and name=='run_operation':
                execution.charge_control.restore_cost=(session['budget']or{}).get('max_per_battle',0)
                synthesis=execution.charge_control.synthesis_control
                if synthesis is not None and cls.__module__=='zzz_od.operation.exchange_ether_battery':
                    self.bind_synthesis(synthesis,args['plan'])
            original(app_id,factory,display_name=name,**kwargs)
            if name=='open_game'and values['enter']and execution.state not in ('failed','stopped')and not execution.login_control.status()['world_verified']:
                execution.state='failed';execution.error='登录流程未核实进入大世界。'
        execution._run_bound_app=bound
        def restore():execution._run_bound_app=original;execution.uses_charge=prepared_uses_charge
        execution.prepared_app='operation_debug';execution.prepared_hash=session['hash']
        execution.resource_confirmation.prepare(session['actions'],session['hash'])
        token=execution.resource_confirmation.token or None
        try:
            execution.uses_charge=scope=='operation'and prepared_uses_charge
            result=self.service.start_application('operation_debug',budget=copy.deepcopy(session['budget'])if execution.uses_charge else None,
                timeout_seconds=session['timeout_seconds'],resource_confirmation=token)
        except Exception:restore();raise
        record=self.note(result,parent,name,values,output=output,restore=restore)
        ticket=PendingTool(record=record,kind=name,enter=values.get('enter'))
        if name in INPUT_TOOLS or name=='close_game'or values.get('block'):return ticket
        return dict(started=True,source='mcp',op_id=values.get('op_id'),started_at=time.strftime('%Y-%m-%dT%H:%M:%S'),hint='用get_run_status查进度与结果')

    def bind_synthesis(self,control,plan):
        """Bind the reviewed explicit plan without editing the user's plan list.

        Existing config/file invariants remain fixed; only the original
        membership test changes from configured plans to the locally reviewed
        operation argument. The original reservation/submission/settlement
        nodes and counters remain installed unchanged.
        """
        from .onedragon_synthesis import plan_spec,CATEGORY
        from one_dragon.utils.yaml_utils import safe_load
        expected=plan_spec(plan.to_dict())
        def authorize(operation):
            if control.path.resolve()!=control.path or not control.path.is_file():raise ValueError('合成配置路径无效。')
            saved=safe_load(control.path.read_bytes())
            if (not isinstance(saved,dict)or saved.get('loop',True)!=control.loop
                or [plan_spec(row)for row in saved.get('plan_list',[])]!=control.specs
                or [plan_spec(row.to_dict())for row in operation.config.plan_list]!=control.specs
                or operation.config.loop!=control.loop):raise ValueError('合成配置已变化，请重新准备。')
            if operation.plan.category_name!=CATEGORY or plan_spec(operation.plan.to_dict())!=expected:
                raise ValueError('合成operation参数与本地核对项不一致。')
        control.authorize=authorize

    def input_operation(self,name,values,output):
        from one_dragon.base.operation.operation import Operation
        from one_dragon.base.operation.operation_node import operation_node
        action=self.registry.tools[name][0]
        execution=self.service._execution
        class InputOperation(Operation):
            def __init__(self,ctx):
                super().__init__(ctx,op_name='MCP '+name)
                self.remaining=max(0,values.get('press_time',0));self.completed=None
            @operation_node(name='受控输入',is_start_node=True)
            def execute_input(self):
                if self.completed is not None:
                    output.update(self.completed);return self.round_success()if output.get('success')else self.round_fail(output.get('error'))
                request=dict(values)
                if name=='key_tap'and self.remaining:request['press_time']=self.remaining
                started=time.monotonic();result=to_plain(action(**request))
                state=execution.control.status()
                self.remaining=max(0,self.remaining-(time.monotonic()-started))
                if state['state']=='stopped':execution.control.checkpoint()
                if name=='key_tap'and self.remaining and request.get('press_time',0)>0:
                    return self.round_wait('等待恢复后继续长按',wait=.05)
                if state['state']!='running':
                    self.completed=dict(result,success=False,error='输入被暂停或失焦打断，未重复发送。')
                    return self.round_wait('等待恢复后继续输入',wait=.05)
                self.completed=result
                execution.control.checkpoint()
                output.update(result)
                return self.round_success()if result.get('success')else self.round_fail(result.get('error','输入未生效'))
        return InputOperation

    def startup(self,execution):
        from .onedragon_startup import GameStartup
        from .game_launch import LaunchOptions
        def elapsed(seconds):
            execution.active_seconds+=seconds
            if execution.active_seconds>=execution.time_limit:execution.cancelled.set()
        def attempt(action):
            with execution.lock:
                if execution.cancelled.is_set()or execution.manual_pause.is_set():return False
                if execution.run_lease is not None:execution.run_lease.check()
                guard=getattr(execution.ctx,'autozzz_runtime_guard',None)
                if guard is not None:guard()
                action();return True
        path=execution.ctx.game_account_config.game_path
        execution.startup=GameStartup(path,cancelled=execution.cancelled,
            paused=execution.manual_pause,poll_keys=execution._poll_task_keys,elapsed=elapsed,
            phase=lambda text:setattr(execution,'preparation_step',text),attempt=attempt,
            options=LaunchOptions.from_config(execution.ctx.game_config),guard=execution._validate_task_input,
            hdr_lease=execution.startup_control.hdr_lease(path))
        try:return execution.startup.run(auto_launch=True)
        finally:execution.startup=None;execution.preparation_step=''

    def close_client(self,execution):
        from .onedragon_client_exit import ClientExit
        execution.ctx.controller.bind(execution.ctx.game_account_config.game_path)
        transition=ClientExit(execution.ctx.controller,execution.control)
        stop=threading.Event();watch=None
        try:
            execution.client_exit=transition;execution.control.reset();execution._input_ready=True
            execution.control.set_manual_pause(execution.manual_pause.is_set())
            watch=threading.Thread(target=execution._watch,args=(stop,),daemon=True);watch.start()
            success=transition.run(execution.cancelled,stop.wait)
            execution.state='success'if success else'stopped'if execution.cancelled.is_set()else'failed'
            if execution.state=='failed':execution.error=execution.control.error or'尚未核实原游戏进程退出。'
        finally:
            stop.set()
            if watch is not None:watch.join()
            execution.client_exit_result=transition.status();execution.control.stop()
            if execution.client_exit_result['process_exited']:execution.ctx.controller.game_win.clear_binding()
            execution.client_exit=None;execution._input_ready=False
