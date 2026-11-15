"""VNC 뷰어 창의 제목 표시줄에 설비명을 붙인다 (Windows 전용).

뷰어마다 제목을 정하는 옵션이 다르거나 없어서, 실행한 뷰어 프로세스의 창을 찾아
제목 앞에 "[설비명] "을 붙이는 방식을 쓴다. 뷰어가 접속 뒤 제목을 다시 쓰기도 하므로
호출하는 쪽에서 주기적으로 다시 부른다.
"""
from __future__ import annotations

import sys

from .core import new_title

AVAILABLE = sys.platform == "win32"
_WM_SETTEXT = 0x000C
_SMTO_ABORTIFHUNG = 0x0002


def retitle(pid: int, name: str) -> int:
    """pid 프로세스의 보이는 최상위 창 제목에 설비명을 붙인다. 바꾼 창 수를 돌려준다."""
    if not AVAILABLE:
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                           wintypes.LPCWSTR, wintypes.UINT, wintypes.UINT,
                                           ctypes.POINTER(ctypes.c_size_t)]
    changed = 0

    def visit(hwnd, _lparam):
        nonlocal changed
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = new_title(name, buf.value)
        if title:
            # 뷰어가 멈춰 있어도 이 프로그램까지 멈추지 않도록 시간 제한을 둔다.
            result = ctypes.c_size_t()
            if user32.SendMessageTimeoutW(hwnd, _WM_SETTEXT, 0, title, _SMTO_ABORTIFHUNG, 200,
                                          ctypes.byref(result)):
                changed += 1
        return True

    user32.EnumWindows(enum_proc(visit), 0)
    return changed
