"""Manual paste verification with a memory-only Windows input adapter."""

from __future__ import annotations

import ctypes
import threading

import pytest

from voice_to_me import paste
from voice_to_me.paste import INPUT_MARKER, PasteError, PasteMacro


class FakeWin32:
    def __init__(self):
        self.window = 101
        self.process = 202
        self.thread_id = 303
        self.held = set()
        self.sent = []
        self.send_results = []
        self.foreground_error = False
        self.key_state_error = False
        self.foreground_calls = 0
        self.change_focus_on_call = None
        self.change_pid_on_call = None
        self.key_checked = threading.Event()
        self.foreground_gate = None
        self.key_gate = None
        self.send_threads = []

    def GetForegroundWindow(self):
        self.foreground_calls += 1
        if self.foreground_error:
            raise OSError("Unavailable")
        if self.foreground_gate is not None and self.foreground_calls > 1:
            assert self.foreground_gate.wait(2)
        if self.foreground_calls == self.change_focus_on_call:
            self.window = 999
        if self.foreground_calls == self.change_pid_on_call:
            self.process = 999
        return self.window

    def GetWindowThreadProcessId(self, window, process):
        assert window == self.window
        ctypes.cast(process, ctypes.POINTER(ctypes.c_uint32)).contents.value = self.process
        return self.thread_id

    def GetAsyncKeyState(self, key):
        self.key_checked.set()
        if self.key_gate is not None:
            assert self.key_gate.wait(2)
        if self.key_state_error:
            raise OSError("Unavailable")
        return 0x8000 if key in self.held else 0

    def SendInput(self, count, events, size):
        assert size == ctypes.sizeof(paste._INPUT)
        self.send_threads.append(threading.current_thread())
        self.sent.append([
            (
                event.type,
                event.ki.wVk,
                event.ki.wScan,
                event.ki.dwFlags,
                event.ki.time,
                event.ki.dwExtraInfo,
            )
            for event in events[:count]
        ])
        result = self.send_results.pop(0) if self.send_results else count
        if isinstance(result, Exception):
            raise result
        return result


def adapter(win32, **kwargs):
    return PasteMacro(user32=win32, process_id=404, **kwargs)


def idle(macro):
    with macro._condition:
        assert macro._condition.wait_for(lambda: not macro._busy, timeout=2)


def signature(keys):
    return [(1, key, 0, 2 if released else 0, 0, INPUT_MARKER) for key, released in keys]


def test_windows_input_layout_matches_native_pointer_width():
    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(paste._KEYBDINPUT) == (24 if pointer_size == 8 else 16)
    assert ctypes.sizeof(paste._MOUSEINPUT) == (32 if pointer_size == 8 else 24)
    assert ctypes.sizeof(paste._HARDWAREINPUT) == 8
    assert ctypes.sizeof(paste._INPUT) == (40 if pointer_size == 8 else 28)
    assert paste._INPUT.u.offset == pointer_size
    assert paste._KEYBDINPUT.dwExtraInfo.offset == (16 if pointer_size == 8 else 12)
    assert 0 < INPUT_MARKER < 2**32


def test_only_ctrl_v_events_are_sent_on_a_daemon_worker():
    win32 = FakeWin32()
    macro = adapter(win32)
    try:
        assert macro.paste()
        idle(macro)
        assert win32.sent == [signature([(0x11, False), (0x56, False), (0x56, True), (0x11, True)])]
        assert len(win32.send_threads) == 1
        assert win32.send_threads[0] is not threading.current_thread()
        assert win32.send_threads[0].daemon
    finally:
        macro.close()


def test_multiple_manual_pastes_reuse_one_worker():
    win32 = FakeWin32()
    macro = adapter(win32)
    try:
        assert macro.paste()
        idle(macro)
        first_worker = macro._worker
        assert macro.paste()
        idle(macro)
        assert macro._worker is first_worker
        assert len(win32.sent) == 2
    finally:
        macro.close()


def test_repeated_triggers_while_busy_are_not_queued():
    win32 = FakeWin32()
    win32.key_gate = threading.Event()
    macro = adapter(win32)
    try:
        assert macro.paste()
        assert win32.key_checked.wait(2)
        assert not macro.paste()
        assert not macro.paste()
        win32.key_gate.set()
        idle(macro)
        assert len(win32.sent) == 1
    finally:
        win32.key_gate.set()
        macro.close()


@pytest.mark.parametrize("window, process, thread_id", [(0, 202, 303), (101, 0, 303), (101, 404, 303), (101, 202, 0)])
def test_unknown_or_own_foreground_window_is_ignored(window, process, thread_id):
    win32 = FakeWin32()
    win32.window, win32.process, win32.thread_id = window, process, thread_id
    macro = adapter(win32)
    try:
        assert not macro.paste()
        assert macro._worker is None
        assert win32.sent == []
    finally:
        macro.close()


