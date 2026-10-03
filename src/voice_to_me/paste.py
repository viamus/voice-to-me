"""Manual Ctrl+V in the current external window; never read or replace clipboard text."""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from typing import Any

_LOG = logging.getLogger(__name__)

# A pointer-sized marker that fits both Windows ABIs. Global shortcut listeners
# exclude only our own inputs, leaving other applications' injected inputs alone.
INPUT_MARKER = 0x56544D50
_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_VK_CONTROL = 0x11
_VK_V = 0x56
_MODIFIERS = (_VK_CONTROL, 0x12, 0x10, 0x5B, 0x5C)


class PasteError(RuntimeError):
    """The Windows paste adapter could not be initialized."""


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_uint16),
        ("wScan", ctypes.c_uint16),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_uint32),
        ("wParamL", ctypes.c_uint16),
        ("wParamH", ctypes.c_uint16),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_uint32), ("u", _INPUT_UNION)]


def _key_event(virtual_key: int, *, released: bool = False) -> _INPUT:
    event = _INPUT(type=_INPUT_KEYBOARD)
    event.ki = _KEYBDINPUT(
        wVk=virtual_key,
        dwFlags=_KEYEVENTF_KEYUP if released else 0,
        dwExtraInfo=INPUT_MARKER,
    )
    return event


class PasteMacro:
    """One optional manual paste per trigger, handled on a single daemon worker.

    ``paste()`` immediately returns whether a request was accepted. Requests are
    dropped while one is pending or active; they never form a queue. The worker
    waits briefly for physical modifiers to be released, then verifies that the
    same external foreground window still owns focus. ``close()`` cancels waiting
    work and prevents any later injection. A four-event SendInput call already in
    progress is allowed to finish its key releases before close returns.

    Injecting user32 is intended for tests. No clipboard, shell, or subprocess API
    is involved. Windows may refuse inputs to a more privileged application.
    """

    def __init__(
        self,
        *,
        user32: Any = None,
        process_id: int | None = None,
        modifier_wait: float = 0.4,
    ) -> None:
        if modifier_wait < 0:
            raise ValueError("The modifier wait must be non-negative")
        if user32 is None:
            if os.name != "nt":
                raise PasteError("The paste shortcut requires Windows")
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._configure_functions(user32)
        self._user32 = user32
        self._own_pid = os.getpid() if process_id is None else process_id
        self._modifier_wait = modifier_wait
        self._condition = threading.Condition()
        self._cancel = threading.Event()
        self._closed = False
        self._busy = False
        self._pending: tuple[int, int] | None = None
        self._worker: threading.Thread | None = None

    @staticmethod
    def _configure_functions(user32: Any) -> None:
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetWindowThreadProcessId.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        user32.GetWindowThreadProcessId.restype = ctypes.c_uint32
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_int16
        user32.SendInput.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(_INPUT),
            ctypes.c_int,
        ]
        user32.SendInput.restype = ctypes.c_uint32

    def _foreground(self) -> tuple[int, int] | None:
        window = int(self._user32.GetForegroundWindow() or 0)
        if not window:
            return None
        process = ctypes.c_uint32()
        thread = self._user32.GetWindowThreadProcessId(window, ctypes.byref(process))
        if not thread or not process.value or process.value == self._own_pid:
            return None
        return window, process.value

    def paste(self) -> bool:
        """Accept a manual trigger for the external window currently in focus."""
        with self._condition:
            if self._closed or self._busy:
                return False
        try:
            foreground = self._foreground()
        except Exception:  # noqa: BLE001 - native failures safely ignore the trigger
            _LOG.warning("Paste shortcut ignored: the foreground window is unavailable.")
            return False
        if foreground is None:
            return False
        with self._condition:
            if self._closed or self._busy:
                return False
            self._pending = foreground
            self._busy = True
            if self._worker is None:
                self._worker = threading.Thread(
                    target=self._run,
                    name="voice-to-me-paste",
                    daemon=True,
                )
                try:
                    self._worker.start()
                except Exception:
                    self._pending = None
                    self._busy = False
                    self._worker = None
                    raise
            self._condition.notify_all()
            return True

    def _modifiers_held(self) -> bool:
        return any(self._user32.GetAsyncKeyState(key) & 0x8000 for key in _MODIFIERS)

    def _ready(self, foreground: tuple[int, int]) -> bool:
        deadline = time.monotonic() + self._modifier_wait
        while not self._cancel.is_set():
            if self._foreground() != foreground:
                return False
            if not self._modifiers_held():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._cancel.wait(min(remaining, 0.01))
        return False

    def _release_after_failure(self) -> None:
        # SendInput inserts a prefix on partial success. Releasing both keys is
        # also safe when a native exception leaves the accepted prefix unknown.
        pending = [_key_event(_VK_V, released=True), _key_event(_VK_CONTROL, released=True)]
        for _ in range(3):
            events = (_INPUT * len(pending))(*pending)
            try:
                sent = int(self._user32.SendInput(len(events), events, ctypes.sizeof(_INPUT)))
            except Exception:  # noqa: BLE001 - still retry release after native failure
                sent = 0
            if sent >= len(pending):
                return
            if sent > 0:
                pending = pending[sent:]
        _LOG.warning("Windows refused the paste shortcut's key releases.")

    def _send(self) -> None:
        events = (_INPUT * 4)(
            _key_event(_VK_CONTROL),
            _key_event(_VK_V),
            _key_event(_VK_V, released=True),
            _key_event(_VK_CONTROL, released=True),
        )
        try:
            complete = int(self._user32.SendInput(4, events, ctypes.sizeof(_INPUT))) == 4
        except Exception:  # noqa: BLE001 - native failure must still release pressed keys
            complete = False
        if not complete:
            self._release_after_failure()
            _LOG.warning("Windows could not complete the manual paste shortcut.")

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                foreground, self._pending = self._pending, None
            try:
                if foreground is not None and self._ready(foreground):
                    with self._condition:
                        # Serialize a complete key sequence with close, and
                        # recheck focus/modifiers immediately before insertion.
                        if (
                            not self._closed
                            and self._foreground() == foreground
                            and not self._modifiers_held()
                        ):
                            self._send()
            except Exception:  # noqa: BLE001 - one rejected trigger must not kill the worker
                _LOG.warning("Paste shortcut ignored because a Windows input check failed.")
            finally:
                with self._condition:
                    self._busy = False
                    self._condition.notify_all()

    def close(self) -> None:
        """Cancel waiting work and disable future paste requests."""
        self._cancel.set()
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify_all()
            worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=0.1)
