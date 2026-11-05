"""Optional OneDragon shortcuts backed by passive Windows input hooks."""
from __future__ import annotations

from collections import deque
import ctypes
from dataclasses import dataclass
import threading
import time

from .agent_template_hotkeys import MODIFIERS
from .global_capture import parse_capture_hotkey
from .global_key_events import F10, MODIFIER_CODES, MODIFIER_GROUPS, KeyboardEdgeQueue, ReadOnlyKeyboardEvents


F9 = 0x78
_FIXED_CODES = {'f9': F9, 'f10': F10}
_RESERVED = {'f9': F9, 'vk_120': F9, 'f10': F10, 'vk_121': F10}
_STOP_ACTIONS = {'f10', 'stop'}
_MOUSE_NAMES = {'mouse_left': 1, 'mouse_right': 2, 'mouse_middle': 4, 'mouse_x1': 5, 'mouse_x2': 6}
MOUSE_CODES = frozenset(_MOUSE_NAMES.values())
_MODIFIER_NAMES = {
    'shift': 16, 'shift_l': 160, 'shift_r': 161,
    'ctrl': 17, 'ctrl_l': 162, 'ctrl_r': 163,
    'alt': 18, 'alt_l': 164, 'alt_r': 165, 'alt_gr': 165,
    'win': 91, 'cmd': 91, 'cmd_l': 91, 'cmd_r': 92,
}
_GENERIC_MODIFIERS = {group: codes for group, codes in MODIFIER_GROUPS.items() if group != 91}
_SIDE_GROUPS = {code: group for group, codes in MODIFIER_GROUPS.items() for code in codes}


def _key_parts(value):
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError('运行快捷键须为不超过 128 字符的文字。')
    value = value.strip()
    if value in ('', '+'):
        return (), value
    if value.endswith('+') and value[:-1].rstrip().endswith('+'):
        # Ctrl++ is a chord with the literal plus key.
        modifiers = value[:-1].rstrip()[:-1].split('+')
        primary = '+'
    else:
        *modifiers, primary = value.split('+')
    names = [name.strip().title() for name in modifiers]
    if not primary.strip() or any(name not in MODIFIERS for name in names) or len(set(names)) != len(names):
        raise ValueError('运行快捷键格式如 F8、Ctrl+Shift+P 或 Ctrl++。')
    return tuple(name for name in MODIFIERS if name in names), primary.strip()


def display_run_key(value):
    """Display source spellings without losing literals or hiding invalid saved values."""
    try:
        names, primary = _key_parts(value)
    except ValueError:
        return value
    if primary.isascii():
        primary = primary.upper()
    return '+'.join((*names, primary))


def _character_chord(value):
    key_scan = ctypes.WinDLL('user32', use_last_error=True).VkKeyScanW
    key_scan.argtypes = [ctypes.c_wchar]
    key_scan.restype = ctypes.c_short
    key = key_scan(value)
    shift_state = key >> 8
    if key == -1 or shift_state & ~7:
        raise ValueError('当前 Windows 键盘布局无法输入这个快捷键字符。')
    modifiers = {code for bit, code in ((1, 16), (2, 17), (4, 18)) if shift_state & bit}
    return modifiers, key & 0xff


def parse_run_key(value, action):
    """Read one saved binding. A blank value removes the optional alias."""
    if action not in ('start', 'stop'):
        raise ValueError('运行快捷键类型无效。')
    names, primary = _key_parts(value)
    if not primary:
        return ()
    key = primary.lower()
    reserved = _RESERVED.get(key)
    if reserved is not None:
        expected = F9 if action == 'start' else F10
        if names or reserved != expected:
            raise ValueError('F9 用于开始、暂停和继续，F10 用于停止。')
        return (reserved,)
    modifiers = {MODIFIERS[name] for name in names}
    numeric = int(key[3:]) if key.startswith('vk_') and key[3:].isascii() and key[3:].isdigit() else None
    if key in _MODIFIER_NAMES or numeric in (*MODIFIER_CODES, *_GENERIC_MODIFIERS):
        if modifiers:
            raise ValueError('修饰键可单独使用，组合键须有其他主键。')
        return (_MODIFIER_NAMES[key] if key in _MODIFIER_NAMES else numeric,)
    if key in _MOUSE_NAMES or numeric in MOUSE_CODES:
        code = _MOUSE_NAMES[key] if key in _MOUSE_NAMES else numeric
    elif len(primary) == 1 and not (primary.isascii() and primary.isalnum()):
        required, code = _character_chord(primary)
        modifiers.update(required)
    else:
        code = parse_capture_hotkey(primary)[-1]
    return tuple(sorted(modifiers)) + (code,)


def run_keys_conflict(first, second):
    if not first or not second:
        return False
    if len(first) == len(second) == 1:
        left = _GENERIC_MODIFIERS.get(first[0], first)
        right = _GENERIC_MODIFIERS.get(second[0], second)
        return bool(set(left).intersection(right))
    return first == second


