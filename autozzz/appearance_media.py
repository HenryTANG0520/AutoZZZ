"""Bounded managed backgrounds and one cooperative local-media decoder."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from urllib.parse import urlsplit

from PIL import Image

IMAGE_EXTENSIONS = {'.png','.jpg','.jpeg','.webp','.gif','.bmp'}
VIDEO_EXTENSIONS = {'.mp4','.webm','.avi','.mov','.mkv'}
IMAGE_LIMIT = 20 * 1024 * 1024
VIDEO_LIMIT = 160 * 1024 * 1024
ENDPOINT = 'https://hyp-api.mihoyo.com/hyp/hyp-connect/api/'
PARAMS = '?launcher_id=jGHBHlcOq1&language=zh-cn'


class _HttpSession:
    """Small stdlib transport: the packaged Tk process has no requests dependency."""
    def __init__(self):
        import urllib.request
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        self.opener=urllib.request.build_opener(NoRedirect())

    def get(self,url,**kwargs):
        import urllib.error
        import urllib.request
        try:stream=self.opener.open(urllib.request.Request(url,headers={'User-Agent':'AutoZZZ/1.0'}),timeout=8)
        except urllib.error.HTTPError as exc:
            if exc.code not in (301,302,303,307,308):raise
            stream=exc
        class Response:
            def __init__(self):self.status_code=stream.code;self.headers=stream.headers
            def raise_for_status(self):
                if self.status_code>=400:raise OSError(f'官方背景 HTTP {self.status_code}')
            def iter_content(self,size):
                while block:=stream.read(size):yield block
            def close(self):stream.close()
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        return Response()

    def close(self):pass


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError('背景操作已取消。')


def validate_url(url):
    if not isinstance(url,str) or len(url)>4096:
        raise ValueError('官方背景链接无效。')
    parsed = urlsplit(url)
    domains = ('mihoyo.com','miyoushe.com','hoyoverse.com','hoyolab.com','hoyocdn.com','yuanshen.com')
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None,443)
            or not any(parsed.hostname == host or (parsed.hostname or '').endswith('.'+host) for host in domains)):
        raise ValueError('背景只能从官方 HTTPS 资源域下载。')
    return url


def official_asset(metadata, mode):
    """Match the fixed upstream launcher metadata, without executing web content."""
    data = metadata['data']
    if mode == 'version_poster':
        game = next(item for item in data['games'] if item.get('biz') == 'nap_cn')
        return validate_url(game['display']['background']['url'])
    game = next(item for item in data['game_info_list'] if item.get('game',{}).get('biz') == 'nap_cn')
    backgrounds = game['backgrounds']
    if mode == 'static':
        return validate_url(backgrounds[0]['background']['url'])
    if mode == 'dynamic':
        entry = next(item for item in backgrounds if item.get('type') == 'BACKGROUND_TYPE_VIDEO')
        return validate_url(entry['video']['url'])
    raise ValueError('请选择官方背景类型。')


def _response(session,url):
    # Validate every redirect rather than letting requests follow arbitrary hosts.
    for _ in range(4):
        validate_url(url)
        response = session.get(url,stream=True,timeout=(5,8),allow_redirects=False)
        if response.status_code in (301,302,303,307,308):
            from urllib.parse import urljoin
            next_url = urljoin(url,response.headers.get('Location',''))
            response.close(); url=next_url
            continue
        try:response.raise_for_status()
        except Exception:
            response.close();raise
        return response
    raise ValueError('官方背景跳转次数过多。')


def _chunks(response,limit,cancel):
    size=0
    declared=response.headers.get('Content-Length')
    if declared is not None and int(declared)>limit:
        raise ValueError('背景资源超过允许大小。')
    for block in response.iter_content(64*1024):
        _cancelled(cancel)
        size+=len(block)
        if size>limit:
            raise ValueError('背景资源超过允许大小。')
        yield block


def probe_media(path):
    path=Path(path)
    suffix=path.suffix.lower()
    kind='image' if suffix in IMAGE_EXTENSIONS else 'video' if suffix in VIDEO_EXTENSIONS else None
    if kind is None or path.stat().st_size > (IMAGE_LIMIT if kind=='image' else VIDEO_LIMIT):
        raise ValueError('背景格式不支持或文件过大（图片 20 MB，视频 160 MB）。')
    if kind=='image':
        with Image.open(path) as source:
            if source.width*source.height>40_000_000:
                raise ValueError('背景图片尺寸过大。')
            source.verify()
    else:
        import cv2
        capture=cv2.VideoCapture(str(path))
        try:
            if not capture.isOpened() or capture.get(cv2.CAP_PROP_FRAME_WIDTH)*capture.get(cv2.CAP_PROP_FRAME_HEIGHT)>16_000_000:
                raise ValueError('无法解码背景视频或视频尺寸过大。')
            ok,frame=capture.read()
            if not ok or frame is None:
                raise ValueError('背景视频没有可解码的帧。')
        finally:
            capture.release()
    return kind


def import_media(source,store,cancel=None):
    """Copy exactly one selected file; keep existing configuration on any failure."""
    source=Path(source)
    kind=probe_media(source)
    store.asset_dir.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=store.asset_dir,suffix=source.suffix.lower(),delete=False) as output,source.open('rb') as stream:
            temporary=Path(output.name);digest=hashlib.sha256();size=0
            while block:=stream.read(64*1024):
                _cancelled(cancel);size+=len(block)
                if size>(IMAGE_LIMIT if kind=='image' else VIDEO_LIMIT):
                    raise ValueError('复制时背景文件变大，已停止。')
                digest.update(block);output.write(block)
            output.flush();os.fsync(output.fileno())
        probe_media(temporary)
        _cancelled(cancel)
        sha=digest.hexdigest();name=sha+source.suffix.lower()
        os.replace(temporary,store.asset_dir/name)
        return dict(file=name,kind=kind,sha256=sha)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def download_official(mode,store,cancel=None,session=None):
    if mode not in ('version_poster','static','dynamic'):
        raise ValueError('请选择官方背景类型。')
    own=session is None;session=session or _HttpSession()
    temporary=None
    try:
        _cancelled(cancel)
        endpoint='getGames' if mode=='version_poster' else 'getAllGameBasicInfo'
        with _response(session,ENDPOINT+endpoint+PARAMS) as response:
            raw=b''.join(_chunks(response,2*1024*1024,cancel))
        try:
            url=official_asset(json.loads(raw),mode)
        except (KeyError,TypeError,StopIteration) as exc:
            raise ValueError('官方接口尚未提供此背景。') from exc
        suffix=Path(urlsplit(url).path).suffix.lower()
        allowed=VIDEO_EXTENSIONS if mode=='dynamic' else IMAGE_EXTENSIONS
        if suffix not in allowed:
            raise ValueError('官方资源扩展名与背景类型不符。')
        limit=VIDEO_LIMIT if mode=='dynamic' else IMAGE_LIMIT
        store.asset_dir.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=store.asset_dir,suffix=suffix,delete=False) as output:
            temporary=Path(output.name)
            with _response(session,url) as response:
                for block in _chunks(response,limit,cancel):
                    output.write(block)
        return import_media(temporary,store,cancel)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if own:
            session.close()


def managed_path(store,asset):
    if not asset:
        return None
    # Schema owns the filename. Still refuse a replaced/symlinked cache entry.
    path=store.asset_dir/asset['file']
    if path.is_symlink() or path.resolve().parent != store.asset_dir.resolve():
        raise ValueError('背景缓存路径无效。')
    if not path.is_file():
        raise ValueError('背景缓存文件不存在，请重新选择或下载。')
    digest=hashlib.sha256()
    if path.stat().st_size>VIDEO_LIMIT:
        raise ValueError('背景缓存过大。')
    with path.open('rb') as stream:
        while block:=stream.read(64*1024):digest.update(block)
    if digest.hexdigest()!=asset['sha256']:
        raise ValueError('背景缓存校验失败，请重新选择或下载。')
    return path


class BackgroundPlayer:
    """One decoder, one latest frame; all Tk work stays on the main thread."""
    def __init__(self):
        self._condition=threading.Condition();self._stop=False;self._visible=False
        self._request=None;self._generation=0;self._frame=None;self.frame_number=0;self.error=None
        self.worker=threading.Thread(target=self._run,name='AutoZZZ-background',daemon=False)
        self.worker.start()

    def load(self,path,kind):
        with self._condition:
            self._generation+=1;self._request=(self._generation,Path(path),kind) if path else (self._generation,None,None)
            self._frame=None;self.error=None;self._condition.notify_all()

    def visible(self,value):
        with self._condition:
            self._visible=bool(value);self._condition.notify_all()

    def frame(self):
        with self._condition:return self._frame

    def close(self):
        with self._condition:self._stop=True;self._condition.notify_all()

    def _publish(self,frame,generation):
        with self._condition:
            if generation==self._generation and not self._stop:
                self._frame=frame;self.frame_number+=1

    def _run(self):
        capture=None;generation=-1;image=None;fps=8;native_fps=8;skip_budget=0
        try:
            while True:
                with self._condition:
                    if self._stop:return
                    request=self._request
                    if request is None or (request[0]==generation and (not self._visible or capture is None)):
                        self._condition.wait(.2);continue
                if request[0]!=generation:
                    if capture is not None:capture.release();capture=None
                    generation,path,kind=request;image=None
                    if path is None:continue
                    try:
                        if kind=='image':
                            with Image.open(path) as source:
                                image=source.convert('RGB');image.thumbnail((1600,1000))
                            self._publish(image,generation);continue
                        import cv2
                        capture=cv2.VideoCapture(str(path))
                        if not capture.isOpened():raise ValueError('背景视频无法打开。')
                        value=capture.get(cv2.CAP_PROP_FPS)
                        native_fps=min(240,max(1,value)) if math.isfinite(value) and value>0 else 8
                        fps=min(12,native_fps);skip_budget=0
                    except Exception as exc:
                        if capture is not None:capture.release();capture=None
                        with self._condition:self.error=str(exc)
                        continue
                with self._condition:
                    if not self._visible:continue
                if capture is not None:
                    started=time.monotonic();ok,frame=capture.read()
                    if not ok:
                        import cv2
                        capture.set(cv2.CAP_PROP_POS_FRAMES,0);ok,frame=capture.read()
                    if not ok:
                        capture.release();capture=None
                        with self._condition:self.error='背景视频解码已停止。'
                        continue
                    import cv2
                    image=Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB));image.thumbnail((1280,800))
                    self._publish(image,generation)
                    # Downsample display/decode work while retaining wall-clock
                    # playback speed for ordinary 24/30/60 fps backgrounds.
                    skip_budget+=native_fps/fps-1
                    skips=int(skip_budget);skip_budget-=skips
                    for _ in range(skips):capture.grab()
                    with self._condition:self._condition.wait(max(.001,1/fps-(time.monotonic()-started)))
        finally:
            if capture is not None:capture.release()