def test_native_foreground_failure_is_ignored():
    win32 = FakeWin32()
    win32.foreground_error = True
    macro = adapter(win32)
    try:
        assert not macro.paste()
        assert win32.sent == []
    finally:
        macro.close()


@pytest.mark.parametrize("key", [0x11, 0x12, 0x10, 0x5B, 0x5C])
def test_physical_modifiers_must_be_released_before_paste(key):
    win32 = FakeWin32()
    win32.held.add(key)
    macro = adapter(win32, modifier_wait=0)
    try:
        assert macro.paste()
        idle(macro)
        assert win32.sent == []
    finally:
        macro.close()


def test_waits_briefly_for_physical_modifier_release():
    win32 = FakeWin32()
    win32.held.add(0x11)
    macro = adapter(win32, modifier_wait=0.5)
    try:
        assert macro.paste()
        assert win32.key_checked.wait(2)
        assert win32.sent == []
        win32.held.clear()
        idle(macro)
        assert len(win32.sent) == 1
    finally:
        macro.close()


@pytest.mark.parametrize("focus_check", [2, 3])
def test_focus_changes_after_trigger_prevent_paste(focus_check):
    win32 = FakeWin32()
    win32.change_focus_on_call = focus_check
    macro = adapter(win32)
    try:
        assert macro.paste()
        idle(macro)
        assert win32.sent == []
    finally:
        macro.close()


def test_recycled_window_with_different_process_is_ignored():
    win32 = FakeWin32()
    win32.change_pid_on_call = 2
    macro = adapter(win32)
    try:
        assert macro.paste()
        idle(macro)
        assert win32.sent == []
    finally:
        macro.close()


def test_key_state_error_ignores_trigger_and_worker_can_recover():
    win32 = FakeWin32()
    win32.key_state_error = True
    macro = adapter(win32)
    try:
        assert macro.paste()
        idle(macro)
        assert win32.sent == []
        win32.key_state_error = False
        assert macro.paste()
        idle(macro)
        assert len(win32.sent) == 1
    finally:
        macro.close()


@pytest.mark.parametrize("sent", [0, 1, 2, 3, OSError("Send failed")])
def test_partial_or_failed_insertion_releases_both_keys(sent):
    win32 = FakeWin32()
    win32.send_results.append(sent)
    macro = adapter(win32)
    try:
        assert macro.paste()
        idle(macro)
        assert len(win32.sent) == 2
        assert win32.sent[1] == signature([(0x56, True), (0x11, True)])
    finally:
        macro.close()


def test_partial_release_retries_only_unreleased_keys():
    win32 = FakeWin32()
    win32.send_results = [1, 1, 1]
    macro = adapter(win32)
    try:
        assert macro.paste()
        idle(macro)
        assert win32.sent[1] == signature([(0x56, True), (0x11, True)])
        assert win32.sent[2] == signature([(0x11, True)])
    finally:
        macro.close()


def test_release_failures_have_bounded_retries_and_worker_survives(caplog):
    win32 = FakeWin32()
    win32.send_results = [1, OSError("Unavailable"), 0, 0]
    macro = adapter(win32)
    try:
        assert macro.paste()
        idle(macro)
        assert len(win32.sent) == 4
        assert "key releases" in caplog.text
        assert macro.paste()
        idle(macro)
        assert len(win32.sent) == 5
    finally:
        macro.close()


def test_close_cancels_a_modifier_wait_and_prevents_future_pastes():
    win32 = FakeWin32()
    win32.held.add(0x11)
    macro = adapter(win32, modifier_wait=60)
    assert macro.paste()
    assert win32.key_checked.wait(2)
    macro.close()
    macro.close()
    assert not macro.paste()
    assert not macro._worker.is_alive()
    assert win32.sent == []


def test_close_cancels_before_late_foreground_check_finishes():
    win32 = FakeWin32()
    win32.foreground_gate = threading.Event()
    macro = adapter(win32)
    assert macro.paste()
    macro.close()
    win32.foreground_gate.set()
    macro._worker.join(timeout=2)
    assert not macro._worker.is_alive()
    assert win32.sent == []


def test_close_before_first_trigger_does_not_start_a_worker():
    win32 = FakeWin32()
    macro = adapter(win32)
    macro.close()
    assert not macro.paste()
    assert macro._worker is None
    assert win32.foreground_calls == 0


def test_negative_modifier_wait_is_rejected():
    with pytest.raises(ValueError, match="non-negative"):
        adapter(FakeWin32(), modifier_wait=-1)


def test_default_adapter_rejects_non_windows_without_loading_user32(monkeypatch):
    monkeypatch.setattr(paste.os, "name", "posix")
    with pytest.raises(PasteError, match="Windows"):
        PasteMacro()
