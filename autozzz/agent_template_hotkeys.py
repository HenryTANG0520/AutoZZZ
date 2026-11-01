"""Template-tool hotkeys, read-only edge polling shared with the trace tool."""
from .trace_hotkeys import HotkeyPoller

ACTIONS=('battle_1','battle_2','chain','quick','hollow','team','switch','expand')
DEFAULTS={key:f'Ctrl+{i+1}' for i,key in enumerate(ACTIONS[:6])}|{'switch':'Ctrl+0','expand':'Ctrl+9'}


def formatted(value):
    # Same normalization as the fixed source; allow both source and native display spelling.
    if type(value)is not str:raise ValueError('快捷键须为文字。')
    parts=[p.strip().lower() for p in value.split('+') if p.strip()]
    if not parts:return ''
    modifiers=[p for p in ('ctrl','alt','shift') if p in parts[:-1]]
    if len(parts)!=len(set(parts)) or any(p not in ('ctrl','alt','shift') for p in parts[:-1]):raise ValueError('格式如 Ctrl+Shift+1；留空禁用，F9/F10保留。')
    return '+'.join([p.title() for p in modifiers]+[parts[-1].upper()])


def validate_bindings(values):
    if type(values)is not dict or set(values)!=set(ACTIONS):raise ValueError('请填写六个截图键、切换窗口和悬浮展开键。')
    result={key:formatted(value) for key,value in values.items()}
    HotkeyPoller(lambda _:0).configure(result)
    return result
