"""Passive event hotkeys with identity-checked routing and explicit poll fallback."""
import time
from .agent_template_hotkeys import HotkeyPoller as TemplatePoller
from .global_capture import parse_capture_hotkey
from .global_key_events import KeyEdge


class CapturePoller(TemplatePoller):
    def configure(self,bindings):
        self.bindings={key:parse_capture_hotkey(value) for key,value in bindings.items()}
        self.held={key:self._down(combo) for key,combo in self.bindings.items()}


class GlobalCaptureHotkeys:
    """request(method,params,done): done(result=None,error=None), on the Tk thread."""
    def __init__(self,root,read_key,read_foreground,request,report,*,event_source=None):
        self.root=root;self.request=request;self.report=report;self.foreground=read_foreground
        self.poller=CapturePoller(read_key);self.poller.configure({'capture':''})
        self.key=None;self.pending=False;self.pending_kind=None;self.queued_capture=False;self.next_status=0;self.closed=False
        self.event_source=event_source;self.generation=0;self.pending_edge=None;self.cancel_epoch=0
        self.timer=None;self.error='';self._backend_notice=None;self._last_tick=None
        self.diagnostics=dict(edges=0,captures=0,rejected=0,last_rejection='',max_tick_gap_ms=0.,status_requests=0)
        self.tick()

    @property
    def worker(self):return getattr(self.event_source,'worker',None)

    def join_listener(self,timeout=None):
        # Also safe on the SDK worker's finally path: no Tk calls, even if the
        # transport exits before the user explicitly closes the main window.
        if self.event_source is not None:
            self.event_source.close();self.event_source.join(timeout)

    def status(self):
        backend=self.event_source.status() if self.event_source is not None else dict(state='polling',error='')
        return dict(self.diagnostics,key=self.key,generation=self.generation,pending=self.pending_kind,
            queued=self.pending_edge is not None,closed=self.closed,event_backend=backend)

    def _error(self,message):
        if message and message!=self.error:self.report(message)
        self.error=message or ''

    def update(self,result=None,error=None,*,sent_at=None,requested_edge=None):
        self.pending=False;self.pending_kind=None
        if self.closed:return
        edge=self.pending_edge
        # Equal clock readings only prove order when this request already knew
        # about the same edge. A periodic request may have been in flight.
        fresh=(edge is not None and sent_at is not None and
               (sent_at>edge.created or sent_at==edge.created and requested_edge is edge))
        if error:
            self._error(str(error))
            if fresh:self._reject(str(error))
            else:self._dispatch_edge()
            return
        try:
            key=result['settings']['key_screenshot']
            if key!=self.key:
                self.poller.configure({'capture':key});self.key=key;self.generation+=1
                self.pending_edge=None;self.queued_capture=False
                if self.event_source is not None:self.event_source.configure(parse_capture_hotkey(key),self.generation)
        except (ValueError,TypeError,KeyError) as exc:self._reject(str(exc));self._error(str(exc));return
        self._error('')
        self._dispatch_edge(result if fresh else None)

    def _reject(self,reason):
        if self.pending_edge is not None:
            self.diagnostics['rejected']+=1;self.diagnostics['last_rejection']=reason
        self.pending_edge=None;self.queued_capture=False

    def _edge_valid(self,edge):
        if self.closed or edge.generation!=self.generation:return False
        if not 0<=time.monotonic()-edge.created<=1.5:return False
        if self.poller.read_key(0x79)&0x8000:return False
        epoch=self.event_source.status()['cancel_epoch'] if self.event_source is not None else self.cancel_epoch
        return edge.cancel_epoch==epoch and edge.foreground and self.foreground()==edge.foreground

    def _accept_edge(self,edge):
        if edge.kind=='cancel':
            self.cancel_epoch=max(self.cancel_epoch,edge.cancel_epoch);self._reject('F10 已取消待处理截图。');return
        self.diagnostics['edges']+=1
        if not self._edge_valid(edge):
            self.pending_edge=edge;self._reject('截图按键的焦点、时效、配置或停止代次已变化。');return
        if self.pending_kind=='capture':return  # One capture per press, never a backlog of screenshots.
        self.pending_edge=edge;self.queued_capture=True
        self._dispatch_edge()

    def _dispatch_edge(self,status=None):
        edge=self.pending_edge
        if edge is None:return
        if not self._edge_valid(edge):self._reject('待处理截图已过期或目标焦点已变化。');return
        if self.pending:return
        if status is None:
            self._request_status()
            return
        identity=status.get('window_identity') or {}
        token=status.get('token')
        if (not status.get('ready') or not status.get('foreground') or identity.get('hwnd')!=edge.foreground
                or not isinstance(token,str) or not token):
            self._reject('刷新后游戏目标尚未就绪或并非按键时的前台窗口。')
            return
        self._capture(token)

    def _capture(self,token):
        self.pending_edge=None;self.queued_capture=False
        self.pending=True;self.pending_kind='capture'
        self.diagnostics['captures']+=1
        def done(result=None,error=None):
            self.pending=False;self.pending_kind=None
            if self.closed:return
            if error:self._error(str(error));return
            self.report('截图已保存：'+result['path']+(' · 已复制' if result.get('copied') else ' · 剪贴板复制失败' if result.get('copy_requested') else ''))
        try:self.request('global_capture_capture',{'token':token},done)
        except Exception as exc:done(error=str(exc))

    def _request_status(self):
        if self.closed or self.pending:return
        sent_at=time.monotonic()
        requested_edge=self.pending_edge
        self.pending=True;self.pending_kind='status';self.next_status=sent_at+1
        self.diagnostics['status_requests']+=1
        def done(result=None,error=None):
            self.update(result,error,sent_at=sent_at,requested_edge=requested_edge)
        try:self.request('global_capture_status',{},done)
        except Exception as exc:done(error=str(exc))

    def _events_enabled(self):
        if self.event_source is None:return False
        state=self.event_source.status();signature=(state['state'],state.get('error',''))
        if signature!=self._backend_notice:
            self._backend_notice=signature
            if state['state']=='listening':
                self.report('全局截图：只读键盘事件监听已启用。'+(' '+state['error'] if state.get('error') else ''))
            elif state['state'] in ('failed','closed') and not self.closed:
                self.report('全局截图事件监听不可用，已降级为轮询；短按可能遗漏。 '+state.get('error',''))
        return state['state'] in ('starting','listening')

    def tick(self):
        if self.closed:return
        now=time.monotonic()
        if self._last_tick is not None:self.diagnostics['max_tick_gap_ms']=max(self.diagnostics['max_tick_gap_ms'],(now-self._last_tick)*1000)
        self._last_tick=now
        stopped=bool(self.poller.read_key(0x79)&0x8000)
        if stopped:self._reject('F10 已取消待处理截图。')
        event_mode=self._events_enabled()
        if self.event_source is not None:
            for edge in self.event_source.drain():self._accept_edge(edge)
        if event_mode:
            # Keep a fallback edge baseline current, without firing it twice.
            self.poller.poll(False)
        else:
            for action in self.poller.poll(not stopped):
                if action=='capture':self._accept_edge(KeyEdge('capture',self.generation,now,int(self.foreground() or 0),0,False,False,self.cancel_epoch))
        self._dispatch_edge()
        if not self.pending and time.monotonic()>=self.next_status:
            self._request_status()
        self.timer=self.root.after(40,self.tick)

    def close(self):
        self.closed=True;self.pending_edge=None;self.queued_capture=False;self.poller.close()
        if self.event_source is not None:self.event_source.close()
        if self.timer is not None:self.root.after_cancel(self.timer);self.timer=None
