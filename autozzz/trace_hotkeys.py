"""Read-only edge polling; does not register, swallow or send any keyboard input."""
from __future__ import annotations

MODIFIERS={'Ctrl':0x11,'Shift':0x10,'Alt':0x12}


def parse_hotkey(value):
    if type(value)is not str:raise ValueError('快捷键须为文字。')
    value=value.strip()
    if not value:return ()
    parts=value.split('+');modifiers=parts[:-1];key=parts[-1]
    if len(set(modifiers))!=len(modifiers) or any(part not in MODIFIERS for part in modifiers):raise ValueError('快捷键格式示例：Ctrl+Shift+F7。')
    if key in ('F9','F10'):raise ValueError('F9 和 F10 保留用于暂停和停止。')
    if key.startswith('F') and key[1:].isdigit() and 1<=int(key[1:])<=12:code=0x70+int(key[1:])-1
    elif len(key)==1 and key.isascii() and key.isalnum():code=ord(key.upper())
    else:raise ValueError('快捷键主键须为 F1—F12、英文字母或数字。')
    if code in (0x78,0x79):raise ValueError('F9 和 F10 保留用于暂停和停止。')
    if not modifiers:raise ValueError('覆盖快捷键至少包含 Ctrl、Shift、Alt 中的一项。')
    return tuple(sorted(MODIFIERS[part] for part in modifiers))+(code,)


class HotkeyPoller:
    def __init__(self,read_key):self.read_key=read_key;self.held={};self.bindings={}
    def configure(self,bindings):
        parsed={key:parse_hotkey(value) for key,value in bindings.items()}
        used=[item for item in parsed.values() if item]
        if len(set(used))!=len(used):raise ValueError('覆盖显隐和截图快捷键不能相同。')
        self.bindings=parsed;self.held={key:self._down(combo) for key,combo in parsed.items()}
    def _down(self,combo):
        return bool(combo) and all(bool(self.read_key(code)&0x8000) for code in combo) and all(
            bool(self.read_key(code)&0x8000)==(code in combo[:-1]) for code in MODIFIERS.values())
    def poll(self,active):
        actions=[]
        for key,combo in self.bindings.items():
            down=self._down(combo)
            if active and down and not self.held.get(key,True):actions.append(key)
            self.held[key]=down
        return actions
    def close(self):self.bindings={};self.held={}
