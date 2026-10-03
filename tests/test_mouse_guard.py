from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import replace
from unittest.mock import Mock

import pytest

from voice_to_me import mouse_guard
from voice_to_me.config import HotkeySettings
from voice_to_me.mouse_guard import MouseShortcutGuard, WindowsMouseAPI


class FakeUser32:
    """Fake raw Win32 functions; no native window, listener, or device is touched."""

    def __init__(self):
        self.pids = {100: 77, 200: 77}
        self.thread_id = 1
        self.window_class = "ExternalApplicationWindow"
        self.WindowFromPoint = Mock(return_value=100)
        self.GetAncestor = Mock(return_value=200)
        self.GetWindowThreadProcessId = Mock(side_effect=self._process_id)
        self.GetClassNameW = Mock(side_effect=self._class_name)

    def _process_id(self, window, pointer):
        ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD)).contents.value = self.pids.get(window, 0)
        return self.thread_id

    def _class_name(self, window, buffer, length):
        del window, length
        buffer.value = self.window_class
        return len(self.window_class)


@pytest.fixture
def win32(monkeypatch):
    monkeypatch.setattr(mouse_guard.os, "getpid", lambda: 42)
    return FakeUser32()


def make_guard(win32, button="left"):
    return MouseShortcutGuard(
        lambda: HotkeySettings(mouse_button=button),
        api=WindowsMouseAPI(win32), is_windows=True,
    )


@pytest.mark.parametrize("button", ["left", "right"])
def test_primary_button_allows_a_known_external_window(win32, button):
    assert make_guard(win32, button)(500, 300)
    win32.GetAncestor.assert_called_once_with(100, 2)
    assert win32.GetWindowThreadProcessId.call_count == 2


@pytest.mark.parametrize("button", ["left", "right"])
def test_primary_button_ignores_this_process_window(win32, button):
    win32.pids[100] = 42
    assert not make_guard(win32, button)(500, 300)
    win32.GetAncestor.assert_not_called()


def test_primary_button_ignores_owned_root_even_when_child_has_a_different_pid(win32):
    win32.pids[200] = 42
    assert not make_guard(win32)(500, 300)


@pytest.mark.parametrize("button", ["left", "right"])
@pytest.mark.parametrize("root_class", [
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
])
def test_primary_button_ignores_taskbar_tray_and_overflow(win32, button, root_class):
    win32.window_class = root_class
    assert not make_guard(win32, button)(500, 300)


@pytest.mark.parametrize("failure", [
    "no-window", "no-process", "no-thread", "no-root", "no-root-process", "no-class",
])
def test_unknown_native_result_ignores_the_click(win32, failure):
    if failure == "no-window":
        win32.WindowFromPoint.return_value = None
    elif failure == "no-process":
        win32.pids[100] = 0
    elif failure == "no-thread":
        win32.thread_id = 0
    elif failure == "no-root":
        win32.GetAncestor.return_value = 0
    elif failure == "no-root-process":
        win32.pids[200] = 0
    elif failure == "no-class":
        win32.window_class = ""
    assert not make_guard(win32)(123, 456)


@pytest.mark.parametrize("function", [
    "WindowFromPoint", "GetWindowThreadProcessId", "GetAncestor", "GetClassNameW",
])
def test_native_failure_ignores_click_without_logging_coordinates(win32, function, caplog):
    getattr(win32, function).side_effect = OSError("private window information")
    assert not make_guard(win32)(123, 456)
    assert caplog.text == ""


def test_api_initialization_failure_ignores_click(monkeypatch, caplog):
    monkeypatch.setattr(mouse_guard, "WindowsMouseAPI", Mock(side_effect=OSError("no user32")))
    guard = MouseShortcutGuard(lambda: HotkeySettings(mouse_button="left"), is_windows=True)
    assert not guard(123, 456)
    assert caplog.text == ""


@pytest.mark.parametrize("button", ["middle", "x1", "x2", ""])
def test_non_primary_buttons_allow_everywhere_without_native_calls(win32, button):
    win32.WindowFromPoint.side_effect = AssertionError("native access must not happen")
    assert make_guard(win32, button)(123, 456)
    win32.WindowFromPoint.assert_not_called()


def test_non_windows_fallback_allows_without_settings_or_api_access():
    provider = Mock(side_effect=AssertionError("settings access must not happen"))
    api = Mock(spec=WindowsMouseAPI)
    guard = MouseShortcutGuard(provider, api=api, is_windows=False)
    assert guard(-123, -456)
    provider.assert_not_called()
    assert api.mock_calls == []


def test_settings_provider_failure_safely_ignores_click(win32):
    provider = Mock(side_effect=RuntimeError("settings are unavailable"))
    guard = MouseShortcutGuard(provider, api=WindowsMouseAPI(win32), is_windows=True)
    assert not guard(123, 456)
    win32.WindowFromPoint.assert_not_called()


def test_primary_filter_reads_live_settings_without_recreating_the_guard(win32):
    settings = [HotkeySettings(mouse_button="left")]
    win32.pids[100] = 42
    guard = MouseShortcutGuard(
        lambda: settings[0], api=WindowsMouseAPI(win32), is_windows=True,
    )
    assert not guard(123, 456)
    settings[0] = replace(settings[0], mouse_button="x1")
    assert guard(123, 456)
    assert win32.WindowFromPoint.call_count == 1
    settings[0] = replace(settings[0], mouse_button="right")
    assert not guard(123, 456)
    assert win32.WindowFromPoint.call_count == 2


def test_api_uses_signed_point_by_value_and_typed_win32_signatures(win32):
    api = WindowsMouseAPI(win32)
    assert api.window_from_point(-1920, -1080) == 100
    point = win32.WindowFromPoint.call_args.args[0]
    assert isinstance(point, mouse_guard._Point)
    assert ctypes.sizeof(point) == 8
    assert (point.x, point.y) == (-1920, -1080)
    assert win32.WindowFromPoint.argtypes == [mouse_guard._Point]
    assert win32.WindowFromPoint.restype is wintypes.HWND
    assert win32.GetWindowThreadProcessId.argtypes == [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
    ]
    assert win32.GetWindowThreadProcessId.restype is wintypes.DWORD
    assert win32.GetAncestor.argtypes == [wintypes.HWND, wintypes.UINT]
    assert win32.GetAncestor.restype is wintypes.HWND
    assert win32.GetClassNameW.argtypes == [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    assert win32.GetClassNameW.restype is ctypes.c_int
