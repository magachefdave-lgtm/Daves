"""Windows 창 다루기: 뷰어 창 제목에 설비명 붙이기, 창을 앞으로 가져오기, 화면 크기.

뷰어마다 제목을 정하는 옵션이 다르거나 없어서, 실행한 뷰어 프로세스의 창을 찾아
제목 앞에 "[설비명] "을 붙이는 방식을 쓴다. 뷰어가 접속 뒤 제목을 다시 쓰기도 하므로
호출하는 쪽에서 주기적으로 다시 부른다.

콘솔 없는 exe 에서는 외부 명령(tasklist 등)이 실패할 수 있어 전부 Win32 를 직접 부른다.
Windows 가 아니면 모든 함수가 "아무것도 못 했다"를 돌려준다.
"""
from __future__ import annotations

import functools
import sys

from .core import new_title

AVAILABLE = sys.platform == "win32"
_WM_SETTEXT = 0x000C
_SMTO_ABORTIFHUNG = 0x0002
_SW_RESTORE = 9


@functools.lru_cache(maxsize=1)
def _api():
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                           wintypes.LPCWSTR, wintypes.UINT, wintypes.UINT,
                                           ctypes.POINTER(ctypes.c_size_t)]
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    return ctypes, wintypes, user32, enum_proc


def _windows(pid: int) -> list[tuple[int, str]]:
    """pid 프로세스의 보이는 최상위 창 (핸들, 제목). 제목 없는 창은 뺀다."""
    ctypes, wintypes, user32, enum_proc = _api()
    found = []

    def visit(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(enum_proc(visit), 0)
    return found


def retitle(pid: int, name: str, rename: bool = True) -> int:
    """pid 프로세스의 보이는 최상위 창 수를 돌려주고, rename 이면 그 제목에 설비명을 붙인다."""
    if not AVAILABLE:
        return 0
    ctypes, _wintypes, user32, _enum = _api()
    windows = _windows(pid)
    for hwnd, title in windows:
        title = new_title(name, title) if rename else None
        if title:
            # 뷰어가 멈춰 있어도 이 프로그램까지 멈추지 않도록 시간 제한을 둔다.
            result = ctypes.c_size_t()
            user32.SendMessageTimeoutW(hwnd, _WM_SETTEXT, 0, title, _SMTO_ABORTIFHUNG, 200,
                                       ctypes.byref(result))
    return len(windows)


def focus(pid: int) -> bool:
    """pid 프로세스의 창을 맨 앞으로 가져온다 (최소화돼 있으면 되살린다). 창이 없으면 False.

    방금 이 프로그램을 클릭한 직후라 Windows 가 앞으로 가져오기를 허락한다.
    """
    if not AVAILABLE:
        return False
    _ctypes, _wintypes, user32, _enum = _api()
    windows = _windows(pid)
    if not windows:
        return False
    hwnd = windows[0][0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, _SW_RESTORE)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    return True


def virtual_screen() -> tuple[int, int, int, int] | None:
    """모든 모니터를 합친 영역 (x, y, 폭, 높이). Windows 가 아니면 None."""
    if not AVAILABLE:
        return None
    user32 = _api()[2]
    x, y, w, h = (user32.GetSystemMetrics(i) for i in (76, 77, 78, 79))
    return (x, y, w, h) if w > 0 and h > 0 else None
