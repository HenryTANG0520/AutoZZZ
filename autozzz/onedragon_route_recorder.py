"""复用实际上游小地图定位；截图作业保持原窗口、焦点和取消状态。"""
from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
import math
from pathlib import Path
import threading
import uuid

from .onedragon_control import ExecutionControl, ExecutionStopped, ObservationExpired, WindowState
from .onedragon_library_rules import coordinate
from .run_lock import RunLock, profile_lock

MAX_IMAGE_BYTES = 1_400_000


def _capture_account(service):
    from .config_transaction import config_revision_from_path
    path = Path(service.data) / "config/one_dragon.yml"
    try:
        info = path.stat()
        file_generation = (info.st_ino, info.st_mtime_ns)
    except FileNotFoundError:
        file_generation = None
    return service.ctx.current_instance_idx, config_revision_from_path(path), file_generation


def _capture_window(window):
    from .global_capture import geometry, process_created
    if not window.identity_valid() or (rect := geometry(window)) is None:
        raise ValueError("取景窗口已关闭或不可用，请重新取景。")
    x1, y1, x2, y2 = rect
    return window.bound.path, window.bound.hwnd, window.process_id, process_created(window), (x2 - x1, y2 - y1)


@dataclass(frozen=True)
class CaptureOrigin:
    account: tuple
    window: tuple

    def require_current(self, service):
        from .global_capture import standalone_window
        if _capture_account(service) != self.account:
            raise ValueError("取景后的账号配置已变化，请重新取景。")
        with standalone_window(self.window[0]) as window:
            if _capture_window(window) != self.window:
                raise ValueError("取景窗口或客户区尺寸已变化，请重新取景。")


def decode_client_image(encoded):
    import cv2
    import numpy as np
    if not isinstance(encoded, str) or len(encoded) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
        raise ValueError("截图过大，请导入客户区截图。")
    try: raw = base64.b64decode(encoded, validate=True)
    except ValueError: raise ValueError("截图编码无效。") from None
    if len(raw) > MAX_IMAGE_BYTES: raise ValueError("截图过大。")
    # 先读取图像头，避免解码超大尺寸文件。
    from PIL import Image
    import io
    with Image.open(io.BytesIO(raw)) as image:
        if image.size != (1920, 1080): raise ValueError("截图须为 1920 × 1080 的游戏客户区。")
        image.verify()
    result = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if result is None: raise ValueError("截图无法读取。")
    return cv2.cvtColor(result, cv2.COLOR_BGR2RGB)


def encode_client_image(path):
    """界面导入只接受完整客户区；缩放到上游实际使用的 1080p。"""
    from PIL import Image
    import io
    path = Path(path)
    if path.stat().st_size > 32 * 1024 * 1024: raise ValueError("截图文件过大。")
    with Image.open(path) as source:
        width, height = source.size
        if width * height > 25_000_000 or abs(width / height - 16 / 9) > .01:
            raise ValueError("请选择完整的 16:9 游戏客户区截图。")
        frame = source.convert("RGB").resize((1920, 1080), Image.Resampling.LANCZOS)
    stream = io.BytesIO(); frame.save(stream, format="JPEG", quality=92)
    if stream.tell() > MAX_IMAGE_BYTES: raise ValueError("截图内容过大，请重新保存后导入。")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def route_map(service, area_id, anchor):
    from .map_transaction import MapTransaction
    from .onedragon_library import LibraryStore
    from one_dragon.base.geometry.point import Point
    store = LibraryStore(service)
    with profile_lock(service.data):
        MapTransaction(service.data).assert_clear()
        revision = store._route_map_revision(area_id)
        large_map = store._map(area_id)
        if not isinstance(anchor, list) or len(anchor) != 2: raise ValueError("请先设置有效的起点。")
        height, width = large_map.road_mask.shape[:2]
        point = Point(coordinate(anchor[0], width), coordinate(anchor[1], height))
        def validate_map():
            MapTransaction(service.data).assert_clear()
            if store._route_map_revision(area_id) != revision:
                raise ValueError("定位地图已变化，请重新读取地图和路线。")
        validate_map()
    return large_map, point, validate_map


