"""Explicit debug screenshots, outside the recognition and game-input paths."""
from __future__ import annotations

import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile
import time
import uuid


def capture_directory(root):
    root=Path(root).absolute();directory=root/'autozzz'/'trace-captures'
    for part in (root,directory.parent,directory):
        if part.exists() and (part.is_symlink() or part.resolve()!=part):raise ValueError('截图目录不可重定向。')
    return directory


def atomic_bytes(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as stream:
            temporary=Path(stream.name);stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def capture_source(runtime,root,epoch,generation):
    """Use the source screenshot controller without observe(), input or activation."""
    import cv2
    snapshot=runtime.snapshot();control=runtime._control();controller=getattr(runtime.ctx,'controller',None)
    if control is None or controller is None or getattr(controller,'game_win',None) is None:return dict(waiting=True)
    geometry=None
    def window_geometry():
        rect=controller.game_win.win_rect
        return None if rect is None else (rect.x1,rect.y1,rect.x2,rect.y2)
    def valid():
        status=control.status() if control is not None else {}
        return (runtime.enabled and not runtime.closed and runtime.epoch==epoch and runtime.generation==generation
                and control.epoch==epoch and status.get('state') in ('running','paused','focus_wait')
                and getattr(controller.game_win,'is_win_active',False)
                and (status.get('state')!='running' or status.get('fresh_frame'))
                and controller.game_win.identity_valid() and (geometry is None or window_geometry()==geometry))
    if snapshot['epoch']!=epoch or snapshot['generation']!=generation or not valid():return dict(waiting=True)
    identity=snapshot['window_identity']
    if not identity:return dict(waiting=True)
    geometry=window_geometry()
    if geometry is None:return dict(waiting=True)
    from .onedragon_battle_state import battle_snapshot
    battle=battle_snapshot(runtime.ctx)
    start=time.time();image=controller.screenshot_controller.get_screenshot(independent=True,resize=False)
    if not valid() or image is None:return dict(waiting=True)
    if image.ndim!=3 or image.shape[2]!=3 or not image.size or image.shape[0]*image.shape[1]>36_000_000:
        raise ValueError('截图尺寸无效或超过 3600 万像素。')
    ok,encoded=cv2.imencode('.png',cv2.cvtColor(image,cv2.COLOR_RGB2BGR))
    if not ok:raise ValueError('无法编码游戏截图。')
    data=encoded.tobytes()
    if len(data)>80_000_000:raise ValueError('截图文件过大。')
    result=runtime.snapshot()
    if (not valid() or result['epoch']!=epoch or result['generation']!=generation or result['window_identity']!=identity):return dict(waiting=True)
    # Retain only records that existed by capture start; no future decision is painted on an older frame.
    paused=result.get('control',{}).get('state')!='running'
    result['capture_battle']={} if paused else battle
    for rows in result['items'].values():rows[:]=[] if paused else [row for row in rows if row['created']<=start]
    directory=capture_directory(root);path=directory/(uuid.uuid4().hex+'.png')
    atomic_bytes(path,data)
    if not valid():path.unlink(missing_ok=True);return dict(waiting=True)
    return dict(waiting=False,snapshot=result,capture=dict(path=str(path),sha256=hashlib.sha256(data).hexdigest(),
                width=int(image.shape[1]),height=int(image.shape[0]),epoch=epoch,generation=generation,paused=paused))


def read_capture(root,value):
    from PIL import Image
    if any(type(value.get(key))is not int or value[key]<=0 for key in ('width','height')) or value['width']*value['height']>36_000_000:raise ValueError('截图尺寸无效。')
    directory=capture_directory(root);path=Path(value['path'])
    if (path.parent!=directory or not re.fullmatch(r'[0-9a-f]{32}\.png',path.name)
            or path.is_symlink() or path.resolve()!=path or path.stat().st_nlink!=1
            or path.stat().st_size>80_000_000):raise ValueError('截图文件来源无效。')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=value['sha256']:raise ValueError('截图文件已变化。')
    image=Image.open(BytesIO(data))
    if image.format!='PNG' or image.size!=(value['width'],value['height']) or image.width*image.height>36_000_000:
        raise ValueError('截图尺寸或格式不匹配。')
    image.load();return image.convert('RGB'),path


def compose_overlay(image,snapshot,state,layers,sources,preferences,font_size=11,opacity=85,scale=1.,tk_scaling=1.333):
    """Render the same trace groups and calibrated boxes onto a copy of the source PNG."""
    from PIL import Image,ImageDraw,ImageFont
    from .onedragon_trace_ui import overlay_groups,vision_rows,COLORS
    from .trace_preferences import calibrated_box,panel_bounds
    from . import theme
    width,height=image.size;layer=Image.new('RGBA',image.size);draw=ImageDraw.Draw(layer)
    alpha=round(opacity*255/100)
    def font(size):
        path=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/'msyh.ttc'
        return ImageFont.truetype(str(path),max(8,round(size*tk_scaling))) if path.is_file() else ImageFont.load_default()
    face=font(font_size)
    if layers['vision']:
        for row in vision_rows(snapshot,sources):
            box=calibrated_box(row,snapshot['standard_size'],image.size,preferences)
            color=COLORS.get(row['source'],'#eeeeee');rgb=tuple(int(color[i:i+2],16) for i in (1,3,5))+(alpha,)
            draw.rectangle(box,outline=rgb,width=2);draw.text((box[0]+3,max(0,box[1]-font_size*tk_scaling)),row['label'][:60],font=face,fill=rgb)
    groups=overlay_groups(snapshot,state,layers,preferences)
    def wrap(lines,width,point_size):
        ink=ImageDraw.Draw(Image.new('RGBA',(1,1)));f=font(point_size);result=[]
        for line in lines:
            current=''
            for character in line:
                if current and ink.textlength(current+character,font=f)>max(1,width-16):result.append(current);current=''
                current+=character
            result.append(current)
        return result
    def panel(lines,bounds,point_size):
        x,y,w,h=bounds;tile=Image.new('RGBA',(max(1,w),max(1,h)),(16,24,32,alpha));ink=ImageDraw.Draw(tile)
        ink.rectangle((0,0,w-1,h-1),outline=(85,85,85,alpha));f=font(point_size);cursor=6
        for line in wrap(lines,w,point_size):
            ink.text((8,cursor),line,font=f,fill=(240,240,240,alpha));cursor+=round(point_size*tk_scaling+3)
            if cursor>=h:break
        layer.alpha_composite(tile,(x,y))
    if preferences['separate_panels']:
        for key,lines in groups.items():
            if lines:panel(lines,panel_bounds(preferences['panels'][key],image.size),preferences['panels'][key]['font_size'])
    else:
        lines=[line for group in groups.values() for line in group]
        if lines:
            x=max(12,width-min(round(400*scale),round(width*.35)));y=round(40*scale)
            panel(lines,(x,y,max(1,width-x-12),max(1,min(height-y-12,round(len(wrap(lines,max(1,width-x-12),font_size))*(font_size*tk_scaling+4)+24)))),font_size)
    return Image.alpha_composite(image.convert('RGBA'),layer).convert('RGB')
