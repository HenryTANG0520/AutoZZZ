"""Global read-only screenshots: borrow an active SDK, otherwise use a short task lock."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace
import uuid


def parse_capture_hotkey(value):
    from .agent_template_hotkeys import formatted,parse_template_hotkey
    if type(value)is not str or len(value)>128:raise ValueError('截图快捷键须为不超过128字符的文字。')
    value=formatted(value)
    if not value:return ()
    if '+' in value:return parse_template_hotkey(value)
    return parse_template_hotkey('Ctrl+'+value)[-1:]


def validate_capture_hotkey(value,trace=None,template=None):
    combo=parse_capture_hotkey(value)
    if trace is None:
        from .trace_preferences import TracePreferences
        trace=TracePreferences().load()['hotkeys']
    from .trace_hotkeys import parse_hotkey
    if combo and any(combo==parse_hotkey(key) for key in trace.values()):
        raise ValueError('全局截图键不能与追踪显隐或追踪截图键相同。')
    if template is None:
        from zzz_od.config.agent_template_generator_config import AgentTemplateGeneratorConfig
        from .agent_template_hotkeys import DEFAULTS as TEMPLATE_KEYS
        config=AgentTemplateGeneratorConfig()
        template={key:config.get_hotkey(key,default) for key,default in TEMPLATE_KEYS.items()}
    if combo and any(combo==parse_capture_hotkey(key) for key in template.values()):
        raise ValueError('全局截图键不能与用户的素材工具快捷键相同。')
    return combo


def settings():
    from one_dragon.envs.env_config import EnvConfig
    from one_dragon.envs.repo_config import RepoConfig
    from one_dragon.base.config.yaml_operator import cached_yaml_data
    cached_yaml_data.clear();config=EnvConfig(RepoConfig(prefer_bundled_config=True))
    key=config.key_screenshot;copy=config.copy_screenshot
    validate_capture_hotkey(key)
    if type(copy)is not bool:raise ValueError('复制截图开关无效。')
    return dict(key_screenshot=key,copy_screenshot=copy)


def geometry(window):
    rect=window.win_rect
    return None if rect is None else (rect.x1,rect.y1,rect.x2,rect.y2)


def process_created(window):
    from .task_lease import _created
    from .windows import kernel32
    return _created(kernel32,window.process_handle)


@contextmanager
def standalone_window(game_path):
    from .windows import find_game,user32,kernel32,W,C
    path=Path(game_path).resolve() if isinstance(game_path,str) and game_path else None
    if path is None or not path.is_file() or path.name.casefold()!='zenlesszonezero.exe':
        raise ValueError('请选择有效的绝区零程序。')
    bound=find_game(str(path))
    from .onedragon_host import BoundGameWindow
    window=BoundGameWindow(1920,1080);window.bound=bound
    pid=W.DWORD();user32.GetWindowThreadProcessId(bound.hwnd,C.byref(pid));window.process_id=pid.value
    window.process_handle=kernel32.OpenProcess(0x101000,False,pid.value)
    try:window.init_win();yield window
    finally:window.clear_binding()


@contextmanager
def retain_active_task(execution):
    """Keep the original existence-based task mutex alive through the borrowed read."""
    from .windows import kernel32,W
    from .run_lock import RUN_MUTEX_NAME
    kernel32.OpenMutexW.argtypes=[W.DWORD,W.BOOL,W.LPCWSTR];kernel32.OpenMutexW.restype=W.HANDLE
    if not execution.busy or not execution._input_ready:raise RuntimeError('原任务正在切换，请稍后截图。')
    handle=kernel32.OpenMutexW(0x100000,False,RUN_MUTEX_NAME)
    if not handle:raise RuntimeError('无法保留原任务锁，未截图。')
    try:
        if not execution.busy or not execution._input_ready:raise RuntimeError('原任务状态已变化，请重新截图。')
        yield
    finally:kernel32.CloseHandle(handle)


class GlobalCaptureRuntime:
    def __init__(self,service):self.service=service;self.nonce=uuid.uuid4().hex;self.closed=False

    def _borrowed(self):
        ctx=getattr(self.service,'_ctx',None);controller=getattr(ctx,'controller',None)
        window=getattr(controller,'game_win',None)
        return ctx,controller,window,getattr(ctx,'execution_control',None)

    def _identity(self,window,control,controller,mode):
        return dict(hwnd=window.bound.hwnd,pid=window.process_id,process_created=process_created(window),
            path=window.bound.path,geometry=geometry(window),epoch=getattr(control,'epoch',None),
            controller=id(controller) if controller is not None else 0,mode=mode,nonce=self.nonce)

    def _valid(self,window,initial,control,controller,mode):
        from .windows import user32
        return (not self.closed and window.bound is not None and window.identity_valid()
            and not user32.IsIconic(window.bound.hwnd) and user32.GetForegroundWindow()==window.bound.hwnd
            and not user32.GetAsyncKeyState(0x79)&0x8000
            and self._identity(window,control,controller,mode)==initial and initial['geometry'] is not None)

    @staticmethod
    def _token(identity):return hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()

    @contextmanager
    def _source(self,game_path):
        ctx,controller,window,control=self._borrowed()
        execution=getattr(self.service,'_execution',None)
        if window is not None and window.bound is not None and not window.identity_valid():
            if execution is not None and execution.busy:raise ValueError('原任务窗口身份已变化。')
            controller=window=control=ctx=None
        if controller is not None and window is not None and window.bound is not None:
            if game_path and Path(window.bound.path).resolve()!=Path(game_path).resolve():
                raise ValueError('当前任务绑定其他区服，未切换截图目标。')
            if execution is not None and execution.busy and not execution._input_ready:
                raise RuntimeError('当前任务正在初始化或切换窗口，请稍后截图。')
            yield ctx,controller,window,control,'borrowed'
        else:
            if execution is not None and execution.busy:raise RuntimeError('当前原生任务尚未绑定窗口，请稍后截图。')
            with standalone_window(game_path) as window:yield None,None,window,None,'standalone'

    def status(self,game_path=None):
        result=dict(settings=settings(),ready=False,foreground=False,token='',window_identity=None,mode='',reason='')
        if self.closed:result['reason']='截图连接已关闭。';return result
        try:
            with self._source(game_path) as (_,controller,window,control,mode):
                if not window.identity_valid():raise ValueError('游戏窗口身份无效。')
                identity=self._identity(window,control,controller,mode)
                result.update(ready=identity['geometry'] is not None,foreground=self._valid(window,identity,control,controller,mode),
                    token=self._token(identity),window_identity={key:identity[key] for key in ('hwnd','pid','process_created')},mode=mode)
        except (OSError,ValueError,RuntimeError) as exc:result['reason']=str(exc)
        return result

    def capture(self,token,game_path=None,include_overlay=None,legacy_lease=None):
        if type(token)is not str or re.fullmatch('[a-f0-9]{64}',token) is None:raise ValueError('截图身份凭据无效。')
        if include_overlay is not None and type(include_overlay)is not bool:raise ValueError('覆盖截图开关须为布尔值。')
        from .run_lock import RunLock
        execution=getattr(self.service,'_execution',None)
        if legacy_lease is not None:
            from .capture_lease import JoinedCaptureLease
            if execution is not None and execution.busy:raise ValueError('不能把旧任务截图能力用于原生执行连接。')
            lock=JoinedCaptureLease(legacy_lease)
        else:lock=retain_active_task(execution) if execution is not None and execution.busy else RunLock()
        with lock,self._source(game_path) as (ctx,controller,window,control,mode):
            identity=self._identity(window,control,controller,mode)
            valid=lambda:((lock.check() if legacy_lease is not None else True)
                and self._valid(window,identity,control,controller,mode) and self._token(identity)==token)
            if not valid():raise ValueError('截图目标、焦点或观察代次已变化，请重新请求。')
            source=getattr(controller,'screenshot_controller',None);owned=source is None
            if owned:
                from .onedragon_host import WindowCapture
                source=WindowCapture(SimpleNamespace(using_background=False),window,1920,1080)
            try:
                snapshot=None;runtime=getattr(ctx,'debug_trace_runtime',None)
                if runtime is not None:
                    snapshot=runtime.snapshot()
                    from .onedragon_battle_state import battle_snapshot
                    snapshot['capture_battle']=battle_snapshot(ctx)
                image=source.get_screenshot(independent=True,resize=False)
                if image is None or not valid():raise ValueError('截图期间窗口身份、焦点或尺寸已变化。')
                return self._save(image,snapshot,control,valid,include_overlay,'legacy' if legacy_lease is not None else mode)
            finally:
                if owned:source.cleanup()

    def _save(self,image,snapshot,control,valid,include_overlay,mode):
        from one_dragon.utils import debug_utils
        from .trace_preferences import TracePreferences
        from .trace_capture import compose_overlay,atomic_bytes
        from PIL import Image
        from io import BytesIO
        if image.ndim!=3 or image.shape[2]!=3 or not image.size or image.shape[0]*image.shape[1]>36_000_000:
            raise ValueError('截图尺寸无效或超过 3600 万像素。')
        options=settings();preferences=TracePreferences().load()
        include_overlay=preferences['patched_capture_enabled'] if include_overlay is None else include_overlay
        filename='autozzz-global-'+uuid.uuid4().hex;path=Path(debug_utils.get_debug_image_path(filename)).absolute()
        root=Path(self.service.data).resolve()
        if not path.resolve().is_relative_to(root) or any(p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&0x400 for p in (path,*path.parents) if p.exists()):
            raise ValueError('SDK 截图目录不能指向资料目录外或链接。')
        if path.exists():raise ValueError('SDK 截图文件已存在。')
        published=[]
        try:
            if not valid():raise ValueError('截图目标已变化。')
            debug_utils.save_debug_image(image,file_name=filename,copy_screenshot=False);published.append(path)
            if not path.is_file() or path.stat().st_size>80_000_000:raise ValueError('SDK 未生成有效截图文件。')
            if not valid():raise ValueError('截图保存期间目标已变化。')
            overlay=None
            if include_overlay and snapshot is not None:
                state=control.status() if control is not None else {}
                if state.get('state')!='running' or not state.get('fresh_frame'):
                    for rows in snapshot['items'].values():rows[:]=[]
                    snapshot['capture_battle']={}
                stamp=snapshot['created']
                for rows in snapshot['items'].values():rows[:]=[row for row in rows if row['created']<=stamp]
                output=compose_overlay(Image.fromarray(image.copy()),snapshot,{'battle':snapshot['capture_battle']},preferences['layers'],preferences['sources'],preferences,
                    font_size=preferences['base_font_size'],opacity=preferences['base_opacity'])
                stream=BytesIO();output.save(stream,format='PNG');overlay=path.with_name(path.stem+preferences['capture_suffix']+'.png')
                if overlay.exists():raise ValueError('覆盖截图文件已存在。')
                atomic_bytes(overlay,stream.getvalue());published.append(overlay)
            if not valid():raise ValueError('截图期间目标已变化，已丢弃。')
            copied=debug_utils.copy_image_to_clipboard(image) if options['copy_screenshot'] else False
            return dict(path=str(path),overlay_path=str(overlay) if overlay else None,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),width=int(image.shape[1]),height=int(image.shape[0]),
                copied=copied,copy_requested=options['copy_screenshot'],clipboard_format='CF_DIB',mode=mode)
        except BaseException:
            for target in published:target.unlink(missing_ok=True)
            raise

    def close(self):self.closed=True
