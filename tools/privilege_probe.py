"""Read-only token and DPI diagnostics for input compatibility."""
import ctypes as C
import json
import sys
from ctypes import wintypes as W
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autozzz.config import Settings
from autozzz.windows import find_game, geometry, user32, kernel32, is_admin
advapi = C.WinDLL("advapi32", use_last_error=True)
advapi.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
advapi.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
window = find_game(Settings().game_path)
pid = W.DWORD()
user32.GetWindowThreadProcessId(window.hwnd, C.byref(pid))
process = kernel32.OpenProcess(0x1000, False, pid.value)
token = W.HANDLE()
elevation = W.DWORD()
length = W.DWORD()
try:
    opened = advapi.OpenProcessToken(process, 8, C.byref(token))
    read = opened and advapi.GetTokenInformation(token, 20, C.byref(elevation), C.sizeof(elevation), C.byref(length))
    print(json.dumps({"bot_admin":is_admin(), "game_elevated":bool(elevation.value) if read else None,
                      "token_error":C.get_last_error() if not read else 0, "geometry":geometry(window.hwnd).__dict__,
                      "foreground":user32.GetForegroundWindow()==window.hwnd}, indent=2))
finally:
    if token: kernel32.CloseHandle(token)
    if process: kernel32.CloseHandle(process)
