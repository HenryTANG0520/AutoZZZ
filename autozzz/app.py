from __future__ import annotations

import ctypes as C
import json
import logging
from pathlib import Path
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .config import GAME_PATHS, ROOT, RESOURCE_ROOT, Settings
from .models import AutomationError, FocusLost, InputInterrupted, RunReport, Scene, ScreenshotNotReady, Stopped, TaskResult, Token
from .vision import Vision, save_scene
from .windows import find_game, is_admin, is_elevated, launch


def launch_game(settings: Settings) -> str:
    settings.validate()
    try:
        find_game(settings.game_path)
    except AutomationError:
        import ntpath
        for server, path in GAME_PATHS.items():
            if ntpath.normcase(ntpath.normpath(path)) == ntpath.normcase(ntpath.normpath(settings.game_path)):
                continue
            try:
                find_game(path)
            except AutomationError:
                continue
            name = "国服" if server == "cn" else "国际服"
            raise AutomationError(f"{name}绝区零仍在运行，请先退出再启动另一客户端，避免 multi game detected。")
    return launch(settings.game_path)


def inspect_game(settings: Settings) -> dict:
    settings.validate()
    window = find_game(settings.game_path)
    window.focus()
    vision = Vision(settings.ocr_confidence)
    image, geometry = window.capture()
    scene = vision.read(image, geometry)
    output = ROOT / "runs" / ("check_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    screenshot = save_scene(scene, output, "screen")
    result = dict(window=window.hwnd, title=window.title, width=geometry.width, height=geometry.height,
                  administrator=is_admin(), game_elevated=is_elevated(window.hwnd), ocr_text=scene.text, screenshot=str(screenshot))
    result["input_ready"] = not (result["game_elevated"] and not result["administrator"])
    if not result["input_ready"]:
        result["input_hint"] = "绝区零以管理员权限运行，请用 Start-AutoZZZ-Admin.cmd 或管理员版 EXE 启动助手。"
    (output / "check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


class Context:
    def __init__(self, runner: Runner, window, vision):
        self.runner, self.window, self.vision = runner, window, vision
        self.settings, self.report = runner.settings, runner.report
        self.last_scene: Scene | None = None
        self.scene_epoch = 0
        self._scroll_anchor = None

    def log(self, message: str, level="info"):
        self.runner.log(message, level)

    def checkpoint(self):
        self.runner.checkpoint()

    def input_checkpoint(self):
        # Never block while Windows is holding a key/button down.
        self.runner.guard_reset_day()
        if self.runner._stop.is_set():
            raise Stopped("任务已停止。")
        if self.runner._pause.is_set() or self.scene_epoch != self.runner.input_epoch:
            raise InputInterrupted("暂停已中断当前长按/拖动。")

    def sleep(self, seconds: float):
        remaining = seconds
        while remaining > 0:
            self.checkpoint()
            interval = min(0.1, remaining)
            start = time.monotonic()
            time.sleep(interval)
            remaining -= time.monotonic() - start
        self.checkpoint()

    def snapshot(self) -> Scene:
        while True:
            self.checkpoint()
            capture_epoch = self.runner.input_epoch
            focus_epoch = self.runner.focus_epoch
            captured_at = time.monotonic()
            try:
                image, geometry = self.window.capture()
                scene = self.vision.read(image, geometry)
                scene.captured_at = captured_at
                self.checkpoint()
                if capture_epoch != self.runner.input_epoch:
                    self.last_scene = None
                    if focus_epoch != self.runner.focus_epoch:
                        continue
                    raise InputInterrupted("识别过程中发生手动暂停或恢复，旧截图已丢弃，重新识别当前任务。")
                self.window.guard(geometry)
            except FocusLost:
                self.last_scene = None
                self.runner.wait_for_focus(self.window)
                continue
            if capture_epoch != self.runner.input_epoch:
                self.last_scene = None
                if focus_epoch != self.runner.focus_epoch:
                    continue  # Re-capture pixels and geometry after an automatic focus wait.
                raise InputInterrupted("识别过程中发生手动暂停或恢复，旧截图已丢弃，重新识别当前任务。")
            self.last_scene = scene
            self.scene_epoch = capture_epoch
            return scene

    def _input_scene(self, scene, detail):
        if scene is self.last_scene and self.scene_epoch != self.runner.input_epoch:
            self.last_scene = None
            raise InputInterrupted("暂停恢复后旧画面已丢弃，重新识别当前任务。")
        if scene is not self.last_scene or time.monotonic() - scene.captured_at > 8:
            raise AutomationError(detail)

    def _focus_interrupted(self):
        self.last_scene = None
        self._scroll_anchor = None
        self.runner.wait_for_focus(self.window)
        raise InputInterrupted("焦点恢复后重新识别当前任务，不使用暂停前的点击目标。")

    def click(self, scene: Scene, target: Token | tuple[float, float]):
        self.checkpoint()
        self._input_scene(scene, "识别画面已经过期，请重新识别后操作。")
        from .policy import guard_click
        if isinstance(target, Token) and not any(target is token for token in scene.tokens):
            raise AutomationError("点击目标不属于当前识别截图。")
        guard_click(scene, target)
        coordinates = target.center if isinstance(target, Token) else target
        label = target.text if isinstance(target, Token) else f"{coordinates[0]:.3f},{coordinates[1]:.3f}"
        self.log(f"点击：{label}")
        try:
            self.window.click(*coordinates, scene.geometry)
        except FocusLost:
            # GameWindow.click checks focus before sending move/down; no click was sent.
            self._focus_interrupted()
        self.last_scene = None
        self.sleep(self.settings.action_delay)

    def key(self, name: str, scene: Scene | None = None, duration=0.08):
        self.checkpoint()
        scene = scene or self.last_scene
        if scene is None:
            raise AutomationError("按键前必须重新识别当前游戏页面。")
        self._input_scene(scene, "按键前必须重新识别当前游戏页面。")
        from .policy import guard_scene
        guard_scene(scene)
        if name not in ("esc", "f1", "f2", "f3", "f4", "m", "f", "w") or not 0.05 <= duration <= 1.0:
            raise AutomationError("该按键或持续时间不属于非战斗每日流程。")
        try:
            self.window.key(name, duration, self.input_checkpoint)
        except FocusLost:
            self._focus_interrupted()
        except InputInterrupted:
            self.last_scene = None
            self.runner.wait_for_focus(self.window)
            self.checkpoint()
            self.log("长按已释放；恢复后重新识别实际界面。")
        self.last_scene = None
        self.sleep(self.settings.action_delay)

    def wait(self, predicate, timeout=30, description="界面", *, allow_blank=False) -> Scene:
        elapsed = 0.0
        while elapsed < timeout:
            # Paused time is excluded from transition deadlines.
            self.checkpoint()
            start = time.monotonic()
            inactive_before = self.runner.inactive_duration
            try:
                scene = self.snapshot()
            except ScreenshotNotReady:
                if not allow_blank:
                    raise
                self.last_scene = None
                scene = None
            if scene is not None and predicate(scene):
                return scene
            elapsed += max(0, time.monotonic() - start -
                           (self.runner.inactive_duration - inactive_before))
            self.sleep(0.7)
            elapsed += 0.7
        if self.last_scene:
            self.evidence(self.last_scene, "timeout")
        raise AutomationError(f"等待{description}超时（{timeout} 秒）。请查看本次运行截图。")

    def evidence(self, scene: Scene, name: str):
        stamp = datetime.now().strftime("%H%M%S_%f")
        path = save_scene(scene, Path(self.report.run_dir), f"{stamp}_{name}")
        self.report.evidence.append(str(path))
        return path

    def drag(self, scene: Scene, start, end):
        self.checkpoint()
        self._input_scene(scene, "拖动前必须重新识别刮刮卡页面。")
        from .policy import guard_scene
        guard_scene(scene)
        if not scene.has("每日可刮取一次"):
            raise AutomationError("仅允许在已识别的每日刮刮卡上拖动。")
        try:
            self.window.drag(start, end, scene.geometry, self.input_checkpoint)
        except FocusLost:
            self._focus_interrupted()
        except InputInterrupted:
            self.last_scene = None
            self.runner.wait_for_focus(self.window)
            self.checkpoint()
            self.log("鼠标已释放；恢复后重新识别刮卡界面。")
        self.last_scene = None
        self.sleep(self.settings.action_delay)

    def scroll(self, amount: int):
        if type(amount) is not int or amount not in (-360, -240, -120, 120, 240, 360):
            raise AutomationError("每次列表滚动仅允许一至三个滚轮刻度。")
        self.checkpoint()
        if self.last_scene is not None and self.scene_epoch != self.runner.input_epoch:
            self.last_scene = None
            self._scroll_anchor = None
            raise InputInterrupted("暂停恢复后需重新识别列表并定位滚动区域。")
        if not self._scroll_anchor or self.last_scene is None:
            raise AutomationError("滚动前需要重新识别并定位列表区域。")
        point, geometry, epoch = self._scroll_anchor
        if epoch != self.runner.input_epoch or time.monotonic()-self.last_scene.captured_at > 8:
            raise AutomationError("滚动前定位已经过期。")
        from .policy import guard_scene
        guard_scene(self.last_scene)
        try:
            self.window.move(*point, geometry)
            self.window.scroll(amount)
        except FocusLost:
            self._focus_interrupted()
        self.last_scene = None
        self.sleep(self.settings.action_delay)

    def move(self, scene: Scene, point: tuple[float, float]):
        self.checkpoint()
        self._input_scene(scene, "移动前识别画面已经过期。")
        try:
            self.window.move(*point, scene.geometry)
        except FocusLost:
            self._focus_interrupted()
        self._scroll_anchor = (point, scene.geometry, self.scene_epoch)

    def template(self, scene: Scene, name: str, roi=(0, 0, 1, 1), threshold=0.88):
        path = RESOURCE_ROOT / "assets" / "templates" / f"{name}.png"
        if not path.exists():
            return None
        return self.vision.template(scene, path, roi, threshold)


class Runner:
    def __init__(self, settings: Settings, emit=None):
        self.settings = settings
        self.report = RunReport()
        self._event_sink = emit or (lambda event: None)
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._focus_waiting = False
        self._window = None
        self._monitor_end = threading.Event()
        self._logger = None
        self.input_epoch = 0
        self.focus_epoch = 0
        self.inactive_duration = 0.0

    def _daily_completed(self) -> bool:
        # Only the workflow's verified reward claim may publish this terminal state.
        # A numeric 400 alone is not evidence that the four rewards were claimed.
        return (self.report.daily_verified is True and type(self.report.engagement) is int
                and self.report.engagement == 400)

    def emit(self, event):
        try:
            self._event_sink(event)
        except Exception:
            if not self._daily_completed():
                raise
            if self._logger:
                self._logger.warning("每日任务已验证完成；界面通知失败，已保留完成结果。", exc_info=True)

    def _selected_tasks(self):
        return [name for name in ("scratch", "coffee", "video", "daily")
                if getattr(self.settings, name)]

    def _complete_daily(self):
        if not self._daily_completed():
            raise AutomationError("每日完成状态缺少 400 活跃度和已领取奖励的验证。")
        self.report.status = "success"
        detail = "每日任务已完成：400 活跃度及四档奖励已领取，无需继续执行日常动作。"
        previous = {result.task: result for result in self.report.results}
        results = []
        for name in self._selected_tasks():
            result = previous.get(name)
            if result is None or result.status != "ok":
                if result is not None:
                    self.log(f"{name} 原步骤结果：{result.status}；{result.detail}", "warning")
                result = TaskResult(name, "ok", detail)
            results.append(result)
        results.extend(result for result in self.report.results if result.task not in self._selected_tasks())
        self.report.results = results
        # Publish after the entire report is terminal so a UI callback cannot
        # turn the user's already completed daily goal into a failed run.
        for result in results:
            self.emit(dict(type="task", **result.__dict__))
        self.log(detail)

    def _reward_workflow(self, name, context):
        if name == "weekly":
            from .weekly import WeeklyRewardsWorkflow
            return WeeklyRewardsWorkflow(context)
        from .city_fund import CityFundRewardsWorkflow
        return CityFundRewardsWorkflow(context)

    def _run_finish_rewards(self, context):
        selected = [name for name in ("weekly", "city_fund") if getattr(self.settings, name)]
        for index, name in enumerate(selected):
            try:
                self.checkpoint()
                self.emit(dict(type="task", task=name, status="running", detail="正在领取收尾奖励"))
                workflow = self._reward_workflow(name, context)
                result = self._resume_step(getattr(workflow, name))
            except Stopped as error:
                self.report.results.append(TaskResult(name, "stopped", str(error)))
                for remaining in selected[index + 1:]:
                    self.report.results.append(TaskResult(remaining, "skipped", "用户已停止，未执行后续收尾奖励。"))
                raise
            except Exception as error:
                result = TaskResult(name, "partial", f"收尾奖励未核实完成：{error}")
                if context.last_scene is not None:
                    try:
                        context.evidence(context.last_scene, name + "_cleanup_failure")
                    except Exception as evidence_error:
                        self.log(f"{name} 收尾截图保存失败：{evidence_error}", "warning")
            self.report.results.append(result)
            self.emit(dict(type="task", **result.__dict__))
            self.log(f"{name}：{result.detail}", "info" if result.status == "ok" else "warning")
            if result.status != "ok":
                for remaining in selected[index + 1:]:
                    skipped = TaskResult(remaining, "skipped", "前一收尾页面未核实，未继续发送输入。")
                    self.report.results.append(skipped)
                    self.emit(dict(type="task", **skipped.__dict__))
                break
        if not self._daily_completed() and any(result.status not in ("ok", "skipped")
                                               for result in self.report.results):
            self.report.status = "partial"

    def log(self, message: str, level="info"):
        if self._logger:
            getattr(self._logger, level if level in ("info", "warning", "error") else "info")(message)
        self.emit(dict(type="log", message=message, level=level))

    def stop(self):
        self._stop.set()
        self._pause.clear()

    def toggle_pause(self) -> bool:
        self.input_epoch += 1
        if self._pause.is_set():
            self._pause.clear()
            self.log("继续任务；正在重新检查游戏窗口。")
            self.emit(dict(type="paused", paused=False))
            return False
        self._pause.set()
        self.log("任务已暂停，按 F9 或点击继续恢复。")
        self.emit(dict(type="paused", paused=True))
        return True

    def guard_reset_day(self):
        if self.settings.server == "cn" and self.report.reset_day:
            now = datetime.now(timezone(timedelta(hours=8)))
            if (now-timedelta(hours=4)).date().isoformat() != self.report.reset_day:
                self.stop()
                raise Stopped("运行跨过国服每日重置时间，请重新运行以读取当天任务。")

    def _control_checkpoint(self):
        self.guard_reset_day()
        if self._stop.is_set():
            raise Stopped("任务已停止。")
        if self._pause.is_set():
            started = time.monotonic()
            try:
                while self._pause.is_set():
                    self.guard_reset_day()
                    if self._stop.wait(0.1):
                        raise Stopped("任务已停止。")
            finally:
                self.inactive_duration += time.monotonic() - started
        if self._stop.is_set():
            raise Stopped("任务已停止。")
        self.guard_reset_day()

    def wait_for_focus(self, window=None):
        window = window or self._window
        if window is None:
            return
        self._control_checkpoint()
        try:
            window.guard()
            return
        except FocusLost:
            pass
        started = time.monotonic()
        inactive_before = self.inactive_duration
        self.input_epoch += 1
        self.focus_epoch += 1
        self._focus_waiting = True
        self.log("游戏窗口暂时失去焦点，已暂停输入；等待游戏回到前台后自动继续。", "warning")
        self.emit(dict(type="focus_wait", waiting=True))
        try:
            while True:
                if window.emergency_pressed():
                    self.stop()
                self._control_checkpoint()
                try:
                    geometry = window.guard(refresh=True)
                except FocusLost:
                    if self._stop.wait(0.1):
                        raise Stopped("任务已停止。")
                    continue
                # Valid geometry is read again; all pre-wait scenes are invalidated by the epoch.
                self._control_checkpoint()
                try:
                    geometry = window.guard(refresh=True)
                except FocusLost:
                    continue
                window.baseline = geometry
                self.input_epoch += 1
                self.log("游戏窗口已回到前台，重新识别当前界面并继续任务。")
                return
        finally:
            # Manual pause durations were accounted within _control_checkpoint.
            self.inactive_duration = inactive_before + time.monotonic() - started
            self._focus_waiting = False
            self.emit(dict(type="focus_wait", waiting=False))

    def checkpoint(self):
        self._control_checkpoint()
        if self._window is not None:
            self.wait_for_focus()

    def _resume_step(self, action):
        while True:
            self.checkpoint()
            try:
                return action()
            except InputInterrupted:
                self.checkpoint()
                self.log("暂停前的识别结果已丢弃，按当前画面继续未完成任务。")

    def _prepare_window(self):
        # Focus is requested once when starting; subsequent losses wait for the user.
        try:
            self._window.focus()
        except FocusLost:
            self.wait_for_focus()
        while True:
            self._control_checkpoint()
            try:
                geometry = self._window.guard(refresh=True)
            except FocusLost:
                self.wait_for_focus()
                continue
            self._window.baseline = geometry
            return

    def _hotkeys(self):
        from .windows import user32
        previous = False
        while not self._monitor_end.wait(0.05):
            emergency = (self._window.emergency_pressed() if self._window is not None
                         else bool(user32.GetAsyncKeyState(0x79) & 0x8000))
            if emergency:
                self.stop()
            current = bool(user32.GetAsyncKeyState(0x78) & 0x8000)
            if current and not previous and self._window is not None and user32.GetForegroundWindow() == self._window.hwnd:
                self.toggle_pause()
            previous = current

    def _initial_snapshot(self, context: Context) -> Scene:
        if not self.settings.auto_launch:
            return context.snapshot()
        # A newly created window can be valid before its first rendered frame.
        # Only this initial read may wait for black frames; no input is sent.
        started = time.monotonic()
        inactive_before = self.inactive_duration
        timeout = self.settings.transition_timeout
        announced = False

        def remaining():
            elapsed = max(0, time.monotonic() - started -
                          (self.inactive_duration - inactive_before))
            return timeout - elapsed

        while True:
            self.checkpoint()
            if remaining() <= 0:
                raise ScreenshotNotReady(f"等待游戏首次画面超时（{timeout} 秒）；截图仍为空或黑屏，请查看游戏加载状态。")
            try:
                return context.snapshot()
            except ScreenshotNotReady:
                if not announced:
                    self.log("游戏正在加载首次画面，等待画面可识别后继续；此时不发送输入。")
                    announced = True
            except InputInterrupted:
                # Manual pause invalidates the first frame but must not restart
                # the loading budget or consume a game action.
                pass
            delay = min(0.5, max(0, remaining()))
            if delay:
                context.sleep(delay)

    def enter_game(self) -> RunReport:
        """Launch and verify the game main screen, without running daily tasks."""
        return self.run(entry_only=True)

    def run(self, *, entry_only: bool = False) -> RunReport:
        from .workflows import DailyWorkflow
        from .windows import kernel32
        if entry_only:
            self.settings = replace(self.settings, auto_launch=True)
            self.report.operation = "enter_game"
        directory = ROOT / "runs" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        directory.mkdir(parents=True)
        self.report.run_dir = str(directory)
        self._logger = logging.getLogger(f"autozzz.{directory.name}")
        self._logger.setLevel(logging.INFO)
        handler = logging.FileHandler(directory / "run.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self._logger.addHandler(handler)
        kernel32.CreateMutexW.argtypes = [C.c_void_p, C.c_int, C.c_wchar_p]
        kernel32.CreateMutexW.restype = C.c_void_p
        mutex = kernel32.CreateMutexW(None, False, "Local\\AutoZZZ.DailyRunner")
        already_running = C.get_last_error() == 183
        context = None
        try:
            if not mutex or already_running:
                raise AutomationError("另一个 AutoZZZ 任务正在运行，请先停止后重试。")
            self.settings.validate()
            now = datetime.now(timezone(timedelta(hours=8)))
            self.report.server = self.settings.server
            self.report.game_path = self.settings.game_path
            self.report.started_at = now.isoformat()
            self.report.reset_day = (now-timedelta(hours=4)).date().isoformat() if self.settings.server == "cn" else "unverified-global-reset"
            self.checkpoint()
            threading.Thread(target=self._hotkeys, name="AutoZZZ-hotkeys", daemon=True).start()
            if self.settings.auto_launch:
                self.log(launch_game(self.settings))
                self.log("等待游戏窗口；首次登录/更新请手动完成。")
                for _ in range(120):
                    self.checkpoint()
                    try:
                        self._window = find_game(self.settings.game_path)
                        break
                    except AutomationError:
                        if self._stop.wait(1):
                            raise Stopped("任务已停止。")
            self._window = self._window or find_game(self.settings.game_path)
            if is_elevated(self._window.hwnd) and not is_admin():
                raise AutomationError("绝区零以管理员权限运行；请使用 Start-AutoZZZ-Admin.cmd 或以管理员身份运行 AutoZZZ.exe。")
            self._prepare_window()
            self.log("正在加载中文 OCR 模型。F9 暂停/继续，F10 紧急停止。")
            vision = Vision(self.settings.ocr_confidence)
            context = Context(self, self._window, vision)
            workflow = DailyWorkflow(context)
            initial = self._initial_snapshot(context)
            context.evidence(initial, "start")
            main_result = self._resume_step(workflow.ensure_main)
            if entry_only:
                if not isinstance(main_result, TaskResult) or main_result.task != "main" or main_result.status != "ok":
                    raise AutomationError("尚未确认进入游戏主界面。")
                self.checkpoint()
                context.evidence(context.last_scene, "entered_game")
                self.report.status = "success"
                self.report.results.append(TaskResult("launch", "ok", "已进入游戏主界面，可以开始每日。"))
                self.log("已进入游戏主界面；启动游戏操作完成。")
                return self.report
            if not self._daily_completed():
                self._resume_step(workflow.daily_already_complete)
            if self._daily_completed():
                self._complete_daily()
            else:
                for name in self._selected_tasks():
                    self.checkpoint()
                    self.emit(dict(type="task", task=name, status="running", detail="正在执行"))
                    result = self._resume_step(getattr(workflow, name))
                    self.report.results.append(result)
                    self.emit(dict(type="task", **result.__dict__))
                    self.log(f"{result.task}：{result.detail}")
                    if self._daily_completed():
                        self._complete_daily()
                        break
                if not self.report.results:
                    raise AutomationError("至少选择一个任务。")
                if not self._daily_completed():
                    success = all(r.status == "ok" for r in self.report.results)
                    self.report.status = "success" if success else "partial"
                    if self.settings.daily and not self._daily_completed():
                        self.report.status = "partial"
                    self.log("任务已结束；请查看各项结果与每日活跃、奖励验证。")
            self._run_finish_rewards(context)
        except Stopped as error:
            if self._daily_completed():
                self.log(f"每日任务已验证完成；后续停止不影响完成结果：{error}", "warning")
                self._complete_daily()
            else:
                self.report.status = "stopped"
                self.log(str(error), "warning")
        except Exception as error:
            if self._daily_completed():
                self.log(f"每日任务已验证完成；后续异常不影响完成结果：{error}", "warning")
                self._complete_daily()
            else:
                self.report.status = "failed"
                self.log(str(error), "error")
                if context and context.last_scene:
                    context.evidence(context.last_scene, "failure")
                self.report.results.append(TaskResult("run", "failed", str(error)))
        finally:
            self._monitor_end.set()
            self.report.finished_at = datetime.now(timezone(timedelta(hours=8))).isoformat()
            finalization_error = None

            def record_finalization_error(label, error):
                nonlocal finalization_error
                if self._daily_completed():
                    self.log(f"每日任务已验证完成；{label}失败不影响完成结果：{error}", "warning")
                else:
                    if finalization_error is None:
                        finalization_error = error
                    self.report.status = "failed"
                    detail = f"{label}失败：{error}"
                    self.report.results.append(TaskResult("run", "failed", detail))
                    self.log(detail, "error")

            try:
                if mutex and not kernel32.CloseHandle(mutex):
                    raise AutomationError(f"Windows 运行锁释放失败（错误 {C.get_last_error()}）。")
            except Exception as error:
                record_finalization_error("运行锁释放", error)
            try:
                (directory / "report.json").write_text(json.dumps(self.report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as error:
                record_finalization_error("运行报告保存", error)
            try:
                self.emit(dict(type="finished", report=self.report.to_dict()))
            finally:
                self._logger.removeHandler(handler)
                handler.close()
            if finalization_error is not None:
                raise finalization_error
        return self.report
