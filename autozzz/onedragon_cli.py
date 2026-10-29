"""命令行与原生运行窗口共用同一执行会话、预算与报告。"""
from __future__ import annotations

import queue
import re
import signal
import uuid

from .config import ROOT
from .onedragon_runtime import RuntimePaths
from .onedragon_session import RuntimeSession


def combat_budget(allowance, battles, maximum, identifier=None):
    values = (allowance, battles, maximum)
    if all(value is None for value in values):
        if identifier is not None:
            raise ValueError("预算编号须同时指定三项预算。")
        return None
    if any(value is None for value in values):
        raise ValueError("请同时指定总电量、场次与每场电量上限。")
    for value, low, high in ((allowance, 1, 300), (battles, 1, 15), (maximum, 20, 100)):
        if type(value) is not int or not low <= value <= high:
            raise ValueError("战斗预算范围：总电量 1—300、场次 1—15、每场电量 20—100。")
    if maximum > allowance:
        raise ValueError("每场电量上限不能超过本轮总电量。")
    if identifier is not None and (not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identifier)):
        raise ValueError("预算编号仅允许 1—80 位字母、数字、横线和下划线。")
    return dict(id=identifier or "onedragon-" + uuid.uuid4().hex, allowance=allowance,
                battle_limit=battles, max_per_battle=maximum)


def run_application(app_id, *, server, game_path, region, timeout=600,
                    allowance=None, battles=None, maximum=None, budget_id=None, auto_launch=True,
                    emit=lambda event: None, session_factory=RuntimeSession,confirm_resources=False):
    if not isinstance(app_id,str) or not app_id or len(app_id)>512:raise ValueError('应用编号无效。')
    if type(confirm_resources)is not bool:raise ValueError('资源确认开关须为布尔值。')
    if server not in ("cn", "global"):
        raise ValueError("请选择国服或国际服。")
    if type(timeout) is not int or not 30 <= timeout <= 7200:
        raise ValueError("运行时限须为 30—7200 秒。")
    budget = combat_budget(allowance, battles, maximum, budget_id)
    events = queue.Queue()
    session = session_factory(RuntimePaths.discover(ROOT, server), events.put)
    started = False
    stopping = False
    handlers = {}
    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        session.stop()
    try:
        for signum in (signal.SIGINT, getattr(signal, "SIGBREAK", None)):
            if signum is not None:
                handlers[signum] = signal.signal(signum, stop)
        while True:
            event = events.get()
            emit(event)
            kind = event.get("type")
            if kind == "connected":
                if stopping:
                    return dict(state=dict(state="stopped", application_started=False), report=None)
                session.send("prepare", app_id=app_id, server=server,
                             game_path=game_path, region=region)
            elif kind == "error" and not event.get("busy", False):
                raise RuntimeError(event["message"])
            elif kind == "closed":
                raise RuntimeError("执行会话在返回结果前退出。")
            elif kind == "state":
                state = event["state"]
                if state.get("busy"):
                    continue
                if state["state"] == "prepared" and not started:
                    if stopping:
                        return dict(state=dict(state="stopped", application_started=False), report=None)
                    resources=state.get('resources',{});confirmation=None
                    if resources.get('confirmation_required') and not confirm_resources:
                        raise ValueError("此计划包含消费或拆解操作，请在图形界面核对并确认。")
                    if resources.get('confirmation_required'):
                        confirmation=resources.get('confirmation_id')
                        if not isinstance(confirmation,str) or not confirmation:raise ValueError('本轮资源确认编号缺失，请重新准备。')
                    uses_charge = bool(state.get("uses_charge"))
                    if uses_charge != (budget is not None):
                        raise ValueError("刷本须明确指定三项预算；非刷本应用无需战斗预算。")
                    started = True
                    extra={} if confirmation is None else dict(resource_confirmation=confirmation)
                    session.send("start", app_id=app_id, budget=budget,
                                 timeout_seconds=timeout, auto_launch=auto_launch,**extra)
                elif state["state"] in ("success", "failed", "stopped", "interrupted"):
                    return dict(state=state, report=None if session.report_dir is None
                                else str(session.report_dir / "report.json"))
    finally:
        try:
            session.close()
            session.worker.join()
        finally:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
