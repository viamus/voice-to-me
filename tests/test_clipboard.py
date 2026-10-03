"""Clipboard verification with a memory-only Win32 adapter."""

from __future__ import annotations

import ctypes

import pytest

from voice_to_me.clipboard import ClipboardError, WindowsClipboard


class FakeWin32:
    def __init__(self) -> None:
        self.buffer = None
        self.buffers = {}
        self.freed = []
        self.calls = []
        self.open_results = [True]
        self.allocate = True
        self.lock = True
        self.empty = True
        self.set_data = True
        self.create_owner = True
        self.payload = None
        self.previous_text = None
        self.previous_buffer = None
        self.set_results = []
        self.snapshot_allocate = True
        self.lock_handle_failures = set()

    def CreateWindowExW(self, *args):
        self.calls.append("create_owner")
        return 456 if self.create_owner else 0

    def DestroyWindow(self, owner):
        self.calls.append("destroy_owner")
        assert owner == 456
        return True

    def GlobalAlloc(self, flags, size):
        self.calls.append("allocate")
        if not self.allocate or (self.buffers and not self.snapshot_allocate):
            return 0
        self.buffer = ctypes.create_string_buffer(size)
        handle = 123 + len(self.buffers)
        self.buffers[handle] = self.buffer
        return handle

    def GlobalLock(self, handle):
        self.calls.append("lock")
        buffer = self.previous_buffer if handle == 999 else self.buffers[handle]
        return (
            ctypes.addressof(buffer) if self.lock and handle not in self.lock_handle_failures else 0
        )

    def GlobalUnlock(self, handle):
        self.calls.append("unlock")
        return 0  # Successful final unlock also returns zero.

    def GlobalFree(self, handle):
        self.calls.append("free")
        self.freed.append(handle)
        return 0

    def GlobalSize(self, handle):
        return len(self.previous_buffer) if handle == 999 else len(self.buffers[handle])

    def IsClipboardFormatAvailable(self, format_id):
        self.calls.append("snapshot_check")
        return self.previous_text is not None

    def GetClipboardData(self, format_id):
        self.calls.append("snapshot_get")
        self.previous_buffer = ctypes.create_string_buffer(
            self.previous_text.encode("utf-16-le") + b"\0\0"
        )
        return 999

    def OpenClipboard(self, owner):
        self.calls.append("open")
        assert owner == 456
        return self.open_results.pop(0) if self.open_results else False

    def EmptyClipboard(self):
        self.calls.append("empty")
        if self.empty:
            self.payload = None
        return self.empty

    def SetClipboardData(self, format_id, handle):
        self.calls.append("set")
        assert format_id == 13
        success = self.set_results.pop(0) if self.set_results else self.set_data
        if success:
            self.payload = self.buffers[handle].raw
        return handle if success else 0

    def CloseClipboard(self):
        self.calls.append("close")
        return True


def adapter(win32, **kwargs):
    return WindowsClipboard(user32=win32, kernel32=win32, sleep=lambda _: None, **kwargs)


def test_writes_utf16_with_emoji_and_transfers_ownership():
    win32 = FakeWin32()
    final_text = "Olá, equipe! 👋\nTudo certo?"
    adapter(win32).write_text(final_text)
    assert win32.payload == final_text.encode("utf-16-le") + b"\0\0"
    assert win32.calls == [
        "allocate",
        "lock",
        "unlock",
        "create_owner",
        "open",
        "snapshot_check",
        "empty",
        "set",
        "close",
        "destroy_owner",
    ]


@pytest.mark.parametrize("text", ["", "  \n\t", None, "text\0truncated", "bad\ud800"])
def test_invalid_text_does_not_touch_clipboard(text):
    win32 = FakeWin32()
    with pytest.raises(ClipboardError):
        adapter(win32).write_text(text)
    assert win32.calls == []


def test_retries_busy_clipboard():
    win32 = FakeWin32()
    win32.open_results = [False, False, True]
    adapter(win32, attempts=3).write_text("Final text")
    assert win32.calls.count("open") == 3
    assert "free" not in win32.calls


def test_busy_clipboard_is_never_emptied_and_allocation_freed():
    win32 = FakeWin32()
    win32.open_results = [False]
    with pytest.raises(ClipboardError, match="busy"):
        adapter(win32, attempts=2).write_text("Final text")
    assert "empty" not in win32.calls
    assert "close" not in win32.calls
    assert win32.calls[-1] == "free"


@pytest.mark.parametrize("failure", ["allocate", "lock", "create_owner", "empty", "set_data"])
def test_native_failure_closes_and_frees_memory(failure):
    win32 = FakeWin32()
    setattr(win32, failure, False)
    with pytest.raises(ClipboardError):
        adapter(win32).write_text("Final text")
    if failure != "allocate":
        assert win32.calls[-1] == "free"
    if failure in {"empty", "set_data"}:
        assert win32.calls[-3] == "close"
    else:
        assert "empty" not in win32.calls


def test_retry_configuration_is_validated():
    win32 = FakeWin32()
    with pytest.raises(ValueError):
        adapter(win32, attempts=0)


def test_failed_set_restores_previous_unicode_and_releases_only_unowned_allocation():
    win32 = FakeWin32()
    win32.previous_text = "Mensagem anterior 👋"
    win32.set_results = [False, True]
    with pytest.raises(ClipboardError, match="write"):
        adapter(win32).write_text("Nova mensagem")
    assert win32.payload == win32.previous_buffer.raw
    assert win32.freed == [123]
    assert win32.calls.count("set") == 2


@pytest.mark.parametrize("failure", ["allocate", "old_lock", "new_lock"])
def test_snapshot_failure_preserves_previous_clipboard_without_empty(failure):
    win32 = FakeWin32()
    win32.previous_text = "Keep this text"
    if failure == "allocate":
        win32.snapshot_allocate = False
    else:
        win32.lock_handle_failures = {999 if failure == "old_lock" else 124}
    with pytest.raises(ClipboardError, match="preserved"):
        adapter(win32).write_text("Final text")
    assert "empty" not in win32.calls
    assert "close" in win32.calls
    assert 123 in win32.freed


def test_success_releases_unused_previous_text_snapshot():
    win32 = FakeWin32()
    win32.previous_text = "Old text"
    adapter(win32).write_text("Final text")
    assert win32.payload == "Final text".encode("utf-16-le") + b"\0\0"
    assert win32.freed == [124]
