"""Route upstream launch/HDR nodes through the shared controlled lifecycle."""
from __future__ import annotations
import copy
from functools import update_wrapper
import logging
from pathlib import Path
from threading import RLock


class StartupOperationControl:
    def __init__(self,ctx,execution):
        self.ctx,self.execution,self.originals=ctx,execution,[]
        self._lock=RLock()
        self._hdr_leases={}

    def _game_path(self):
        value=self.ctx.game_account_config.game_path
        path=Path(value).resolve()
        if path.name.casefold()!='zenlesszonezero.exe' or not path.is_file():
            raise ValueError('请选择有效的 ZenlessZoneZero.exe。')
        return path

    def _guard(self):
        self.execution._validate_task_input()
        if self.execution.cancelled.is_set():
            raise RuntimeError('操作已停止。')

    def hdr_lease(self,path):
        with self._lock:
            return self._hdr_leases.get(Path(path).resolve())

    def disable_hdr(self):
        from .game_launch import LaunchSession
        self._guard()
        path=self._game_path()
        with self._lock:
            lease=self._hdr_leases.get(path)
            if lease is None:
                lease=LaunchSession(path,guard=self._guard)
            lease.begin_hdr()
            self._hdr_leases[path]=lease
        return '已临时禁用 HDR，已记录原值。'

    def restore_hdr(self):
        self._guard()
        path=self._game_path()
        with self._lock:
            lease=self._hdr_leases.get(path)
            if lease is None:
                return '本会话没有待恢复的 HDR 修改。'
            lease.close()
            del self._hdr_leases[path]
        return '已恢复 HDR 原值。'

    def _patch(self,cls,name,replacement):
        original=getattr(cls,name);update_wrapper(replacement,original)
        if hasattr(original,'operation_node_annotation'):
            node=copy.copy(original.operation_node_annotation);node.op_method=replacement;replacement.operation_node_annotation=node
        self.originals.append((cls,name,original));setattr(cls,name,replacement)

    def install(self):
        from zzz_od.operation.enter_game.open_game import OpenGame
        from zzz_od.operation.enter_game.auto_hdr import DisableAutoHDR,EnableAutoHDR
        owner=self
        def start(operation):
            from .game_launch import LaunchOptions
            from .onedragon_startup import GameStartup
            execution=owner.execution
            job_id=execution.job_id
            with owner._lock:
                if getattr(operation,'_autozzz_launch_attempt',None)==job_id:
                    return operation.round_fail('本次任务已尝试启动，未重复发送。')
                operation._autozzz_launch_attempt=job_id
            def elapsed(seconds):
                execution.active_seconds+=seconds
                if execution.active_seconds>=execution.time_limit:execution.cancelled.set()
            try:
                path=owner._game_path()
                startup=GameStartup(path,cancelled=execution.cancelled,
                    paused=execution.manual_pause,poll_keys=execution._poll_task_keys,elapsed=elapsed,
                    phase=lambda text:setattr(execution,'preparation_step',text),
                    options=LaunchOptions.from_config(owner.ctx.game_config),guard=owner._guard,
                    hdr_lease=owner.hdr_lease(path))
                execution.startup=startup
                result=startup.run(True)
                return operation.round_success()if result else operation.round_fail('启动已停止。')
            except Exception as exc:
                return operation.round_fail(str(exc) or '游戏启动失败。')
            finally:execution.startup=None;execution.preparation_step=''
        def ready(operation):return operation.round_success()
        def hdr(operation,action):
            try:return operation.round_success(action())
            except Exception as exc:return operation.round_fail(str(exc))
        self._patch(OpenGame,'open_game',start);self._patch(OpenGame,'wait_game',ready)
        self._patch(DisableAutoHDR,'disable_auto_hdr',lambda operation:hdr(operation,owner.disable_hdr))
        self._patch(EnableAutoHDR,'enable_auto_hdr',lambda operation:hdr(operation,owner.restore_hdr))

    def close(self):
        for cls,name,original in reversed(self.originals):setattr(cls,name,original)
        self.originals.clear()
        with self._lock:
            for lease in self._hdr_leases.values():
                try:lease.close()
                except Exception as exc:
                    logging.getLogger(__name__).warning('HDR 恢复未完成，已保留中断记录：%s',exc)
            self._hdr_leases.clear()
