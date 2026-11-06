"""界面持有的一条龙进程会话；准备、运行、暂停和关闭使用同一连接。"""
from __future__ import annotations

from contextlib import nullcontext
import queue
import json
from datetime import datetime
from pathlib import Path
import threading
import uuid
from typing import Any,Callable

from .onedragon_runtime import OneDragonClient,RequestPending,RuntimePaths


class RuntimeSession:
    def __init__(self,paths:RuntimePaths,emit:Callable[[dict],None],report_root:Path|None=None,*,source_handoff=None):
        self.paths,self.emit=paths,emit
        self.source_handoff=source_handoff
        self._active_client=self._handoff_client=self._stopped_handoff=None
        from .onedragon_trace_session import TraceSubscription
        self.trace_subscription=TraceSubscription(emit)
        self.commands=queue.Queue()
        self.closing=threading.Event()
        self.stop_requested=threading.Event()
        self.runner=None
        self.run_hotkeys=None
        self.single_battle_supported=False
        self.phase,self.job_id="",""
        self.mcp_active=False
        self.mcp_event_sequence=0
        self.mcp_report_paths=[]
        self.parameters={}
        from .config import ROOT
        self.report_root=Path(report_root) if report_root is not None else ROOT/"runs"/"onedragon"
        self.report=None
        self.report_dir=None
        self.report_lock=threading.RLock()
        self.worker=threading.Thread(target=self._work,name="AutoZZZ-OneDragon-Session",daemon=False)
        self.worker.start()

    def send(self,command:str,**params:Any):
        if not self.closing.is_set():
            if command=="start" or command=="prepare" and not self.phase:self.stop_requested.clear()
            self.commands.put((command,params))

    def trace(self,enabled,clear=False,*,capture=False):return self.trace_subscription.configure(enabled,clear,capture=capture)
    def take_trace(self):return self.trace_subscription.take()

    def stop(self):
        self.stop_requested.set()
        client=getattr(self,'_handoff_client',None)
        if client is not None:
            self._stopped_handoff=client
            client.signal_host_stop()
        if self.runner is not None:self.runner.stop()
        self.send("stop")

    def pause(self,paused:bool):
        if self.runner is not None:self.runner.toggle_pause()
        else:self.send("pause",paused=paused)

    def close(self):
        self.stop()
        self.closing.set()

    @staticmethod
    def _call(client,method,**params):
        request=client.submit(method,**params)
        while True:
            try:return client.result(request,timeout=.5)
            except RequestPending:continue

    def _legacy_event(self,event):
        window=getattr(self.runner,"_window",None)
        if window is not None:self.emit(dict(type="window",handle=window.hwnd))
        if event.get("type")=="log":self._log(event.get("message",""))
        elif event.get("type")=="focus_wait":self.emit(dict(type="phase",phase="等待游戏恢复焦点" if event.get("waiting") else "正在进入游戏"))
        elif event.get("type")=="paused":self.emit(dict(type="bootstrap_paused",paused=event["paused"]))

    def _log(self,message):
        self.emit(dict(type="log",message=message))
        with self.report_lock:
            if self.report_dir is not None and self.report is not None and self.report.get("status")=="running":
                try:
                    with (self.report_dir/"run.log").open("a",encoding="utf-8") as stream:stream.write(str(message)+"\n")
                except OSError as exc:self.emit(dict(type="log",message="日志写入失败："+str(exc)))

    def _begin_report(self,params):
        self.report_dir=self.report_root/(datetime.now().strftime("%Y%m%d_%H%M%S_")+uuid.uuid4().hex[:8])
        self.report_dir.mkdir(parents=True,exist_ok=False)
        self.report=dict(operation="onedragon",app_id=params["app_id"],status="running",started_at=datetime.now().astimezone().isoformat(),
                         connection=dict(self.parameters),request=dict(params),game_input_verified=False)
        if params.get('source')=='mcp':
            if not hasattr(self,'mcp_report_paths'):self.mcp_report_paths=[]
            self.mcp_report_paths.append(self.report_dir/'report.json')
        self._save_report()

    def _save_report(self):
        if self.report is None:return
        with self.report_lock:
            target=self.report_dir/"report.json"
            temporary=target.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.report,ensure_ascii=False,indent=2),encoding="utf-8")
            temporary.replace(target)
        self.emit(dict(type="report",path=str(target)))

    def _finish_report(self,state):
        with self.report_lock:
            if self.report is not None and self.report.get("status")=="running":
                from .charge_evidence import collect_charge_evidence
                try:self.report['evidence']=collect_charge_evidence(state,getattr(self.paths,'data',None),self.report_dir)
                except (OSError,ValueError) as exc:
                    self.report['evidence_errors']=[str(exc)]
                self.report.update(status=state["state"],execution=state,finished_at=datetime.now().astimezone().isoformat())
                try:self._save_report()
                except OSError as exc:self.emit(dict(type="error",message="报告写入失败："+str(exc),busy=bool(self.phase)))

    def _enter_game(self,params,client=None,*,manual_login=None):
        from .app import Runner
        from .config import Settings
        self.emit(dict(type="phase",phase="正在进入游戏",minimize=True,bootstrapping=True))
        options=guard=None
        if client is not None:
            from .runtime_generation import RuntimeGeneration
            paths=client.paths
            if paths.generation is None:raise RuntimeError('原 SDK 连接未绑定选择代次，请重新准备。')
            runtime=RuntimeGeneration(paths.stable_data or paths.data,paths.generation,paths.management_only)

            def guard():
                runtime.check_execution()
                if manual_login is not None and not client.alive:
                    raise RuntimeError('人工登录期间原一条龙工作进程已退出，请重新准备。')

            options=self._call(client,'game_launch_options')
        shortcut_options = {'run_hotkeys': self.run_hotkeys} if getattr(self, 'run_hotkeys', None) else {}
        if manual_login is not None:
            shortcut_options['manual_login'] = manual_login
        runner=Runner(Settings(server=params["server"],game_path=params["game_path"],auto_launch=True),self._legacy_event,
                      launch_options=options,runtime_guard=guard,**shortcut_options)
        self.runner=runner
        try:
            if self.closing.is_set() or self.stop_requested.is_set():runner.stop()
            report=runner.enter_game().to_dict()
            if manual_login is not None and report['status'] == 'stopped':
                self.stop_requested.set()  # F10 is handled by the legacy runner during entry.
            if report["status"]!="success":raise RuntimeError(report.get("error") or report.get("safe_next") or "进入游戏未完成。")
        finally:self.runner=None

    def _handle(self,client,command,params):
        if command=="rpc":
            if params['method']=='mcp_action_prepare'and self.phase:self._status(client)
            result=self._call(client,params["method"],**params.get("params",{}))
            if params['method']=='mcp_start':self.mcp_active=True
            elif params['method']=='mcp_stop':
                if self.mcp_active:self._status(client)
                self.mcp_active=False
            self.emit(dict(type="rpc_result",tag=params["tag"],result=result))
        elif command=="prepare":
            if self.phase:raise RuntimeError("原任务仍在处理中。")
            self.single_battle_supported=False
            from .onedragon_runtime import EXECUTABLE_APPLICATIONS
            if params['app_id'] not in EXECUTABLE_APPLICATIONS and params['app_id']!='_login':
                catalog=self._call(client,'catalog')
                if not any(app['id']==params['app_id'] and app.get('executable')is True for app in catalog['applications']):
                    raise ValueError('插件应用未注册或尚未启用执行。')
            self.parameters=params
            result=self._call(client,"initialize_execution",server=params["server"],game_path=params["game_path"],region=params["region"],instance_idx=params.get("instance_idx"))
            self.phase="initializing"
            self.job_id=result["job_id"]
            self.emit(dict(type="phase",phase="正在初始化"))
        elif command=="start":
            if self.phase:raise RuntimeError("原任务仍在处理中。")
            from .manual_login import ManualLoginWait, validate_manual_login_timeout
            from .onedragon_control import validate_finish_after_battle

            finish_after_battle = params.get('finish_after_battle', False)
            validate_finish_after_battle(params['app_id'], finish_after_battle, auto_launch=params.get('auto_launch', False))
            manual_seconds = params.get('manual_login_timeout_seconds')
            validate_manual_login_timeout(manual_seconds, app_id=params['app_id'],
                                          server=self.parameters.get('server'), auto_launch=params.get('auto_launch', False))
            if manual_seconds is not None:
                state = self._call(client, 'execution_status')
                if state.get('manual_login_handoff_supported') is not True:
                    raise RuntimeError('当前一条龙桥接不支持人工登录等待，请使用随包运行环境或更新 SDK。')
                if (state.get('state') != 'prepared' or state.get('job_id') != self.job_id
                        or self.parameters.get('app_id') != params['app_id']):
                    raise RuntimeError('请先准备同一国际服一条龙任务。')
            if finish_after_battle and not self.single_battle_supported:
                raise ValueError('当前运行环境不支持结算后停止，请使用随包运行环境或更新 SDK。')
            if not finish_after_battle:
                params.pop('finish_after_battle', None)
            finish_action=params.get('finish_action')
            if finish_action is not None:
                from .onedragon_finish_action import POWER_ACTIONS
                if (params.get('app_id')!='one_dragon' or finish_action not in POWER_ACTIONS
                    or params.get('source') is not None or params.get('close_game_on_success')):
                    raise ValueError('完成后的系统操作仅限本地一条龙运行。')
            self._begin_report(params)
            params.pop('finish_action',None)  # System actions belong to the interactive host, never the SDK or MCP.
            if self.stop_requested.is_set() or self.closing.is_set():
                self._finish_report(dict(state="stopped",application_started=False))
                self.emit(dict(type="state",state=dict(state="stopped",busy=False,ready=True,uses_charge=False)))
                return
            auto_launch=params.pop("auto_launch",False)
            params.pop('manual_login_timeout_seconds', None)
            login=params.get("app_id")=="_login"
            if manual_seconds is not None:
                manual_login = ManualLoginWait(manual_seconds)
                prepared_job_id = self.job_id
                self._enter_game(self.parameters, client, manual_login=manual_login)
                params['entry_handoff'] = dict(prepared_job_id=prepared_job_id, **manual_login.expected_window())
            elif auto_launch and not login:
                self._enter_game(self.parameters,client)
            if self.stop_requested.is_set() or self.closing.is_set():
                self._finish_report(dict(state="stopped",application_started=False))
                self.emit(dict(type="state",state=dict(state="stopped",busy=False,ready=True,uses_charge=False)))
                return
            self.emit(dict(type="phase",phase="正在运行",minimize=True,bootstrapping=False))
            result=(self._call(client,"start_login",mode=self.parameters["login_mode"],auto_launch=auto_launch,
                               timeout_seconds=params.get("timeout_seconds",600)) if login
                    else self._call(client,"start_application",**params))
            self.phase="running"
            self.job_id=result["job_id"]
            if client is not None and client is getattr(self, '_handoff_client', None) and params['app_id']=='charge_plan':
                if not self.stop_requested.is_set() and not self.closing.is_set():
                    try:self.source_handoff.register(client,self.job_id)
                    except (OSError,RuntimeError,ValueError) as exc:self._log('可信交接未登记：'+str(exc))
        elif command=="pause":
            if self.runner is not None:self.runner.toggle_pause()
            else:self._call(client,"pause_execution",**params)
        elif command=="stop":self._call(client,"stop_execution")

    def _status(self,client):
        state=self._call(client,"execution_status")
        if self.mcp_active and state.get('mcp_events'):
            self._mcp_events(client,state['mcp_events'])
        adopted=False
        if self.mcp_active and self.job_id and state['job_id']!=self.job_id:
            remote=state.get('mcp_run',{})
            if (not self.phase and remote.get('job_id')==state['job_id']
                and remote.get('prepared_job')==self.job_id and remote.get('app_id')==self.parameters.get('app_id')):
                self._begin_report(dict(app_id=remote['app_id'],budget=remote.get('budget'),
                    timeout_seconds=remote.get('timeout_seconds',600),auto_launch=False,source='mcp'))
                self.job_id=state['job_id'];self.phase='running';adopted=True
                self.emit(dict(type='phase',phase='MCP已启动授权应用',minimize=True,bootstrapping=False))
        if self.job_id and state["job_id"]!=self.job_id:raise RuntimeError("任务编号发生变化，请停止并检查原任务。")
        if self.mcp_active and not self.phase and not adopted:return
        if not state["busy"]:
            if state['state']=='prepared':
                self.run_hotkeys=state.get('run_hotkeys')
                self.single_battle_supported=state.get('single_battle_supported') is True
            if self.phase=="initializing" and state["state"]=="ready":
                extra = {"route_debug": self.parameters["route_debug"]} if self.parameters.get("route_debug") is not None else {}
                result=(self._call(client,"prepare_login",mode=self.parameters["login_mode"]) if self.parameters["app_id"]=="_login"
                        else self._call(client,"prepare_application",app_id=self.parameters["app_id"],**extra))
                self.phase="preparing"
                self.job_id=result["job_id"]
                self.emit(dict(type="phase",phase="正在准备策略和模型"))
                return
            if self.phase=="running":self._finish_report(state)
            self.phase=""
        self.emit(dict(type="state",state=state))

    def _mcp_events(self,client,events):
        """Consume durable ordered task events before acknowledging their removal.

        A client can issue many instant MCP actions between observer polls. Each
        start/final pair owns a separate report, independent of latest status.
        """
        sequence=self.mcp_event_sequence
        for event in events:
            current=event.get('sequence')
            if type(current)is not int or current<=sequence:continue
            kind=event.get('kind');job=event.get('job_id');app=event.get('app_id')
            if kind in ('prepare','start'):
                if event.get('parent_job')!=self.job_id:
                    raise RuntimeError('MCP事件未接续原任务编号，请保留现场。')
                if self.phase and self.phase not in ('mcp_preparing','preparing'):
                    raise RuntimeError('MCP事件不能覆盖仍未完成的原任务报告。')
                self.job_id=job;self.parameters['app_id']=app
                if kind=='prepare':self.phase='mcp_preparing'
                else:
                    self._begin_report(dict(app_id=app,budget=event.get('budget'),
                        timeout_seconds=event.get('timeout_seconds',600),auto_launch=False,source='mcp',
                        mcp_session_id=event.get('session_id'),mcp_action=event.get('descriptor')))
                    self.phase='running'
                    self.emit(dict(type='phase',phase='MCP操作已启动',minimize=True,bootstrapping=False))
            elif kind=='finish':
                if job!=self.job_id:raise RuntimeError('MCP终态事件任务编号不匹配。')
                if 'state'in event:final=event['state']
                else:
                    import base64,zlib
                    decoder=zlib.decompressobj();raw=decoder.decompress(base64.b64decode(event['state_zlib'],validate=True),16*1024*1024+1)
                    if len(raw)>16*1024*1024 or not decoder.eof or decoder.unused_data:raise ValueError('MCP终态事件大小或编码无效。')
                    final=json.loads(raw)
                if not event.get('is_prepare'):self._finish_report(final)
                self.phase='';self.emit(dict(type='state',state=final))
            else:raise RuntimeError('MCP事件类型无效。')
            sequence=current
            self.mcp_event_sequence=sequence
        if sequence:self._call(client,'mcp_events_ack',sequence=sequence)

    def _start_client(self):
        handoff=getattr(self,'source_handoff',None)
        context=nullcontext({}) if handoff is None else handoff.worker_options(self.paths)
        with context as options:
            client=OneDragonClient(self.paths,log=self._log,**options)
            self._active_client=client
            self._handoff_client=client if options else None
            client.start()
            if options and (self.stop_requested.is_set() or self.closing.is_set()):
                self._stopped_handoff=client
                client.signal_host_stop()
            return client

    def _work(self):
        self._active_client=None
        prepared_once=False
        try:
            client=self._start_client()
            self.emit(dict(type="connected"))
            while not self.closing.is_set():
                try:command,params=self.commands.get(timeout=.2)
                except queue.Empty:command=None
                if command is not None:
                    try:
                        if command=="prepare" and not self.phase:
                            if prepared_once or client is self._stopped_handoff:
                                self.emit(dict(type="phase",phase="正在重新准备：等待原进程退出"))
                                while True:
                                    try:client.close(timeout=5);break
                                    except RequestPending:
                                        self.emit(dict(type="phase",phase="正在重新准备：等待原进程退出"))
                                self.trace_subscription.disconnected()
                                # close 的等待超时只继续观察原进程。确认退出后，仍须保留新到的停止意图。
                                if self.closing.is_set():break
                                if self.stop_requested.is_set():
                                    self.emit(dict(type="state",state=dict(state="stopped",busy=False,ready=False,uses_charge=False)))
                                    continue
                                client=self._start_client()
                            elif self.stop_requested.is_set():
                                self.emit(dict(type="state",state=dict(state="stopped",busy=False,ready=False,uses_charge=False)))
                                continue
                            prepared_once=True
                        # 重新准备期间取消，旧 client 已确认关闭；排队的 stop 不能向它提交新请求。
                        if command=="stop" and not client.alive:continue
                        self._handle(client,command,params)
                    except Exception as exc:
                        if command=='rpc':
                            # Existing management windows consume the original error event.
                            # Only MCP requests have a tagged Future requiring a separate response.
                            tag=params.get('tag')
                            self.emit(dict(type='rpc_error' if str(tag).startswith('mcp-') else 'error',
                                tag=tag,message=str(exc),busy=bool(self.phase)))
                            continue
                        if command=="start" and self.stop_requested.is_set() and not self.phase:
                            self._finish_report(dict(state="stopped",error=str(exc),application_started=False))
                            self.emit(dict(type="state",state=dict(state="stopped",busy=False,ready=True,uses_charge=False)))
                        else:
                            self.emit(dict(type="error",message=str(exc),busy=bool(self.phase)))
                            if not self.phase:self._finish_report(dict(state="failed",error=str(exc)))
                if self.phase or self.mcp_active:
                    try:self._status(client)
                    except Exception as exc:
                        self.emit(dict(type="error",message="状态查询未完成："+str(exc),busy=True))
                        if not client.alive:break
                if client.alive and not self.closing.is_set():
                    self.trace_subscription.pump(client,lambda method,**params:self._call(client,method,**params))
        except Exception as exc:self.emit(dict(type="error",message=str(exc),busy=False))
        finally:
            self.trace_subscription.disconnected()
            client=self._active_client
            exited=client is None
            try:
                if client is not None and client.alive:
                    if self.mcp_active:
                        try:self._call(client,'mcp_stop')
                        except Exception as exc:self.emit(dict(type='log',message='MCP关闭未完成：'+str(exc)))
                        try:self._status(client)
                        except Exception as exc:self.emit(dict(type='log',message='MCP最终状态未完成：'+str(exc)))
                        while self.phase and client.alive:
                            try:self._status(client)
                            except Exception as exc:
                                self.emit(dict(type='log',message='MCP收尾事件未完成：'+str(exc)));break
                            if self.phase:self.closing.wait(.02)
                    if not getattr(self.paths,'program_control',False):
                        try:self._call(client,"stop_execution")
                        except Exception as exc:self.emit(dict(type="log",message=str(exc)))
                while client is not None:
                    try:
                        client.close(timeout=5)
                        exited=True
                        break
                    except RequestPending:self.emit(dict(type="phase",phase="正在等待原进程退出"))
            except Exception as exc:self.emit(dict(type="error",message=str(exc),busy=False))
            self._finish_report(dict(state="stopped" if self.stop_requested.is_set() and exited else "interrupted",process_exited=exited))
            if self.report is not None:
                self.report["process_exited"]=exited
                try:self._save_report()
                except OSError as exc:self.emit(dict(type="error",message="报告写入失败："+str(exc),busy=False))
            for path in self.mcp_report_paths:
                try:
                    value=json.loads(path.read_text(encoding='utf-8'));value['process_exited']=exited
                    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)
                except (OSError,ValueError)as exc:self.emit(dict(type='log',message='MCP历史报告退出事实未更新：'+str(exc)))
            self.emit(dict(type="closed"))
