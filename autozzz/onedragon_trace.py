"""读取实际 DebugTraceBus；显示数据不进入执行状态或运行报告。"""
from __future__ import annotations

from contextlib import nullcontext
import json
from itertools import islice
import math
import threading
import time


KINDS={'vision':(1.8,200),'decision':(30.,100),'timeline':(60.,150),'perf':(30.,256)}


def finite(value,limit=1e100):
    return type(value)in (int,float) and abs(value)<=limit and math.isfinite(value)


def debug_text(value,limit=4096):
    if type(value)is not str:value=str(value) if value is None or type(value)is bool or finite(value) else '<'+type(value).__name__[:64]+'>'
    return (value[:limit]+('…' if len(value)>limit else '')).encode('utf-8',errors='replace').decode('utf-8')


def plain_debug(value,depth=0,budget=None):
    """调试元数据只保留有限 JSON 数据，不调用任意对象的 repr。"""
    if budget is None:budget=[96]
    budget[0]-=1
    if budget[0]<0 or depth>4:return '…'
    if value is None or type(value)is bool:return value
    if type(value)in (int,float):return value if finite(value) else None
    if type(value)is str:return debug_text(value,1024)
    if isinstance(value,(list,tuple)):return [plain_debug(item,depth+1,budget) for item in value[:24]]
    if isinstance(value,dict):
        result={debug_text(key,128):plain_debug(item,depth+1,budget) for key,item in islice(value.items(),24) if type(key)is str or type(key)is int and abs(key)<1e20}
        if len(value)>24:result['…']='其余元数据未显示'
        return result
    return '<'+type(value).__name__[:64]+'>'


class TraceRuntime:
    def __init__(self,ctx):
        self.ctx=ctx;self.bus=ctx.debug_trace_bus;self.lock=threading.RLock()
        self.closed=False;self.enabled=False;self.epoch=None;self.generation=0;self.discarded=0
        self.originals={}
        self.boss_timing=None
        self.bus.enabled=False;self.bus.clear()
        from .trace_auto_capture import AutomaticOverlayCapture
        self.automatic_capture=AutomaticOverlayCapture(self)
        self.automatic_capture.install()
        for kind in KINDS:
            name='add_'+kind;original=getattr(self.bus,name);self.originals[name]=original
            setattr(self.bus,name,self._guard(original))

    def _control(self):return getattr(self.ctx,'execution_control',None)

    def _sync(self,control):
        epoch=control.epoch if control is not None else 0
        if self.epoch!=epoch:
            self.epoch=epoch;self.generation+=1;self.bus.clear()

    def _guard(self,original):
        def publish(item):
            if not self.enabled or self.closed:return
            control=self._control()
            with control.lock if control is not None else nullcontext():
                with self.lock:
                    self._sync(control)
                    if not self.enabled or self.closed or control is not None and control.task_epoch!=control.epoch:
                        self.discarded+=1;return
                    original(item)
        return publish

    def configure(self,enabled,clear=False,*,boss_timing=False,execution=None):
        if any(type(value) is not bool for value in (enabled,clear,boss_timing)):
            raise ValueError('追踪开关须为布尔值。')
        control=self._control()
        if boss_timing and (not enabled or execution is None or execution.ctx is not self.ctx or execution.control is not control):
            raise ValueError('首领时序需要开启追踪并先初始化同一工作进程的执行环境。')
        if boss_timing:
            from .onedragon_boss_timing import BossTimingTrace
            timing=BossTimingTrace(self,execution.battle_control)
        with control.lock if control is not None else nullcontext():
            with self.lock:
                if self.closed:raise RuntimeError('追踪连接已关闭。')
                self._sync(control)
                timing_changed=boss_timing!=(self.boss_timing is not None)
                if timing_changed:
                    if boss_timing:
                        timing.install()
                        self.boss_timing=timing
                    else:
                        self.boss_timing.close()
                        self.boss_timing=None
                if clear or self.enabled!=enabled or timing_changed:self.generation+=1;self.bus.clear()
                self.enabled=enabled;self.bus.enabled=enabled
                return dict(enabled=enabled,generation=self.generation,epoch=self.epoch)

    @staticmethod
    def _record(kind,item):
        stamp=item.created
        if not finite(stamp,1e15):return None
        fields={'vision':('source','label'),'decision':('source','trigger','expression','operation','status'),
                'timeline':('category','title','detail','level'),'perf':('metric','unit')}[kind]
        row={name:debug_text(getattr(item,name,''),16384 if name=='expression' else 4096) for name in fields}
        row['truncated_fields']=[name for name in fields if type(getattr(item,name,None))is str and len(getattr(item,name))>(16384 if name=='expression' else 4096)]
        row.update(created=stamp,meta=plain_debug(getattr(item,'meta',None)))
        if kind=='vision':
            for name in ('x1','y1','x2','y2','score'):
                value=getattr(item,name,0)
                if not finite(value,1e8):return None
                row[name]=value
            if row['x2']<=row['x1'] or row['y2']<=row['y1']:return None
        if kind=='perf':
            value=item.value
            if not finite(value):return None
            row['value']=value
        return row

    def snapshot(self,now=None):
        now=time.time() if now is None else now;control=self._control()
        with control.lock if control is not None else nullcontext():
            with self.lock:
                self._sync(control);generation=self.generation;epoch=self.epoch
                source=self.bus.snapshot() if self.enabled and not self.closed else None
                enabled=self.enabled and not self.closed
        from .onedragon_runtime import UPSTREAM_REVISION
        result=dict(format=1,revision=getattr(self.ctx,'autozzz_runtime_revision',UPSTREAM_REVISION),enabled=enabled,generation=generation,epoch=epoch,created=now,discarded=self.discarded,
                    items={kind:[] for kind in KINDS},counts={},truncated={},window_identity=None,standard_size=[1920,1080])
        result['automatic_capture']=dict(saved=self.automatic_capture.saved,error=self.automatic_capture.error)
        if source is not None:
            for kind,(ttl,limit) in KINDS.items():
                rows=[];available=0;remaining=240_000
                for item in reversed(getattr(source,kind+'_items')):
                    stamp=item.created
                    if not finite(stamp,1e15) or not -1<=now-stamp<=ttl:continue
                    row=self._record(kind,item)
                    if row is None:continue
                    available+=1
                    size=len(json.dumps(row,ensure_ascii=False,allow_nan=False).encode('utf-8'))+2
                    if len(rows)<limit and size<=remaining:rows.append(row);remaining-=size
                result['items'][kind]=list(reversed(rows));result['counts'][kind]=available;result['truncated'][kind]=available>len(rows)
        with control.lock if control is not None else nullcontext():
            with self.lock:
                self._sync(control)
                if generation!=self.generation or epoch!=self.epoch or enabled!=self.enabled:
                    result.update(enabled=self.enabled,generation=self.generation,epoch=self.epoch,items={kind:[] for kind in KINDS},counts={},truncated={})
                controller=getattr(self.ctx,'controller',None);window=getattr(controller,'game_win',None)
                if window is not None and window.bound is not None and window.identity_valid():
                    result['window_identity']=dict(hwnd=window.bound.hwnd,pid=window.process_id)
                    result['standard_size']=[controller.standard_width,controller.standard_height]
                result['control']=control.status() if control is not None else {}
        return result

    def close(self):
        with self.lock:
            self.closed=True;self.enabled=False;self.bus.enabled=False;self.bus.clear()
            if self.boss_timing is not None:
                self.boss_timing.close()
                self.boss_timing=None
            for name,original in self.originals.items():setattr(self.bus,name,original)
            self.originals.clear()
            self.automatic_capture.close()
