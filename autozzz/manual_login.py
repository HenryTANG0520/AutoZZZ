"""Explicit read-only wait for the international client's credentials form."""
from __future__ import annotations

from pathlib import Path
import time

from .models import AutomationError, normalize
from .policy import guard_scene
from .workflows import START_BLOCKERS, roi


def validate_manual_login_timeout(seconds, *, app_id, server, auto_launch):
    if seconds is None:
        return
    if type(seconds) is not int or not 1 <= seconds <= 3600:
        raise ValueError('人工登录等待须为 1 至 3600 的整数秒。')
    if app_id != 'one_dragon' or server != 'global' or auto_launch is not True:
        raise ValueError('人工登录等待仅用于自动启动国际服的一条龙运行。')


_FORM_BLOCKERS = tuple(label for label in START_BLOCKERS if label not in ('密码', 'password'))
_FORM_TITLE = (770, 220, 1160, 310)
_FORM_SUBMIT = (852, 628, 1072, 700)


def _anchor(scene, label, area):
    matches = [token for token in scene.tokens if token.within(roi(*area))
               and normalize(token.text).replace('／', '/') == normalize(label)]
    return matches[0] if len(matches) == 1 else None


def is_global_login_form(scene, *, already_waiting=False):
    # These regions come from two archived 3840×2160 login frames, scaled to 1920×1080.
    if not (_anchor(scene, 'HOYOVERSE', _FORM_TITLE) and _anchor(scene, '进入游戏', _FORM_SUBMIT)):
        return False
    if scene.has(_FORM_BLOCKERS + ('网络请求错误', '网络连接失败', '网络错误', 'network error', 'connection error')):
        return False
    if scene.has(('用户协议', '隐私政策', '服务条款', '安全验证', '重试', 'retry'),
                 roi(700, 300, 1220, 720), exact=True):
        return False
    email = _anchor(scene, '输入邮箱/用户名', (610, 368, 875, 438))
    password = _anchor(scene, '输入密码', (610, 470, 875, 544))
    if (email and password and abs(email.box[0] - password.box[0]) * 1920 <= 32
            and 82 <= (password.center[1] - email.center[1]) * 1080 <= 124):
        return True
    # Once the fields have been verified, the two links survive typed credentials.
    return bool(already_waiting and _anchor(scene, '立即注册', (605, 557, 731, 607))
                and _anchor(scene, '忘记密码', (1198, 557, 1314, 607)))


def guard_manual_form_input(scene):
    # Even incomplete form OCR must never turn the form's submit button into a cached-account action.
    if (scene.has('HOYOVERSE', roi(*_FORM_TITLE), exact=True)
            and scene.has('进入游戏', roi(*_FORM_SUBMIT), exact=True)):
        raise AutomationError('疑似 HOYOVERSE 人工登录表单，已停止自动输入。')


class ManualLoginWait:
    def __init__(self, timeout_seconds):
        self.timeout_seconds = timeout_seconds
        self.window_handle = None
        self.process = None
        self.deadline = None
        self.wait_started = None
        self.inactive_before = 0.0

    def bind(self, window, game_path):
        from .process_inventory import window_process_identity

        process = window_process_identity(window.hwnd)
        if process is None or process['path'].casefold() != str(Path(game_path).resolve()).casefold():
            raise AutomationError('无法核实人工登录使用的国际服进程。')
        self.window_handle = window.hwnd
        self.process = process

    def check(self):
        self._check_deadline()
        if self.window_handle is not None:
            from .process_inventory import window_process_identity

            if window_process_identity(self.window_handle) != self.process:
                raise AutomationError('人工登录使用的游戏窗口或进程身份已变化，请重新准备。')

    def _check_deadline(self):
        if self.wait_started is not None and self.deadline is not None and time.monotonic() >= self.deadline:
            raise AutomationError(f'等待手动登录国际服超时（{self.timeout_seconds} 秒）。')

    def begin(self, runner, scene):
        guard_scene(scene)
        if self.wait_started is None:
            self.wait_started = time.monotonic()
            self.inactive_before = runner.inactive_duration
        if self.deadline is None:
            self.deadline = self.wait_started + self.timeout_seconds
            runner.log(f'检测到 HOYOVERSE 登录表单，请手动登录。等待上限 {self.timeout_seconds} 秒，F10 可停止。')
        self._check_deadline()

    def leave(self, runner):
        if self.wait_started is not None:
            self._check_deadline()
            # Manual waiting is separate from the ordinary loading budget. Any pause
            # or focus wait inside this interval was already counted by Runner.
            overlap = runner.inactive_duration - self.inactive_before
            runner.inactive_duration += max(0, time.monotonic() - self.wait_started - overlap)
            self.wait_started = None

    def expected_window(self):
        self.check()
        if self.process is None:
            raise AutomationError('未核实人工登录使用的国际服进程。')
        return dict(window_handle=self.window_handle, process_id=self.process['pid'],
                    process_created=self.process['created'])