def locate_position(ctx, large_map, anchor, image):
    import cv2
    from zzz_od.application.devtools.large_map_recorder import large_map_recorder_utils as recorder
    from zzz_od.application.devtools.large_map_recorder.large_map_recorder_wrapper import LargeMapSnapshot
    if ctx.screen_loader.get_area("大世界", "小地图") is None: ctx.screen_loader.reload()
    mini_map = ctx.world_patrol_service.cut_mini_map(image)
    if not mini_map.play_mask_found:
        raise ValueError("未识别到大世界小地图中的玩家标记；请回到大世界后再截图。")
    mini = recorder.create_mini_map_snapshot(ctx, mini_map, .7)
    snapshot = LargeMapSnapshot(large_map, anchor)
    if snapshot.road_mask.ndim == 3: snapshot.road_mask = cv2.cvtColor(snapshot.road_mask, cv2.COLOR_RGB2GRAY)
    match = recorder.cal_pos(ctx, snapshot, mini, anchor, use_icon=True)
    ok, png = cv2.imencode(".png", mini.road_mask)
    result = dict(found=False, position=None, match_value=None,
                  mini_png=base64.b64encode(png).decode("ascii") if ok else "")
    if match is not None and math.isfinite(float(match.confidence)):
        height, width = snapshot.road_mask.shape[:2]
        x, y = match.center.x, match.center.y
        if 0 <= x < width and 0 <= y < height:
            result.update(found=True, position=[int(x), int(y)], match_value=round(float(match.confidence), 4))
    return result


