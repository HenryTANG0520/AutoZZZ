"""Local presentation preferences; never participate in game or SDK configuration."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

PANELS = ('state', 'decision', 'timeline', 'perf')
METRICS = ('ocr_ms', 'yolo_ms', 'cv_pipeline_ms', 'operation_round_ms', 'overlay_refresh_ms')
DEFAULTS = {
    'version': 2, 'offset_x': 0, 'offset_y': 0, 'scale_x': 1., 'scale_y': 1.,
    'separate_panels': False, 'default_cover': False,
    'layers': {key:True for key in ('vision','state','decision','timeline','perf')},
    'sources': {key:True for key in ('ocr','yolo','cv','template')}, 'text_color': '#f2f2f2',
    'follow_interval_ms': 80, 'input_poll_interval_ms': 50,
    'capture_suffix': '_overlay', 'base_font_size': 11, 'base_opacity': 85,
    'hotkeys': {'toggle': 'Ctrl+Shift+F7', 'capture': 'Ctrl+Shift+F8'},
    'metrics': {key: True for key in METRICS},
    'panels': {key: {'x': 1500, 'y': 40 + index * 225, 'w': 400, 'h': 210,
                     'font_size': 11, 'opacity': 85, 'mode': 'game', 'desktop_x': 100+index*35,
                     'desktop_y': 100+index*45, 'collapsed': False, 'visible': True} for index, key in enumerate(PANELS)},
}


def validate(value):
    if isinstance(value,dict) and type(value.get('version'))is int and value['version']==1:
        legacy={'version','offset_x','offset_y','scale_x','scale_y','separate_panels','hotkeys','metrics','panels'}
        if set(value) not in (legacy,legacy-{'hotkeys'}):raise ValueError('旧覆盖设置字段无效。')
        migrated=deepcopy(DEFAULTS);migrated.update(deepcopy(value));migrated['version']=2
        migrated.setdefault('hotkeys',deepcopy(DEFAULTS['hotkeys']))
        if isinstance(value.get('panels'),dict):
            for key,panel in value['panels'].items():
                if key not in DEFAULTS['panels'] or not isinstance(panel,dict) or set(panel)!={'x','y','w','h','font_size'}:raise ValueError('旧面板设置无效。')
                migrated['panels'][key]={**DEFAULTS['panels'][key],**panel}
        value=migrated
    if not isinstance(value, dict) or set(value) != set(DEFAULTS) or type(value.get('version')) is not int or value.get('version') != 2:
        raise ValueError('覆盖设置格式或版本无效。')
    from .trace_hotkeys import HotkeyPoller
    if not isinstance(value['hotkeys'],dict) or set(value['hotkeys'])!={'toggle','capture'}:raise ValueError('快捷键字段无效。')
    HotkeyPoller(lambda _:0).configure(value['hotkeys'])
    result = deepcopy(value)
    def number(item, low, high, integer=False):
        if type(item) not in ((int,) if integer else (int, float)) or not low <= item <= high or not math.isfinite(item):
            raise ValueError('覆盖设置数值超出允许范围。')
    for key in ('offset_x', 'offset_y'): number(value[key], -1920, 1920, True)
    for key in ('scale_x', 'scale_y'): number(value[key], .5, 1.5)
    import re
    if type(value['text_color'])is not str or not re.fullmatch(r'#[0-9a-fA-F]{6}',value['text_color']):raise ValueError('文字颜色须为 #RRGGBB。')
    if type(value['capture_suffix'])is not str or not re.fullmatch(r'_[A-Za-z0-9_-]{1,39}',value['capture_suffix']):raise ValueError('截图后缀须以下划线开头，只含英文字母、数字、下划线和短横线。')
    number(value['base_font_size'],8,28,True);number(value['base_opacity'],5,100,True)
    number(value['follow_interval_ms'],30,1000,True);number(value['input_poll_interval_ms'],20,200,True)
    if type(value['default_cover'])is not bool:raise ValueError('覆盖显隐默认值无效。')
    for key,expected in (('layers',DEFAULTS['layers']),('sources',DEFAULTS['sources'])):
        if not isinstance(value[key],dict) or set(value[key])!=set(expected) or any(type(item)is not bool for item in value[key].values()):raise ValueError('覆盖来源/图层配置无效。')
    if type(value['separate_panels']) is not bool: raise ValueError('独立面板开关无效。')
    if not isinstance(value['metrics'], dict) or set(value['metrics']) != set(METRICS) or any(type(item) is not bool for item in value['metrics'].values()):
        raise ValueError('性能指标设置无效。')
    if not isinstance(value['panels'], dict) or set(value['panels']) != set(PANELS): raise ValueError('面板设置无效。')
    limits = {'x': (0, 1919), 'y': (0, 1079), 'w': (120, 1920), 'h': (60, 1080), 'font_size': (8, 28), 'opacity': (5, 100), 'desktop_x': (-32768,32767), 'desktop_y': (-32768,32767)}
    for panel in value['panels'].values():
        if not isinstance(panel, dict) or set(panel) != set(limits)|{'mode','collapsed','visible'}: raise ValueError('面板布局字段无效。')
        for key, (low, high) in limits.items(): number(panel[key], low, high, True)
        if panel['mode'] not in ('game','desktop') or type(panel['collapsed'])is not bool or type(panel['visible'])is not bool:raise ValueError('面板显示方式无效。')
    return result


def calibrated_box(row, standard, size, preferences):
    """Match source mapping: scale standard coordinates, then add screen-pixel offsets."""
    sw, sh = standard; width, height = size
    if sw <= 0 or sh <= 0: raise ValueError('标准画面尺寸无效。')
    return tuple(max(0, min(limit, row[key] * preferences['scale_' + axis] * ratio + preferences['offset_' + axis]))
                 for key, axis, ratio, limit in (('x1', 'x', width/sw, width), ('y1', 'y', height/sh, height),
                                                 ('x2', 'x', width/sw, width), ('y2', 'y', height/sh, height)))


def panel_bounds(panel, size):
    width, height = size
    w, h = min(width, round(panel['w'] * width/1920)), min(height, round(panel['h'] * height/1080))
    return max(0, min(width-w, round(panel['x']*width/1920))), max(0, min(height-h, round(panel['y']*height/1080))), w, h


class TracePreferences:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'AutoZZZ' / 'trace-overlay.json'
        self.revision = None

    def _read(self):
        try:
            if self.path.stat().st_size > 32768: raise ValueError('覆盖设置文件过大。')
            return self.path.read_bytes()
        except FileNotFoundError: return None

    def load(self):
        raw = self._read(); self.revision = hashlib.sha256(raw).hexdigest() if raw is not None else None
        return deepcopy(DEFAULTS) if raw is None else validate(json.loads(raw.decode('utf-8')))

    def save(self, value):
        value = validate(value)
        raw = self._read(); revision = hashlib.sha256(raw).hexdigest() if raw is not None else None
        if revision != self.revision: raise ValueError('覆盖设置已由其他窗口修改，请关闭后重新打开追踪窗口。')
        data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name); stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.revision = hashlib.sha256(data).hexdigest()
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
