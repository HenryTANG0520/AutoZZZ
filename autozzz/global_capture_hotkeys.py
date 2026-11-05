"""Main-window polling and request routing; does not own an SDK or intercept keys."""
import time
from .agent_template_hotkeys import HotkeyPoller as TemplatePoller
from .global_capture import parse_capture_hotkey


class CapturePoller(TemplatePoller):
    def configure(self,bindings):
        self.bindings={key:parse_capture_hotkey(value) for key,value in bindings.items()}
        self.held={key:self._down(combo) for key,combo in self.bindings.items()}


class GlobalCaptureHotkeys:
    """request(method,params,done): done(result=None,error=None), on the Tk thread."""
    def __init__(self,root,read_key,read_foreground,request,report):
        self.root=root;self.request=request;self.report=report;self.foreground=read_foreground
        self.poller=CapturePoller(read_key);self.poller.configure({'capture':''})
        self.key=None;self.latest=None;self.observed=0;self.pending=False;self.pending_kind=None;self.queued_capture=False;self.next_status=0;self.closed=False
        self.timer=None;self.error='';self.tick()

    def _error(self,message):
        if message and message!=self.error:self.report(message)
        self.error=message or ''

    def update(self,result=None,error=None):
        self.pending=False;self.pending_kind=None
        if self.closed:return
        if error:self.latest=None;self.queued_capture=False;self._error(str(error));return
        try:
            key=result['settings']['key_screenshot']
            if key!=self.key:self.poller.configure({'capture':key});self.key=key
        except (ValueError,TypeError,KeyError) as exc:self.latest=None;self.queued_capture=False;self._error(str(exc));return
        self.latest=result;self.observed=time.monotonic();self._error('')
        if self.queued_capture:self.queued_capture=False;self.capture_now()

    def capture_now(self):
        data=self.latest or {};identity=data.get('window_identity') or {}
        if (self.closed or not data.get('ready') or not data.get('foreground')
                or time.monotonic()-self.observed>1.5 or self.foreground()!=identity.get('hwnd')):
            return False
        if self.pending:
            if self.pending_kind=='status':self.queued_capture=True;return True
            return False
        self.pending=True;self.pending_kind='capture'
        def done(result=None,error=None):
            self.pending=False;self.pending_kind=None
            if self.closed:return
            if error:self._error(str(error));return
            self.report('截图已保存：'+result['path']+(' · 已复制' if result.get('copied') else ' · 剪贴板复制失败' if result.get('copy_requested') else ''))
        try:self.request('global_capture_capture',{'token':data['token']},done)
        except Exception as exc:done(error=str(exc))
        return True

    def tick(self):
        if self.closed:return
        data=self.latest or {};identity=data.get('window_identity') or {}
        stopped=bool(self.poller.read_key(0x79)&0x8000)
        if stopped:self.queued_capture=False
        active=(not stopped and data.get('ready') and data.get('foreground') and self.foreground()==identity.get('hwnd')
            and time.monotonic()-self.observed<=1.5)
        for action in self.poller.poll(active):
            if action=='capture':self.capture_now()
        if not self.pending and time.monotonic()>=self.next_status:
            self.pending=True;self.pending_kind='status';self.next_status=time.monotonic()+1
            try:self.request('global_capture_status',{},self.update)
            except Exception as exc:self.update(error=str(exc))
        self.timer=self.root.after(40,self.tick)

    def close(self):
        self.closed=True;self.latest=None;self.queued_capture=False;self.poller.close()
        if self.timer is not None:self.root.after_cancel(self.timer);self.timer=None
