"""Presentation-only preferences shared by Tk and independent native hosts."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

DEFAULTS = dict(version=1, theme='dark', accent='#f4df00', background='none',
                language='zh', assets={})
BACKGROUND_MODES = ('none', 'version_poster', 'static', 'dynamic', 'custom')


def validate(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULTS) or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('外观设置格式或版本无效。')
    if value['theme'] not in ('dark', 'light', 'system') or value['language'] not in ('auto', 'zh', 'en'):
        raise ValueError('主题或语言无效。')
    if value['background'] not in BACKGROUND_MODES:
        raise ValueError('背景类型无效。')
    if type(value['accent']) is not str or not re.fullmatch(r'#[0-9a-fA-F]{6}', value['accent']):
        raise ValueError('主题色须为 #RRGGBB。')
    if not isinstance(value['assets'], dict) or not set(value['assets']) <= set(BACKGROUND_MODES[1:]):
        raise ValueError('背景资源记录无效。')
    for asset in value['assets'].values():
        if (not isinstance(asset, dict) or set(asset) != {'file', 'kind', 'sha256'}
                or asset['kind'] not in ('image', 'video')
                or type(asset['file']) is not str or not re.fullmatch(r'[0-9a-f]{64}\.(png|jpg|jpeg|webp|gif|bmp|mp4|webm|avi|mov|mkv)', asset['file'])
                or asset['sha256'] != asset['file'].split('.')[0]):
            raise ValueError('背景资源必须是本地受管的单个媒体文件。')
    result = deepcopy(value)
    result['accent'] = result['accent'].lower()
    return result


class AppearancePreferences:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'AutoZZZ' / 'appearance.json'
        self.revision = None

    @property
    def asset_dir(self):
        return self.path.parent / 'appearance-assets'

    def _read(self):
        try:
            if self.path.stat().st_size > 16384:
                raise ValueError('外观设置文件过大。')
            return self.path.read_bytes()
        except FileNotFoundError:
            return None

    def load(self):
        raw = self._read()
        self.revision = hashlib.sha256(raw).hexdigest() if raw is not None else None
        return deepcopy(DEFAULTS) if raw is None else validate(json.loads(raw.decode('utf-8')))

    def save(self, value):
        value = validate(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix('.json.lock')
        # Fail closed for another writer or a leftover lock; never delete somebody else's lock.
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ValueError('外观设置正在写入，请稍后重试。') from exc
        temporary = None
        try:
            os.close(descriptor)
            raw = self._read()
            revision = hashlib.sha256(raw).hexdigest() if raw is not None else None
            if revision != self.revision:
                raise ValueError('外观设置已由其他窗口修改，请重新打开。')
            data = json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.revision = hashlib.sha256(data).hexdigest()
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            lock.unlink(missing_ok=True)


def effective_preferences():
    """Read without rewriting malformed/older files; presentation falls back safely."""
    try:
        return AppearancePreferences().load()
    except (OSError, ValueError, TypeError):
        return deepcopy(DEFAULTS)


def system_dark_theme():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            return winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] == 0
    except (ImportError, OSError):
        return True


def luminance(color):
    channels = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    rgb = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    return sum(c * weight for c, weight in zip(rgb, (.2126, .7152, .0722)))


def contrast(first, second):
    low, high = sorted((luminance(first), luminance(second)))
    return (high + .05) / (low + .05)


def effective_appearance(value=None, *, system_dark=None):
    prefs = effective_preferences() if value is None else validate(value)
    dark = (system_dark_theme() if system_dark is None else bool(system_dark)) if prefs['theme'] == 'system' else prefs['theme'] == 'dark'
    requested = prefs['accent']
    # The accent is also small text on cards. Darken/lighten it until it meets
    # 4.5:1 on BOTH surfaces; save the original choice for the opposite theme.
    surfaces = ('#343434', '#161616') if dark else ('#dededb', '#f9f9f6')
    rgb = [int(requested[i:i+2], 16) for i in (1, 3, 5)]
    accent = requested
    for _ in range(256):
        if min(contrast(accent, surface) for surface in surfaces) >= 6.5:
            break
        rgb = [min(255, c + 1) if dark else max(0, c - 1) for c in rgb]
        accent = '#' + ''.join(f'{c:02x}' for c in rgb)
    ink = '#0c0c0c' if contrast(accent, '#0c0c0c') >= contrast(accent, '#ffffff') else '#ffffff'
    return dict(mode='dark' if dark else 'light', accent=accent, accent_text=ink, requested_accent=requested)
