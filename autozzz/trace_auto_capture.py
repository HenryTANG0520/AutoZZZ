"""Append an overlay copy to SDK operation screenshots without taking a new frame."""
from collections import OrderedDict
from functools import wraps
from io import BytesIO
from pathlib import Path
import threading
import time
import weakref


class AutomaticOverlayCapture:
    def __init__(self,runtime):
        self.runtime=runtime;self.originals=[];self.frames=OrderedDict();self.lock=threading.RLock()
        self.saved=0;self.error=''

    def remember(self,operation,image):
        if operation.ctx is not self.runtime.ctx or image is None or not self.runtime.enabled or self.runtime.closed:return
        snapshot=self.runtime.snapshot()
        from .onedragon_battle_state import battle_snapshot
        snapshot['capture_battle']=battle_snapshot(self.runtime.ctx)
        with self.lock:
            self.frames[id(image)]=(weakref.ref(image),snapshot,time.time())
            self.frames.move_to_end(id(image))
            while len(self.frames)>8:self.frames.popitem(last=False)

    def append(self,image,path,preferences):
        if not preferences['patched_capture_enabled']:return None
        with self.lock:frame=self.frames.get(id(image))
        if frame is None or frame[0]() is not image:return None
        snapshot,stamp=frame[1:]
        control=self.runtime._control()
        if (control is None or self.runtime.closed or not self.runtime.enabled
                or control.epoch!=snapshot['epoch'] or control.task_epoch!=snapshot['epoch']
                or control.status().get('state')!='running' or not control.status().get('fresh_frame')
                or not 0<=time.time()-stamp<=1.8):return None
        current=self.runtime.snapshot()
        if current['window_identity']!=snapshot['window_identity'] or not snapshot['window_identity']:return None
        if (image.ndim!=3 or image.shape[2]!=3 or image.shape[0]*image.shape[1]>36_000_000
                or list(image.shape[:2][::-1])!=snapshot['standard_size']):return None
        from .trace_capture import compose_overlay,atomic_bytes
        from PIL import Image
        # Draw only data already present in the saved frame's observation; no new capture/input.
        for rows in snapshot['items'].values():rows[:]=[row for row in rows if row['created']<=stamp]
        result=compose_overlay(Image.fromarray(image.copy()),snapshot,{'battle':snapshot.get('capture_battle',{})},preferences['layers'],
            preferences['sources'],preferences,font_size=preferences['base_font_size'],opacity=preferences['base_opacity'])
        stream=BytesIO();result.save(stream,format='PNG')
        if control.epoch!=snapshot['epoch'] or control.status().get('state')!='running':return None
        path=Path(path);target=path.with_name(path.stem+preferences['capture_suffix']+'.png')
        if target.exists() or target.is_symlink():return None
        atomic_bytes(target,stream.getvalue());self.saved+=1
        return target

    def install(self):
        import sys
        # The SDK module is already loaded in the worker; read-only fake buses do not load it.
        if 'one_dragon.base.operation.operation' not in sys.modules:return
        from one_dragon.base.operation.operation import Operation
        from one_dragon.utils import debug_utils
        original_frame=Operation.screenshot;original_save=debug_utils.save_debug_image
        @wraps(original_frame)
        def screenshot(operation,*args,**kwargs):
            image=original_frame(operation,*args,**kwargs)
            try:self.remember(operation,image)
            except Exception as exc:self.error=str(exc)[:512]
            return image
        @wraps(original_save)
        def save(image,*args,**kwargs):
            result=original_save(image,*args,**kwargs)
            try:
                from .trace_preferences import TracePreferences
                self.append(image,debug_utils.get_debug_image_path(result),TracePreferences().load())
            except Exception as exc:
                # Diagnostics must not change the original screenshot or running operation result.
                self.error=str(exc)[:512]
            return result
        for obj,name,original,replacement in ((Operation,'screenshot',original_frame,screenshot),
                    (debug_utils,'save_debug_image',original_save,save)):
            self.originals.append((obj,name,original));setattr(obj,name,replacement)

    def close(self):
        for obj,name,original in reversed(self.originals):setattr(obj,name,original)
        self.originals.clear();self.frames.clear()
