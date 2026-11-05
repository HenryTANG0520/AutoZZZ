"""Template-tool hotkeys, read-only edge polling shared with the trace tool."""
from .trace_hotkeys import HotkeyPoller as BasePoller

MODIFIERS={'Ctrl':0x11,'Alt':0x12,'Shift':0x10,'Win':0x5B}
SPECIAL_KEYS={'backspace':8,'tab':9,'enter':13,'pause':19,'caps_lock':20,'esc':27,'space':32,
    'page_up':33,'page_down':34,'end':35,'home':36,'left':37,'up':38,'right':39,'down':40,
    'print_screen':44,'insert':45,'delete':46,'menu':93,'num_lock':144,'scroll_lock':145,
    'media_volume_mute':173,'media_volume_down':174,'media_volume_up':175,
    'media_next':176,'media_previous':177,'media_stop':178,'media_play_pause':179}


def parse_template_hotkey(value):
    value=formatted(value)
    if not value:return ()
    parts=value.split('+');key=parts[-1].lower();modifiers=parts[:-1]
    if not modifiers:raise ValueError('模板快捷键至少包含 Ctrl、Alt、Shift 或 Win。')
    if key in SPECIAL_KEYS:code=SPECIAL_KEYS[key]
    elif key.startswith('numpad_') and key[7:].isdigit() and len(key)==8:code=96+int(key[7:])
    elif key.startswith('vk_') and key[3:].isdigit() and 1<=int(key[3:])<=254:code=int(key[3:])
    elif key.startswith('f') and key[1:].isdigit() and 1<=int(key[1:])<=24:code=111+int(key[1:])
    elif len(key)==1 and key.isascii() and key.isalnum():code=ord(key.upper())
    else:raise ValueError('主键支持 F1—F24、字母数字、numpad_0—9、命名键或 vk_数字。')
    if code in (0x78,0x79):raise ValueError('F9 和 F10 保留用于暂停和停止。')
    if code in (16,17,18,91,92,160,161,162,163,164,165):raise ValueError('修饰键不能作为主键。')
    return tuple(sorted(MODIFIERS[name] for name in modifiers))+(code,)


class HotkeyPoller(BasePoller):
    """Template-only key names; no change to trace overlay hotkey policy."""
    def configure(self,bindings):
        parsed={key:parse_template_hotkey(value) for key,value in bindings.items()}
        used=[item for item in parsed.values() if item]
        if len(set(used))!=len(used):raise ValueError('模板快捷键不能重复。')
        self.bindings=parsed;self.held={key:self._down(combo) for key,combo in parsed.items()}

    def _down(self,combo):
        if not combo:return False
        down=lambda code:bool(self.read_key(code)&0x8000)
        return down(combo[-1]) and all((down(code) or code==91 and down(92))==(code in combo[:-1]) for code in MODIFIERS.values())

ACTIONS=('battle_1','battle_2','chain','quick','hollow','team','switch','expand')
DEFAULTS={key:f'Ctrl+{i+1}' for i,key in enumerate(ACTIONS[:6])}|{'switch':'Ctrl+0','expand':'Ctrl+9'}


def formatted(value):
    # Same normalization as the fixed source; allow both source and native display spelling.
    if type(value)is not str:raise ValueError('快捷键须为文字。')
    parts=[p.strip().lower() for p in value.split('+') if p.strip()]
    if not parts:return ''
    modifiers=[p for p in ('ctrl','alt','shift','win') if p in parts[:-1]]
    if len(parts)!=len(set(parts)) or any(p not in ('ctrl','alt','shift','win') for p in parts[:-1]):raise ValueError('格式如 Ctrl+Shift+1 或 Win+F13；留空禁用，F9/F10保留。')
    return '+'.join([p.title() for p in modifiers]+[parts[-1].upper()])


def validate_bindings(values):
    if type(values)is not dict or set(values)!=set(ACTIONS):raise ValueError('请填写六个截图键、切换窗口和悬浮展开键。')
    result={key:formatted(value) for key,value in values.items()}
    HotkeyPoller(lambda _:0).configure(result)
    return result
