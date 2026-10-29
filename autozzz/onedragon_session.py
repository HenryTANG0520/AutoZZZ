"""界面持有的一条龙进程会话；准备、运行、暂停和关闭使用同一连接。"""
from __future__ import annotations

import queue
import json
from datetime import datetime
from pathlib import Path
import threading
import uuid
from typing import Any,Callable

from .onedragon_runtime import OneDragonClient,RequestPending,RuntimePaths


class RuntimeSession:
    def __init__(self,paths:RuntimePaths,emit:Callable[[dict],None],report_root:Path|None=None):
        self.paths,self.emit=paths,emit
        from .onedragon_trace_session import TraceSubscription
        self.trace_subscription=TraceSubscription(emit)
        self.commands=queue.Queue()
        self.closing=threading.Event()
        self.stop_requested=threading.Event()
        self.runner=None
        self.phase,self.job_id="",""
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

    def trace(self,enabled,clear=False):return self.trace_subscription.configure(enabled,clear)
    def take_trace(self):return self.trace_subscription.take()

    def stop(self):
        self.stop_requested.set()
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
                self.report.update(status=state["state"],execution=state,finished_at=datetime.now().astimezone().isoformat())
                try:self._save_report()
                except OSError as exc:self.emit(dict(type="error",message="报告写入失败："+str(exc),busy=bool(self.phase)))

    def _enter_game(self,params):
        from .app import Runner
        from .config import Settings
        self.emit(dict(type="phase",phase="正在进入游戏",minimize=True,bootstrapping=True))
        runner=Runner(Settings(server=params["server"],game_path=params["game_path"],auto_launch=True),self._legacy_event)
        self.runner=runner
        try:
            if self.closing.is_set() or self.stop_requested.is_set():runner.stop()
            report=runner.enter_game().to_dict()
            if report["status"]!="success":raise RuntimeError(report.get("error") or report.get("safe_next") or "进入游戏未完成。")
        finally:self.runner=None

    def _handle(self,client,command,params):
        if command=="rpc":
            result=self._call(client,params["method"],**params.get("params",{}))
            self.emit(dict(type="rpc_result",tag=params["tag"],result=result))
        elif command=="prepare":
            if self.phase:raise RuntimeError("原任务仍在处理中。")
            self.parameters=params
            result=self._call(client,"initialize_execution",server=params["server"],game_path=params["game_path"],region=params["region"],instance_idx=params.get("instance_idx"))
            self.phase="initializing"
            self.job_id=result["job_id"]
            self.emit(dict(type="phase",phase="正在初始化"))
        elif command=="start":
            if self.phase:raise RuntimeError("原任务仍在处理中。")
            self._begin_report(params)
            if self.stop_requested.is_set() or self.closing.is_set():
                self._finish_report(dict(state="stopped",application_started=False))
                self.emit(dict(type="state",state=dict(state="stopped",busy=False,ready=True,uses_charge=False)))
                return
            auto_launch=params.pop("auto_launch",False)
            login=params.get("app_id")=="_login"
            if auto_launch and not login:self._enter_game(self.parameters)
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
        elif command=="pause":
            if self.runner is not None:self.runner.toggle_pause()
            else:self._call(client,"pause_execution",**params)
        elif command=="stop":self._call(client,"stop_execution")

    def _status(self,client):
        state=self._call(client,"execution_status")
        if self.job_id and state["job_id"]!=self.job_id:raise RuntimeError("任务编号发生变化，请停止并检查原任务。")
        if not state["busy"]:
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

    def _work(self):
        client=OneDragonClient(self.paths,log=self._log)
        prepared_once=False
        try:
            client.start()
            self.emit(dict(type="connected"))
            while not self.closing.is_set():
                try:command,params=self.commands.get(timeout=.2)
                except queue.Empty:command=None
                if command is not None:
                    try:
                        if command=="prepare" and not self.phase:
                            if prepared_once:
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
                                client=OneDragonClient(self.paths,log=self._log)
                                client.start()
                            elif self.stop_requested.is_set():
                                self.emit(dict(type="state",state=dict(state="stopped",busy=False,ready=False,uses_charge=False)))
                                continue
                            prepared_once=True
                        # 重新准备期间取消，旧 client 已确认关闭；排队的 stop 不能向它提交新请求。
                        if command=="stop" and not client.alive:continue
                        self._handle(client,command,params)
                    except Exception as exc:
                        if command=="start" and self.stop_requested.is_set() and not self.phase:
                            self._finish_report(dict(state="stopped",error=str(exc),application_started=False))
                            self.emit(dict(type="state",state=dict(state="stopped",busy=False,ready=True,uses_charge=False)))
                        else:
                            self.emit(dict(type="error",message=str(exc),busy=bool(self.phase)))
                            if not self.phase:self._finish_report(dict(state="failed",error=str(exc)))
                if self.phase:
                    try:self._status(client)
                    except Exception as exc:
                        self.emit(dict(type="error",message="状态查询未完成："+str(exc),busy=True))
                        if not client.alive:break
                if client.alive and not self.closing.is_set():
                    self.trace_subscription.pump(client,lambda method,**params:self._call(client,method,**params))
        except Exception as exc:self.emit(dict(type="error",message=str(exc),busy=False))
        finally:
            self.trace_subscription.disconnected()
            exited=False
            try:
                if client.alive:
                    try:self._call(client,"stop_execution")
                    except Exception as exc:self.emit(dict(type="log",message=str(exc)))
                while True:
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
            self.emit(dict(type="closed"))
