"""Capture a shortcut from actual key presses without injecting or suppressing input."""

from __future__ import annotations

import importlib
import logging
import math
import threading
from collections.abc import Callable
from typing import Any

from voice_to_me.config import MOUSE_BUTTONS, HotkeySettings
from voice_to_me.hotkeys import HotkeyManager

_LOG = logging.getLogger(__name__)
_MODIFIERS = {"ctrl": 0, "alt": 1, "shift": 2, "cmd": 3}
_MODIFIER_ALIASES = {
    "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "shift_l": "shift", "shift_r": "shift",
    "cmd_l": "cmd", "cmd_r": "cmd",
}


class ShortcutCapture:
    """Listen once for a keyboard chord or any standard mouse button.

    Keyboard capture completes on the first release, after collecting all keys
    held together. Escape, timeout, and setup failures report one English error.
    ``stop()`` cancels silently. Callbacks run on listener/timer threads; a GUI
    must enqueue their payloads rather than accessing its widgets directly.
    """

    def __init__(
        self,
        on_capture: Callable[[HotkeySettings], None],
        on_error: Callable[[str], None],
        timeout_seconds: float = 10,
        *,
        mouse_filter: Callable[[int, int], bool] | None = None,
    ):
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Shortcut capture timeout must be a positive number.")
        self.on_capture = on_capture
        self.on_error = on_error
        self.timeout_seconds = timeout_seconds
        self.mouse_filter = mouse_filter
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._keyboard: Any | None = None
        self._keyboard_listener: Any | None = None
        self._mouse_listener: Any | None = None
        self._mouse_buttons: dict[Any, str] = {}
        self._timer: Any | None = None
        self._pressed: dict[Any, str] = {}
        self._held_mouse: set[Any] = set()
        self._active = False
        self._generation = 0

    @staticmethod
    def _token(key: Any, listener: Any) -> str:
        name = getattr(key, "name", None)
        if name:
            name = _MODIFIER_ALIASES.get(name, name)
            return f"<{name}>"
        canonical = listener.canonical(key)
        name = getattr(canonical, "name", None)
        if name:
            return f"<{_MODIFIER_ALIASES.get(name, name)}>"
        char = getattr(canonical, "char", None)
        if char == " ":
            return "<space>"
        if char is not None and len(char) == 1 and char.isprintable():
            return char.lower()
        vk = getattr(canonical, "vk", None)
        if vk is not None:
            return f"<{vk}>"
        raise ValueError("The key cannot be represented as a shortcut.")

    @staticmethod
    def _token_order(token: str) -> tuple[int, str]:
        return _MODIFIERS.get(token.strip("<>"), 4), token

    def _on_press(self, generation: int, key: Any) -> None:
        with self._state_lock:
            if not self._active or generation != self._generation or key is None:
                return
            listener = self._keyboard_listener
        if listener is None:
            return
        try:
            token = self._token(key, listener)
        except Exception:  # noqa: BLE001 - report input backend failures through the callback
            self._finish(generation, error="This key cannot be used as a shortcut. Try another key.")
            return
        if token == "<esc>":
            self._finish(generation, error="Shortcut capture cancelled.")
            return
        physical = HotkeyManager._physical_key(key)
        with self._state_lock:
            if self._active and generation == self._generation:
                self._pressed.setdefault(physical, token)

    def _on_release(self, generation: int, key: Any) -> None:
        physical = HotkeyManager._physical_key(key)
        with self._state_lock:
            if (
                not self._active
                or generation != self._generation
                or physical not in self._pressed
            ):
                return
            tokens = set(self._pressed.values())
            keyboard = self._keyboard
        if not any(token.strip("<>") not in _MODIFIERS for token in tokens):
            self._finish(
                generation,
                error="Choose a regular key or combine it with Ctrl, Alt, Shift, or Win.",
            )
            return
        shortcut = "+".join(sorted(tokens, key=self._token_order))
        try:
            if keyboard is None or not keyboard.HotKey.parse(shortcut):
                raise ValueError("The shortcut is empty.")
        except Exception:  # noqa: BLE001 - input errors must not leave capture running
            self._finish(generation, error="This shortcut is not supported. Try another key.")
            return
        self._finish(generation, result=HotkeySettings(keyboard=shortcut, mouse_button=""))

    def _on_click(
        self, generation: int, x: int, y: int, button: Any, pressed: bool
    ) -> None:
        with self._state_lock:
            if not self._active or generation != self._generation:
                return
            name = self._mouse_buttons.get(button)
            if name is None:
                return
            if not pressed:
                self._held_mouse.discard(button)
                return
            if button in self._held_mouse:
                return
            self._held_mouse.add(button)
        if self.mouse_filter is not None:
            try:
                if not self.mouse_filter(x, y):
                    return
            except Exception:  # noqa: BLE001 - a UI filter must not kill capture listeners
                _LOG.warning("Shortcut capture mouse filter failed; this click was ignored.")
                return
        self._finish(generation, result=HotkeySettings(keyboard="", mouse_button=name))

    def _cleanup_locked(self) -> None:
        with self._state_lock:
            self._active = False
            keyboard_listener, mouse_listener = self._keyboard_listener, self._mouse_listener
            timer = self._timer
            self._keyboard_listener = None
            self._mouse_listener = None
            self._keyboard = None
            self._mouse_buttons = {}
            self._timer = None
            self._pressed.clear()
            self._held_mouse.clear()
        if timer is not None:
            timer.cancel()
        HotkeyManager._release_listener(keyboard_listener)
        HotkeyManager._release_listener(mouse_listener)

    def _finish(
        self,
        generation: int,
        result: HotkeySettings | None = None,
        error: str = "",
    ) -> None:
        with self._state_lock:
            if not self._active or generation != self._generation:
                return
            self._active = False
        with self._lifecycle_lock:
            if generation != self._generation:
                return
            self._cleanup_locked()
            try:
                if result is not None:
                    self.on_capture(result)
                elif error:
                    self.on_error(error)
            except Exception:  # noqa: BLE001 - GUI callbacks must not crash an input thread
                _LOG.exception("Shortcut capture callback failed.")

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                if self._active:
                    return
                self._generation += 1
                generation = self._generation
                self._active = True
                self._pressed.clear()
                self._held_mouse.clear()
            try:
                keyboard = importlib.import_module("pynput.keyboard")
                mouse = importlib.import_module("pynput.mouse")
                self._keyboard = keyboard
                self._mouse_buttons = {
                    getattr(mouse.Button, name): name for name in MOUSE_BUTTONS
                }
                self._keyboard_listener = keyboard.Listener(
                    on_press=lambda key: self._on_press(generation, key),
                    on_release=lambda key: self._on_release(generation, key),
                    suppress=False,
                )
                self._mouse_listener = mouse.Listener(
                    on_click=lambda x, y, button, pressed: self._on_click(
                        generation, x, y, button, pressed
                    ),
                    suppress=False,
                )
                for listener in (self._keyboard_listener, self._mouse_listener):
                    listener.start()
                    listener.wait()
                with self._state_lock:
                    if self._active and generation == self._generation:
                        self._timer = threading.Timer(
                            self.timeout_seconds,
                            lambda: self._finish(
                                generation,
                                error="Shortcut capture timed out. Click Capture and try again.",
                            ),
                        )
                        self._timer.daemon = True
                        self._timer.start()
            except BaseException as exc:
                if not isinstance(exc, Exception):
                    self.stop()
                    raise
                self._finish(
                    generation,
                    error="Shortcut capture could not start. Check your input devices and try again.",
                )

    def stop(self) -> None:
        """Stop only this capture session; do not emit a cancellation callback."""
        with self._lifecycle_lock:
            with self._state_lock:
                self._generation += 1
            self._cleanup_locked()