def run_bindings(start='f9', stop='f10'):
    """Return the fixed keys and any distinct aliases configured for this profile."""
    start_chord = parse_run_key(start, 'start')
    stop_chord = parse_run_key(stop, 'stop')
    if run_keys_conflict(start_chord, stop_chord):
        raise ValueError('开始与停止快捷键不能相同。')
    bindings = {'f9': (F9,), 'f10': (F10,)}
    if start_chord and start_chord != (F9,):
        bindings['start'] = start_chord
    if stop_chord and stop_chord != (F10,):
        bindings['stop'] = stop_chord
    return bindings


@dataclass(frozen=True)
class RunKeyEdge:
    action: str
    down: bool
    created: float
    foreground: int
    code: int
    generation: int
    press_id: int
    source: str
    injected: bool
    lower_integrity_injected: bool
    os_time: int


class RunKeyQueue:
    """Track selected keys only. Releases identify the press that qualified."""
    def __init__(self, limit=32, *, allow_injected=False, clock=time.monotonic, fixed_stop_event=None):
        if type(limit) is not int or limit < 1:
            raise ValueError('运行快捷键事件队列容量无效。')
        self._lock = threading.RLock()
        self._limit = limit
        self._allow_injected = allow_injected
        self._fixed_stop_event = fixed_stop_event
        self._clock = clock
        self._events = deque()
        self._fixed = {}
        self._chords = {}
        self._modifier_actions = {}
        self._primaries = set()
        self._pressed = set()
        self._active = {}
        self._generation = 0
        self._configured_at = 0.
        self._press_id = 0
        self._dropped = 0
        self._closed = False

    def configure(self, bindings, read_key=None):
        fixed, chords, modifier_actions = {}, {}, {}
        if run_keys_conflict(bindings.get('start', ()), bindings.get('stop', ())):
            raise ValueError('开始与停止快捷键不能相同。')
        for action, chord in bindings.items():
            if action in _FIXED_CODES and chord == (_FIXED_CODES[action],):
                fixed[chord[0]] = action
                continue
            if (action not in ('start', 'stop') or not chord or len(chord) > 5
                    or any(modifier not in MODIFIER_GROUPS for modifier in chord[:-1])
                    or len(set(chord[:-1])) != len(chord[:-1])):
                raise ValueError('运行快捷键事件配置无效。')
            code = chord[-1]
            if type(code) is not int or not 1 <= code <= 254 or code in (F9, F10):
                raise ValueError('运行快捷键事件配置无效。')
            if code in MODIFIER_CODES or code in _GENERIC_MODIFIERS:
                if len(chord) != 1:
                    raise ValueError('修饰键可单独使用，组合键须有其他主键。')
                modifier_actions[code] = action
            else:
                chords[(frozenset(chord[:-1]), code)] = action
        primaries = set(fixed) | {code for _, code in chords}
        with self._lock:
            if self._closed:
                raise RuntimeError('运行快捷键监听已关闭。')
            self._pressed = set() if read_key is None else {
                code for code in (*MODIFIER_CODES, *primaries) if read_key(code) & 0x8000
            }
            self._events.clear()
            self._active.clear()
            self._fixed, self._chords, self._primaries = fixed, chords, primaries
            self._modifier_actions = modifier_actions
            self._configured_at = self._clock()
            self._generation += 1
            return self._generation

    def watches_mouse(self):
        with self._lock:
            return not self._closed and not MOUSE_CODES.isdisjoint(self._primaries)

    def _modifier_key(self, code):
        if code in self._modifier_actions:
            return code
        group = _SIDE_GROUPS[code]
        generic_codes = _GENERIC_MODIFIERS.get(group, ())
        if group in self._modifier_actions and not any(
                side != code and side in self._pressed for side in generic_codes):
            return group  # One press while either side of a generic modifier is held.
        return None

    def _modifiers_down(self):
        return frozenset(group for group, keys in MODIFIER_GROUPS.items() if any(key in self._pressed for key in keys))

    def feed(self, code, down, *, foreground, created=None, flags=0, scan=0, os_time=0, source='hook'):
        injected = bool(flags & 0x12)
        if injected and not self._allow_injected:
            return
        code = KeyboardEdgeQueue.normalized(code, scan, flags)
        created = self._clock() if created is None else created
        with self._lock:
            if self._closed or code not in MODIFIER_CODES and code not in self._primaries:
                return
            if down == (code in self._pressed):
                return
            if down:
                self._pressed.add(code)
            else:
                self._pressed.discard(code)
            # A callback can begin while a new binding is being installed.
            # Remember its physical level, but do not assign it to the new binding.
            if created < self._configured_at:
                return
            event_key = self._modifier_key(code) if code in MODIFIER_CODES else code
            if event_key is None:
                return
            if down:
                action = self._fixed.get(code)
                if action is None:
                    modifiers = self._modifiers_down()
                    if code in MODIFIER_CODES:
                        if not modifiers.difference((_SIDE_GROUPS[code],)):
                            action = self._modifier_actions.get(event_key)
                    else:
                        action = self._chords.get((modifiers, code))
                if action is None:
                    return
                self._press_id += 1
                press_id = self._press_id
                self._active[event_key] = (action, press_id, code)
            else:
                press = self._active.pop(event_key, None)
                if press is None:
                    return
                action, press_id, code = press
            if action == 'f10' and self._fixed_stop_event is not None:
                if down and source == 'hook' and not injected:
                    self._fixed_stop_event.set()
                return
            self._append(RunKeyEdge(action, down, created, int(foreground or 0), code, self._generation,
                                    press_id, source, injected, bool(flags & 2), os_time))

    def _append(self, event):
        if len(self._events) == self._limit:
            self._dropped += 1
            for index, pending in enumerate(self._events):
                if not pending.down or pending.action not in _STOP_ACTIONS:
                    del self._events[index]
                    break
            else:
                if not event.down or event.action not in _STOP_ACTIONS:
                    return  # Never displace a queued stop with a start or release.
                self._events.popleft()
        self._events.append(event)

    def poll_levels(self, read_key, foreground, *, mouse_only=False):
        """Fallback snapshots only see held levels and cannot identify injection."""
        with self._lock:
            if self._closed:
                return
            codes = (sorted(self._primaries & MOUSE_CODES) if mouse_only else (*MODIFIER_CODES, *sorted(self._primaries)))
            held = {code for code in codes if read_key(code) & 0x8000}
            changed = (held ^ self._pressed) & set(codes)
            if changed:
                hwnd = foreground()
                created = self._clock()
                for code in codes:  # Match each newly pressed primary against the sampled modifier levels.
                    if code in changed:
                        self.feed(code, code in held, foreground=hwnd, created=created, source='polling')

    def drain(self):
        with self._lock:
            events = list(self._events)
            self._events.clear()
            return events

    def status(self):
        with self._lock:
            return dict(generation=self._generation, queue_size=len(self._events), queue_dropped=self._dropped)

    def close(self):
        with self._lock:
            self._closed = True
            self._events.clear()
            self._pressed.clear()
            self._active.clear()


