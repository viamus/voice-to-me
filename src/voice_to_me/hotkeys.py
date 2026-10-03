"""Process-owned recording and manual paste shortcut listeners."""

from __future__ import annotations

import importlib
import logging
import threading
from collections.abc import Callable
from typing import Any

from voice_to_me.config import MOUSE_BUTTONS, HotkeySettings
from voice_to_me.paste import INPUT_MARKER

_LOG = logging.getLogger(__name__)
_PRESS_MESSAGES = frozenset({0x0100, 0x0104})
_RELEASE_MESSAGES = frozenset({0x0101, 0x0105})
_MODIFIER_VKS = frozenset({0x10, 0x11, 0x12, 0x5B, 0x5C, *range(0xA0, 0xA6)})


class HotkeyError(RuntimeError):
    """A configured input listener could not start."""


class HotkeyManager:
    def __init__(
        self,
        settings: HotkeySettings,
        on_toggle: Callable[[], None],
        *,
        mouse_filter: Callable[[int, int], bool] | None = None,
        on_paste: Callable[[], None] | None = None,
        paste_mouse_filter: Callable[[int, int], bool] | None = None,
    ):
        self.settings = settings
        self.on_toggle = on_toggle
        self.mouse_filter = mouse_filter
        self.on_paste = on_paste
        self.paste_mouse_filter = paste_mouse_filter
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._keyboard_listener: Any | None = None
        self._mouse_listener: Any | None = None
        self._hotkey: Any | None = None
        self._paste_hotkey: Any | None = None
        self._paste_keys: frozenset[Any] = frozenset()
        self._paste_modifiers: frozenset[Any] = frozenset()
        self._paste_triggers: frozenset[Any] = frozenset()
        self._paste_suppressed_raw: set[Any] = set()
        self._win32_keyboard = False
        self._paste_armed = False
        self._mouse_button: Any | None = None
        self._paste_mouse_button: Any | None = None
        self._paste_mouse_name = ""
        self._pressed_raw: dict[Any, Any] = {}
        self._canonical_counts: dict[Any, int] = {}
        self._mouse_pressed = False
        self._paste_mouse_pressed = False
        self._paste_mouse_allowed = False
        self._active = False
        self._generation = 0

    @staticmethod
    def _physical_key(key: Any) -> Any:
        vk = getattr(key, "vk", None)
        if vk is None:
            vk = getattr(getattr(key, "value", None), "vk", None)
        return ("vk", vk) if vk is not None else key

    def _activate(self, generation: int) -> None:
        if self._active and generation == self._generation:
            try:
                self.on_toggle()
            except Exception:  # noqa: BLE001 - an app failure must not kill its input thread
                _LOG.exception("Recording toggle callback failed.")

    def _arm_paste(self, generation: int) -> None:
        with self._state_lock:
            if self._active and generation == self._generation:
                # A trigger pressed before its modifiers was ordinary typing.
                # Do not turn that earlier native key into a delayed paste action.
                if self._win32_keyboard and not self._paste_triggers <= {
                    self._pressed_raw[physical]
                    for physical in self._paste_suppressed_raw
                    if physical in self._pressed_raw
                }:
                    return
                self._paste_armed = True

    def _activate_paste(self, generation: int) -> None:
        if self._active and generation == self._generation and self.on_paste is not None:
            try:
                self.on_paste()
            except Exception:  # noqa: BLE001 - callbacks must not kill a native input thread
                _LOG.warning("Paste shortcut callback failed.")

    @staticmethod
    def _event_filter(_message: int, data: Any) -> bool:
        """Ignore our own SendInput events without suppressing input in other apps."""
        extra = getattr(data, "dwExtraInfo", 0)
        return getattr(extra, "value", extra) != INPUT_MARKER

    @classmethod
    def _is_modifier(cls, key: Any) -> bool:
        physical = cls._physical_key(key)
        if isinstance(physical, tuple) and physical[0] == "vk":
            return physical[1] in _MODIFIER_VKS
        return isinstance(key, str) and key.split("_")[0] in {"ctrl", "alt", "shift", "cmd"}

    def _keyboard_event_filter(self, message: int, data: Any, generation: int) -> bool:
        """Reserve paste trigger keys while allowing shared modifiers and other keys.

        Pynput normally queues keyboard callbacks after the native filter. Dispatch
        here instead so a fast chord sees the preceding modifier immediately.
        Returning False prevents duplicate callbacks; suppress_event additionally
        prevents only the reserved trigger from reaching the target application.
        """
        if not self._event_filter(message, data):
            return False  # Our Ctrl+V still reaches the target application.
        listener = self._keyboard_listener
        if (listener is None or not self._active or generation != self._generation
                or message not in _PRESS_MESSAGES | _RELEASE_MESSAGES):
            return True
        vk = getattr(data, "vkCode", None)
        if vk is None:
            return True
        try:
            key = listener._event_to_key(message, vk)
        except OSError:
            return True
        if key is None:
            return True
        physical, canonical = self._physical_key(key), listener.canonical(key)
        pressed = message in _PRESS_MESSAGES
        with self._state_lock:
            if not self._active or generation != self._generation:
                return True
            self._win32_keyboard = True
            suppress = physical in self._paste_suppressed_raw
            if (pressed and physical not in self._pressed_raw
                    and canonical in self._paste_triggers
                    and self._paste_modifiers <= self._canonical_counts.keys()):
                suppress = True
                self._paste_suppressed_raw.add(physical)
        if pressed:
            self._on_press(key, generation)
        else:
            self._on_release(key, generation)
            with self._state_lock:
                self._paste_suppressed_raw.discard(physical)
        if suppress:
            listener.suppress_event()
        return False

    def _mouse_event_filter(self, message: int, data: Any, generation: int) -> bool:
        """Consume reserved middle/extra buttons so paste cannot navigate the app."""
        if not self._event_filter(message, data):
            return False
        listener = self._mouse_listener
        if (listener is None or not self._active or generation != self._generation
                or self._paste_mouse_name not in {"middle", "x1", "x2"}):
            return True
        name = None
        if message in {0x0207, 0x0208}:
            name = "middle"
        elif message in {0x020B, 0x020C}:
            name = {1: "x1", 2: "x2"}.get(getattr(data, "mouseData", 0) >> 16)
        if name != self._paste_mouse_name:
            return True
        self._on_click(data.pt.x, data.pt.y, self._paste_mouse_button,
                       message in {0x0207, 0x020B}, generation)
        listener.suppress_event()
        return False

    def _on_press(self, key: Any, generation: int) -> None:
        listener, hotkey, paste_hotkey = (
            self._keyboard_listener, self._hotkey, self._paste_hotkey,
        )
        if listener is None:
            return
        physical = self._physical_key(key)
        canonical = listener.canonical(key)
        with self._state_lock:
            if (
                not self._active
                or generation != self._generation
                or physical in self._pressed_raw
            ):
                return
            self._pressed_raw[physical] = canonical
            count = self._canonical_counts.get(canonical, 0)
            self._canonical_counts[canonical] = count + 1
        if count == 0:
            if hotkey is not None:
                hotkey.press(canonical)
            if paste_hotkey is not None:
                paste_hotkey.press(canonical)

    def _on_release(self, key: Any, generation: int) -> None:
        hotkey, paste_hotkey = self._hotkey, self._paste_hotkey
        if self._keyboard_listener is None:
            return
        physical = self._physical_key(key)
        with self._state_lock:
            if generation != self._generation or physical not in self._pressed_raw:
                return
            canonical = self._pressed_raw.pop(physical)
            count = self._canonical_counts[canonical] - 1
            if count:
                self._canonical_counts[canonical] = count
            else:
                self._canonical_counts.pop(canonical, None)
            paste_ready = self._paste_armed and not any(
                held in self._canonical_counts for held in self._paste_keys
            )
            if paste_ready:
                self._paste_armed = False
        if count == 0:
            if hotkey is not None:
                hotkey.release(canonical)
            if paste_hotkey is not None:
                paste_hotkey.release(canonical)
        if paste_ready:
            self._activate_paste(generation)

    def _on_click(self, x: int, y: int, button: Any, pressed: bool, generation: int) -> None:
        paste_release = False
        with self._state_lock:
            if (
                not self._active
                or generation != self._generation
            ):
                return
            if button == self._paste_mouse_button:
                if not pressed:
                    paste_release = self._paste_mouse_pressed and self._paste_mouse_allowed
                    self._paste_mouse_pressed = False
                    self._paste_mouse_allowed = False
                elif self._paste_mouse_pressed:
                    return
                else:
                    self._paste_mouse_pressed = True
            elif button != self._mouse_button:
                return
            elif not pressed:
                self._mouse_pressed = False
                return
            elif self._mouse_pressed:
                return
            else:
                self._mouse_pressed = True
        if button == self._paste_mouse_button:
            if not pressed:
                if paste_release and self._allows_mouse(self.paste_mouse_filter, x, y):
                    self._activate_paste(generation)
                return
            allowed = self._allows_mouse(self.paste_mouse_filter, x, y)
            with self._state_lock:
                if self._active and generation == self._generation:
                    self._paste_mouse_allowed = allowed
        elif self._allows_mouse(self.mouse_filter, x, y):
            self._activate(generation)

    @staticmethod
    def _allows_mouse(mouse_filter: Callable[[int, int], bool] | None, x: int, y: int) -> bool:
        if mouse_filter is None:
            return True
        try:
            return mouse_filter(x, y)
        except Exception:  # noqa: BLE001 - a filter must not kill the mouse listener
            _LOG.warning("Mouse shortcut filter failed; this click was ignored.")
            return False

    @staticmethod
    def _release_listener(listener: Any | None) -> None:
        if listener is None:
            return
        try:
            listener.stop()
        except Exception:  # noqa: BLE001 - always attempt to join after stopping fails
            _LOG.debug("Input listener could not be stopped.", exc_info=True)
        if listener is not threading.current_thread():
            try:
                listener.join(timeout=1.0)
            except Exception:  # noqa: BLE001 - native failures must not prevent other cleanup
                # Pynput can re-raise a native hook failure from join().
                _LOG.debug("Input listener could not be joined.", exc_info=True)

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._active:
                return
            self._generation += 1
            generation = self._generation
            keyboard_shortcut = self.settings.keyboard.strip()
            mouse_name = self.settings.mouse_button.strip().lower()
            paste_shortcut = self.settings.paste_keyboard.strip()
            paste_mouse_name = self.settings.paste_mouse_button.strip().lower()
            if not keyboard_shortcut and not mouse_name and not paste_shortcut and not paste_mouse_name:
                # Accessible button-only mode: no global listeners are installed.
                self._active = True
                return
            if any(name not in ("", *MOUSE_BUTTONS) for name in (mouse_name, paste_mouse_name)):
                raise HotkeyError("The mouse button must be left, right, middle, x1, x2, or empty.")
            if mouse_name and mouse_name == paste_mouse_name:
                raise HotkeyError("Use different shortcuts for recording and pasting.")
            if (paste_shortcut or paste_mouse_name) and self.on_paste is None:
                raise HotkeyError("The paste shortcut does not have a paste action.")
            try:
                if keyboard_shortcut or paste_shortcut:
                    keyboard = importlib.import_module("pynput.keyboard")
                    record_keys = keyboard.HotKey.parse(keyboard_shortcut) if keyboard_shortcut else []
                    paste_keys = keyboard.HotKey.parse(paste_shortcut) if paste_shortcut else []
                    for keys in (record_keys, paste_keys):
                        if keys and len(keys) != len(set(keys)):
                            raise ValueError("The shortcut contains duplicate keys.")
                    if (keyboard_shortcut and not record_keys) or (paste_shortcut and not paste_keys):
                        raise ValueError("The shortcut is empty.")
                    if paste_keys and all(self._is_modifier(key) for key in paste_keys):
                        raise HotkeyError(
                            "Choose a regular key, function key or mouse button for pasting."
                        )
                    if record_keys and paste_keys and (
                        set(record_keys) <= set(paste_keys) or set(paste_keys) <= set(record_keys)
                    ):
                        raise ValueError("Use different shortcuts for recording and pasting.")
                    if record_keys:
                        self._hotkey = keyboard.HotKey(record_keys, lambda: self._activate(generation))
                    if paste_keys:
                        self._paste_keys = frozenset(paste_keys)
                        self._paste_modifiers = frozenset(
                            key for key in paste_keys if self._is_modifier(key)
                        )
                        self._paste_triggers = self._paste_keys - self._paste_modifiers
                        if len(self._paste_triggers) > 1:
                            raise HotkeyError(
                                "Choose one paste key, optionally with Ctrl, Alt, Shift or Win."
                            )
                        self._paste_hotkey = keyboard.HotKey(
                            paste_keys, lambda: self._arm_paste(generation),
                        )
                    self._keyboard_listener = keyboard.Listener(
                        on_press=lambda key: self._on_press(key, generation),
                        on_release=lambda key: self._on_release(key, generation),
                        suppress=False,
                        win32_event_filter=lambda message, data: self._keyboard_event_filter(
                            message, data, generation,
                        ),
                    )
                if mouse_name or paste_mouse_name:
                    mouse = importlib.import_module("pynput.mouse")
                    if mouse_name:
                        self._mouse_button = getattr(mouse.Button, mouse_name)
                    if paste_mouse_name:
                        self._paste_mouse_button = getattr(mouse.Button, paste_mouse_name)
                        self._paste_mouse_name = paste_mouse_name
                    self._mouse_listener = mouse.Listener(
                        on_click=lambda x, y, button, pressed: self._on_click(
                            x, y, button, pressed, generation
                        ),
                        suppress=False,
                        win32_event_filter=lambda message, data: self._mouse_event_filter(
                            message, data, generation,
                        ),
                    )
                self._active = True
                for listener in (self._keyboard_listener, self._mouse_listener):
                    if listener is not None:
                        listener.start()
                        listener.wait()
            except BaseException as exc:
                self.stop()
                if not isinstance(exc, Exception) or isinstance(exc, HotkeyError):
                    raise
                raise HotkeyError(
                    "The shortcuts could not be registered. Check the configuration and try again."
                ) from exc

    def reconfigure(self, new_settings: HotkeySettings) -> None:
        """Apply new listeners, restoring the previous configuration after a failure."""
        with self._lifecycle_lock:
            previous_settings = self.settings
            was_active = self._active
            self.stop()
            self.settings = new_settings
            try:
                self.start()
            except Exception as exc:
                self.settings = previous_settings
                if was_active:
                    try:
                        self.start()
                    except Exception as rollback_error:
                        raise HotkeyError(
                            "The new shortcut failed, and the previous shortcut could not be "
                            "restored. Use the recording button and restart the app."
                        ) from rollback_error
                raise HotkeyError(
                    "The new shortcut could not be registered. The previous configuration "
                    "was restored."
                ) from exc

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._active = False
            self._generation += 1
            keyboard_listener = self._keyboard_listener
            mouse_listener = self._mouse_listener
            self._keyboard_listener = None
            self._mouse_listener = None
            self._hotkey = None
            self._paste_hotkey = None
            self._paste_keys = frozenset()
            self._paste_modifiers = frozenset()
            self._paste_triggers = frozenset()
            self._mouse_button = None
            self._paste_mouse_button = None
            self._paste_mouse_name = ""
            with self._state_lock:
                self._pressed_raw.clear()
                self._canonical_counts.clear()
                self._mouse_pressed = False
                self._paste_mouse_pressed = False
                self._paste_mouse_allowed = False
                self._paste_armed = False
                self._paste_suppressed_raw.clear()
                self._win32_keyboard = False
            self._release_listener(keyboard_listener)
            self._release_listener(mouse_listener)
