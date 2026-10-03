"""Write final text to the Windows Unicode clipboard without typing or pasting."""

from __future__ import annotations

import ctypes
import os
import time
from collections.abc import Callable
from ctypes import wintypes
from typing import Any


class ClipboardError(RuntimeError):
    """The clipboard could not safely accept the final text."""


class WindowsClipboard:
    """Transfer one allocated CF_UNICODETEXT block to Windows.

    The injectable Win32 functions let tests run without touching the real
    clipboard. Windows owns the allocation only after SetClipboardData succeeds.
    Previous Unicode text is snapshotted before clearing and restored on a failed
    write. Win32 has no atomic replace; rollback is best-effort and text-only.
    """

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    GMEM_ZEROINIT = 0x0040

    def __init__(
        self,
        *,
        user32: Any = None,
        kernel32: Any = None,
        attempts: int = 10,
        retry_delay: float = 0.05,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if attempts < 1 or retry_delay < 0:
            raise ValueError(
                "Clipboard retry settings must be non-negative with at least one attempt"
            )
        if (user32 is None) != (kernel32 is None):
            raise ValueError("Provide both Win32 libraries or neither")
        if user32 is None:
            if os.name != "nt":
                raise ClipboardError("The Unicode clipboard adapter requires Windows")
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._configure_functions(user32, kernel32)
        self._user32 = user32
        self._kernel32 = kernel32
        self._attempts = attempts
        self._retry_delay = retry_delay
        self._sleep = sleep

    @staticmethod
    def _configure_functions(user32: Any, kernel32: Any) -> None:
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            ctypes.c_void_p,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.restype = wintypes.HGLOBAL
        kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalSize.restype = ctypes.c_size_t

    def _snapshot_unicode(self) -> Any:
        """Must run with the clipboard open; never clear on a snapshot failure."""
        if not self._user32.IsClipboardFormatAvailable(self.CF_UNICODETEXT):
            return None
        original = self._user32.GetClipboardData(self.CF_UNICODETEXT)
        size = self._kernel32.GlobalSize(original) if original else 0
        if not size:
            raise ClipboardError("The previous clipboard text could not be preserved; retry")
        source = self._kernel32.GlobalLock(original)
        if not source:
            raise ClipboardError("The previous clipboard text could not be preserved; retry")
        snapshot = None
        copied = False
        try:
            snapshot = self._kernel32.GlobalAlloc(self.GMEM_MOVEABLE | self.GMEM_ZEROINIT, size)
            if not snapshot:
                raise ClipboardError("The previous clipboard text could not be preserved; retry")
            destination = self._kernel32.GlobalLock(snapshot)
            if not destination:
                raise ClipboardError("The previous clipboard text could not be preserved; retry")
            try:
                ctypes.memmove(destination, source, size)
            finally:
                self._kernel32.GlobalUnlock(snapshot)
            copied = True
            return snapshot
        finally:
            self._kernel32.GlobalUnlock(original)
            if snapshot and not copied:
                self._kernel32.GlobalFree(snapshot)

    def write_text(self, text: str) -> None:
        if not isinstance(text, str) or not text.strip():
            raise ClipboardError("Refusing to replace the clipboard with empty text")
        if "\0" in text:
            raise ClipboardError("Refusing text containing a Unicode clipboard terminator")
        try:
            # Encode explicitly: ctypes.c_wchar uses the host platform's width.
            payload = text.encode("utf-16-le") + b"\0\0"
        except UnicodeEncodeError as exc:
            raise ClipboardError("The final text contains invalid Unicode") from exc

        allocation = self._kernel32.GlobalAlloc(
            self.GMEM_MOVEABLE | self.GMEM_ZEROINIT, len(payload)
        )
        if not allocation:
            raise ClipboardError("Windows could not allocate clipboard memory")
        opened = False
        transferred = False
        previous = None
        previous_transferred = False
        owner = None
        try:
            pointer = self._kernel32.GlobalLock(allocation)
            if not pointer:
                raise ClipboardError("Windows could not lock clipboard memory")
            try:
                ctypes.memmove(pointer, payload, len(payload))
            finally:
                # A zero return can mean a successful final unlock, so it is
                # deliberately not interpreted as failure.
                self._kernel32.GlobalUnlock(allocation)

            # EmptyClipboard requires a real owner for SetClipboardData. A
            # message-only window has no visible UI and belongs to this thread.
            owner = self._user32.CreateWindowExW(
                0,
                "STATIC",
                "Voice to Me Clipboard",
                0,
                0,
                0,
                0,
                0,
                wintypes.HWND(-3),
                None,
                None,
                None,
            )
            if not owner:
                raise ClipboardError("Windows could not create the clipboard owner")
            for attempt in range(self._attempts):
                if self._user32.OpenClipboard(owner):
                    opened = True
                    break
                if attempt + 1 < self._attempts:
                    self._sleep(self._retry_delay)
            if not opened:
                raise ClipboardError(
                    "The clipboard is busy. Retry after closing other clipboard tools"
                )
            previous = self._snapshot_unicode()
            if not self._user32.EmptyClipboard():
                raise ClipboardError("Windows could not prepare the clipboard")
            if not self._user32.SetClipboardData(self.CF_UNICODETEXT, allocation):
                if previous:
                    previous_transferred = bool(
                        self._user32.SetClipboardData(self.CF_UNICODETEXT, previous)
                    )
                raise ClipboardError("Windows could not write the final text to the clipboard")
            transferred = True
        finally:
            if opened:
                self._user32.CloseClipboard()
            if owner:
                self._user32.DestroyWindow(owner)
            if not transferred:
                self._kernel32.GlobalFree(allocation)
            if previous and not previous_transferred:
                self._kernel32.GlobalFree(previous)