class RunKeyEvents:
    """Read keys without consuming them. An unavailable hook uses held-level polling.

    status() retains the keyboard state and error and reports mouse_mode separately.
    Mouse codes are the Win32 left/right/middle/X1/X2 VK codes (1, 2, 4, 5, 6).
    Edges from polling cannot prove physical input, and a full pulse between polls
    can be missed. Consumers may choose to act on hook edges only.
    """
    def __init__(self, bindings=None, *, read_key=None, foreground=None, source_factory=None,
                 allow_injected=False, fixed_stop_event=None):
        if read_key is None or foreground is None:
            from .windows import user32
            if read_key is None:
                read_key = user32.GetAsyncKeyState
            if foreground is None:
                foreground = user32.GetForegroundWindow
        self._read_key = read_key
        self._foreground = foreground
        self._lock = threading.Lock()
        self._closed = False
        self._queue = RunKeyQueue(allow_injected=allow_injected, fixed_stop_event=fixed_stop_event)
        self._queue.configure(run_bindings() if bindings is None else bindings, self._read_key)
        if source_factory is None:
            self._source = ReadOnlyKeyboardEvents(self._queue, mouse_enabled=self._queue.watches_mouse)
        else:
            self._source = source_factory(self._queue)

    def wait_ready(self, timeout):
        return self._source.ready.wait(timeout)

    def configure(self, bindings):
        with self._lock:
            generation = self._queue.configure(bindings, self._read_key)
            refresh_mouse = getattr(self._source, 'refresh_mouse', None)
            if refresh_mouse is not None:
                refresh_mouse()
            return generation

    def drain(self):
        with self._lock:
            if self._closed:
                return []
            status = self._source.status()
            if status['state'] in ('failed', 'closed'):
                self._queue.poll_levels(self._read_key, self._foreground)
            elif status.get('mouse_state') == 'failed' and self._queue.watches_mouse():
                self._queue.poll_levels(self._read_key, self._foreground, mouse_only=True)
            return self._queue.drain()

    def status(self):
        with self._lock:
            status = self._source.status()
            state = status['state']
            if self._closed:
                mode = 'closed'
            elif state == 'listening':
                mode = 'hook'
            elif state == 'starting':
                mode = 'starting'
            else:
                mode = 'polling'
            if self._closed:
                mouse_mode = 'closed'
            elif not self._queue.watches_mouse():
                mouse_mode = 'disabled'
            elif mode == 'polling' or status.get('mouse_state') == 'failed':
                mouse_mode = 'polling'
            elif status.get('mouse_state') == 'listening':
                mouse_mode = 'hook'
            else:
                mouse_mode = 'starting'
            return status | {'mode': mode, 'mouse_mode': mouse_mode}

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._source.close()

    def join(self, timeout=None):
        self._source.join(timeout)
