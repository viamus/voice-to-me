"""Keep primary-button shortcuts away from this app's windows and the Windows tray."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from ctypes import wintypes

from .config import HotkeySettings

_SHELL_WINDOW_CLASSES = frozenset({
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "NotifyIconOverflowWindow",
})
_GA_ROOT = 2


class _Point(ctypes.Structure):
    # Win32 POINT uses signed 32-bit LONGs, including on 64-bit Windows.
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class WindowsMouseAPI:
    """Small typed user32 adapter. It never calls Tk or installs a native hook."""

    def __init__(self, user32=None) -> None:
        self._user32 = user32 if user32 is not None else ctypes.WinDLL(
            "user32", use_last_error=True
        )
        self._user32.WindowFromPoint.argtypes = [_Point]
        self._user32.WindowFromPoint.restype = wintypes.HWND
        self._user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
        ]
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self._user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self._user32.GetAncestor.restype = wintypes.HWND
        self._user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self._user32.GetClassNameW.restype = ctypes.c_int

    def window_from_point(self, x: int, y: int) -> int:
        return int(self._user32.WindowFromPoint(_Point(x, y)) or 0)

    def process_id(self, window: int) -> int:
        process = wintypes.DWORD()
        thread = self._user32.GetWindowThreadProcessId(window, ctypes.byref(process))
        return int(process.value) if thread else 0

    def root_window(self, window: int) -> int:
        return int(self._user32.GetAncestor(window, _GA_ROOT) or 0)

    def class_name(self, window: int) -> str:
        name = ctypes.create_unicode_buffer(256)
        length = self._user32.GetClassNameW(window, name, len(name))
        return name.value if length else ""


class MouseShortcutGuard:
    """Filter only primary-button shortcuts, reading current settings on each click.

    The callback is safe on a mouse listener thread. Unknown Win32 results are
    ignored, preserving normal window commands without risking a second toggle.
    Middle and extra buttons stay available everywhere. Non-Windows platforms
    allow clicks so simulated adapters and portable callers keep working.
    """

    def __init__(
        self,
        settings_provider: Callable[[], HotkeySettings],
        *,
        api: WindowsMouseAPI | None = None,
        is_windows: bool | None = None,
    ) -> None:
        self._settings_provider = settings_provider
        self._api = api
        self._is_windows = os.name == "nt" if is_windows is None else is_windows
        self._own_pid = os.getpid()

    def __call__(self, x: int, y: int) -> bool:
        if not self._is_windows:
            return True
        try:
            if self._settings_provider().mouse_button.strip().lower() not in {"left", "right"}:
                return True
            if self._api is None:
                self._api = WindowsMouseAPI()
            window = self._api.window_from_point(x, y)
            if not window:
                return False
            process = self._api.process_id(window)
            if not process or process == self._own_pid:
                return False
            root = self._api.root_window(window)
            if not root:
                return False
            root_process = self._api.process_id(root)
            if not root_process or root_process == self._own_pid:
                return False
            window_class = self._api.class_name(root)
            if not window_class or window_class in _SHELL_WINDOW_CLASSES:
                return False
            return True
        except Exception:  # noqa: BLE001 - a native failure must safely ignore the click
            return False