class RouteCaptureJob:
    def __init__(self, service):
        self.service = service
        self.lock = threading.RLock()
        self.worker = None
        self.controller = None
        self.control = None
        self.job_id, self.state, self.error = "", "idle", ""
        self.result = None
        self.origin = None
        self.validate_map = None
        self.cancelled = threading.Event()
        self.closed = False

    @property
    def busy(self): return self.worker is not None and self.worker.is_alive()

    def start(self, area_id, anchor, game_path):
        self.service._assert_idle()
        large_map, point, validate_map = route_map(self.service, area_id, anchor)
        return self._start(large_map, point, game_path, validate_map=validate_map)

    def _start(self, large_map, point, game_path, *, validate_map=None):
        self.service._assert_idle()
        if self.closed: raise RuntimeError("截图会话已关闭。")
        game = Path(game_path)
        if game.name.casefold() != "zenlesszonezero.exe" or not game.is_file(): raise ValueError("请选择有效的 ZenlessZoneZero.exe。")
        with profile_lock(self.service.data):
            account = _capture_account(self.service)
        with self.lock:
            if self.busy: raise RuntimeError("截图任务仍在处理，请继续观察原任务。")
            self.job_id, self.state, self.error = uuid.uuid4().hex, "capturing", ""
            self.result = self.origin = None; self.cancelled.clear()
            self.validate_map = validate_map
            self.control = ExecutionControl(self._probe, self._release, lambda: True, lambda: None, lambda: None)
            self.worker = threading.Thread(target=self._work, args=(large_map, point, str(game.resolve()), account),
                                           name="AutoZZZ-Route-Capture", daemon=False)
            self.worker.start()
        return self.status(self.job_id)

    def _calculate(self, large_map, anchor, screen):
        return locate_position(self.service.ctx, large_map, anchor, screen)

    def _probe(self):
        return self.controller.probe() if self.controller is not None else WindowState(False, False, reason="截图器尚未绑定。")

    def _release(self):
        if self.controller is not None:
            from .onedragon_gamepad import release_controller_inputs
            release_controller_inputs(self.controller)

    def _work(self, large_map, anchor, game_path, account):
        result = origin = None
        try:
            with RunLock(), profile_lock(self.service.data):
                from .map_transaction import MapTransaction
                MapTransaction(self.service.data).assert_clear()
                if _capture_account(self.service) != account:
                    raise ValueError("取景前的账号配置已变化，请重新取景。")
                if self.validate_map is not None:
                    self.validate_map()
                from .onedragon_host import ManagedController
                watch_stop = threading.Event()
                watcher = None
                controller = self.controller = ManagedController(self.service.ctx, self.control)
                try:
                    controller.bind(game_path)
                    watch_thread = threading.Thread(target=self._watch, args=(watch_stop,), name="AutoZZZ-Route-Hotkeys", daemon=True)
                    watch_thread.start()
                    watcher = watch_thread
                    while not self.cancelled.is_set():
                        self.control.poll()
                        if self.control.stopped: raise ExecutionStopped(self.control.error or "截图已停止。")
                        if self.control.status()["state"] != "running":
                            self.cancelled.wait(.02); continue
                        try:
                            with self.control.scope():
                                _, screen = controller.screenshot()
                                candidate = self._calculate(large_map, anchor, screen)
                                self.control.checkpoint(require_frame=True)
                                witness = CaptureOrigin(account, _capture_window(controller.game_win))
                                self.control.checkpoint(require_frame=True)
                        except ObservationExpired:
                            continue  # 保留同一任务，恢复焦点后重新获取画面。
                        except Exception:
                            try: self.control.checkpoint(require_frame=True)
                            except ObservationExpired: continue
                            raise
                        result, origin = candidate, witness
                        break
                finally:
                    watch_stop.set()
                    try:
                        if watcher is not None: watcher.join()
                    finally:
                        try: stopped = self.control.stop()
                        finally:
                            try: controller.cleanup_after_app_shutdown()
                            finally: self.controller = None
                    if result is not None and stopped["error"]:
                        raise ExecutionStopped(stopped["error"])
            with self.lock:
                if self.cancelled.is_set() or result is None:
                    self.state = "cancelled"
                else:
                    self.result, self.origin, self.state = result, origin, "success"
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
                self.state = "cancelled" if self.cancelled.is_set() else "failed"

    def _watch(self, stopped):
        from .windows import user32
        f9 = False
        while not stopped.is_set() and not self.cancelled.is_set():
            try:
                if user32.GetAsyncKeyState(0x79) & 0x8000: self.cancelled.set(); self.control.stop(); return
                pressed = bool(user32.GetAsyncKeyState(0x78) & 0x8000)
                if pressed and not f9: self.control.toggle_pause()
                f9 = pressed
                self.control.poll()
            except Exception as exc:
                self.control.stop(str(exc)); return
            stopped.wait(.01)

    def _check_job(self, job_id):
        if job_id != self.job_id or not self.job_id:
            raise ValueError("截图任务编号无效，请继续查询原任务。")

    def status(self, job_id):
        with self.lock:
            self._check_job(job_id)
            if not self.busy and self.state == "success":
                try:
                    self.origin.require_current(self.service)
                    if self.validate_map is not None: self.validate_map()
                except (OSError, RuntimeError, ValueError) as exc:
                    self.result = self.origin = None
                    self.state, self.error = "failed", str(exc)
            state = self.state
            control = self.control.status() if self.control is not None else {}
            if self.busy and state == "capturing": state = control.get("state", state)
            return dict(job_id=self.job_id, busy=self.busy, state=state, error=self.error,
                        result=copy.deepcopy(self.result), control=control)

    def cancel(self, job_id):
        control = None
        with self.lock:
            self._check_job(job_id)
            if self.busy and self.state == "capturing":
                self.cancelled.set()
                if self.controller is not None:
                    control = self.control
        if control is not None:
            control.stop()
        return self.status(job_id)

    def pause(self, job_id, paused):
        if type(paused) is not bool: raise ValueError("暂停状态须为开关。")
        with self.lock:
            self.status(job_id)
            if self.busy: self.control.set_manual_pause(paused)
        return self.status(job_id)

    def close(self):
        self.closed = True
        if self.busy: self.cancel(self.job_id)
        if self.worker is not None: self.worker.join()
