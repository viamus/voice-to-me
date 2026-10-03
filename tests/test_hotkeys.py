from __future__ import annotations

from types import SimpleNamespace

import pytest

from voice_to_me.config import HotkeySettings
from voice_to_me.hotkeys import HotkeyError, HotkeyManager
from voice_to_me.paste import INPUT_MARKER


class SuppressedEvent(Exception):
    """Simulated pynput signal: the native event does not reach other apps."""


class FakeListener:
    def __init__(self, **kwargs):
        self.callbacks = kwargs
        self.started = False
        self.stopped = False
        self.joined = False
        self.start_error = False
        self.suppressed = 0

    def canonical(self, key):
        return {"ctrl_l": "ctrl", "ctrl_r": "ctrl"}.get(key, key)

    def _event_to_key(self, message, vk):
        return {
            0xA2: "ctrl_l", 0xA3: "ctrl_r", 0xA4: "alt", 0xA0: "shift",
            0x77: "f8", 0x78: "f9",
        }.get(vk, chr(vk).lower())

    def suppress_event(self):
        self.suppressed += 1
        raise SuppressedEvent()

    def start(self):
        self.started = True
        if self.start_error:
            raise OSError("listener startup failure")

    def wait(self):
        pass

    def stop(self):
        self.stopped = True

    def join(self, timeout):
        self.joined = True


class FakeHotKey:
    def __init__(self, keys, on_activate):
        self.keys = set(keys)
        self.pressed = set()
        self.on_activate = on_activate

    @staticmethod
    def parse(value):
        if value == "invalid":
            raise ValueError("invalid shortcut")
        return [key.strip("<>") for key in value.split("+")]

    def press(self, key):
        if key in self.keys:
            self.pressed.add(key)
            if self.pressed == self.keys:
                self.on_activate()

    def release(self, key):
        self.pressed.discard(key)


@pytest.fixture
def inputs(monkeypatch):
    keyboard_listeners, mouse_listeners = [], []

    def keyboard_listener(**kwargs):
        listener = FakeListener(**kwargs)
        keyboard_listeners.append(listener)
        return listener

    def mouse_listener(**kwargs):
        listener = FakeListener(**kwargs)
        mouse_listeners.append(listener)
        return listener

    modules = {
        "pynput.keyboard": SimpleNamespace(HotKey=FakeHotKey, Listener=keyboard_listener),
        "pynput.mouse": SimpleNamespace(
            Button=SimpleNamespace(left="mouse_left", right="mouse_right", middle="mouse_middle",
                                   x1="mouse_x1", x2="mouse_x2"),
            Listener=mouse_listener,
        ),
    }
    monkeypatch.setattr("voice_to_me.hotkeys.importlib.import_module", modules.__getitem__)
    return keyboard_listeners, mouse_listeners


def press_chord(listener):
    for key in ("ctrl_l", "alt", "space"):
        listener.callbacks["on_press"](key)


def release_chord(listener):
    for key in ("space", "alt", "ctrl_l"):
        listener.callbacks["on_release"](key)


def test_keyboard_toggle_fires_once_until_keys_are_released(inputs):
    toggles = []
    manager = HotkeyManager(HotkeySettings(), lambda: toggles.append(True))
    manager.start()
    listener = inputs[0][0]
    press_chord(listener)
    for _ in range(10):
        listener.callbacks["on_press"]("space")
    assert toggles == [True]
    release_chord(listener)
    press_chord(listener)
    assert toggles == [True, True]
    manager.stop()
    assert listener.stopped and listener.joined
    assert listener.callbacks["suppress"] is False


def test_keyboard_chord_can_be_retriggered_without_releasing_modifiers(inputs):
    toggles = []
    manager = HotkeyManager(HotkeySettings(), lambda: toggles.append(True))
    manager.start()
    listener = inputs[0][0]
    press_chord(listener)
    listener.callbacks["on_release"]("space")
    listener.callbacks["on_press"]("space")
    assert len(toggles) == 2
    manager.stop()


def test_both_control_keys_do_not_release_a_still_held_modifier(inputs):
    toggles = []
    manager = HotkeyManager(HotkeySettings(), lambda: toggles.append(True))
    manager.start()
    listener = inputs[0][0]
    listener.callbacks["on_press"]("ctrl_l")
    listener.callbacks["on_press"]("ctrl_r")
    listener.callbacks["on_release"]("ctrl_l")
    listener.callbacks["on_press"]("alt")
    listener.callbacks["on_press"]("space")
    assert len(toggles) == 1
    manager.stop()


def test_physical_virtual_key_prevents_repeat_when_character_case_changes(inputs):
    manager = HotkeyManager(HotkeySettings(), lambda: None)
    assert manager._physical_key(SimpleNamespace(vk=65, char="A")) == (
        manager._physical_key(SimpleNamespace(vk=65, char="a"))
    )


def test_optional_mouse_toggle_ignores_other_buttons_and_repeat(inputs):
    toggles = []
    manager = HotkeyManager(
        HotkeySettings(mouse_button="x1"), lambda: toggles.append(True)
    )
    manager.start()
    listener = inputs[1][0]
    click = listener.callbacks["on_click"]
    click(0, 0, "mouse_left", True)
    click(0, 0, "mouse_x2", True)
    click(0, 0, "mouse_x1", True)
    click(0, 0, "mouse_x1", True)
    assert len(toggles) == 1
    click(0, 0, "mouse_x1", False)
    click(0, 0, "mouse_x1", True)
    assert len(toggles) == 2
    manager.stop()
    assert listener.stopped and listener.joined


def test_mouse_only_settings_are_supported(inputs):
    toggles = []
    manager = HotkeyManager(
        HotkeySettings(keyboard="", mouse_button="x2"), lambda: toggles.append(True)
    )
    manager.start()
    assert inputs[0] == []
    inputs[1][0].callbacks["on_click"](0, 0, "mouse_x2", True)
    assert len(toggles) == 1
    manager.stop()


@pytest.mark.parametrize(
    "settings",
    [
        HotkeySettings(mouse_button="wheel_up"),
        HotkeySettings(keyboard="invalid"),
        HotkeySettings(keyboard="<ctrl>+<ctrl>"),
    ],
)
def test_bad_settings_fail_cleanly_without_active_listener(inputs, settings):
    manager = HotkeyManager(settings, lambda: None)
    with pytest.raises(HotkeyError):
        manager.start()
    assert not manager._active
    assert manager._keyboard_listener is None
    assert manager._mouse_listener is None


def test_button_only_mode_does_not_install_listeners(inputs):
    manager = HotkeyManager(HotkeySettings(keyboard="", mouse_button=""), lambda: None)
    manager.start()
    assert manager._active
    assert inputs == ([], [])
    manager.stop()
    assert not manager._active


def test_partial_listener_start_failure_stops_keyboard_and_mouse(monkeypatch):
    keyboard_listener, mouse_listener = FakeListener(), FakeListener()
    mouse_listener.start_error = True
    modules = {
        "pynput.keyboard": SimpleNamespace(
            HotKey=FakeHotKey, Listener=lambda **_kwargs: keyboard_listener,
        ),
        "pynput.mouse": SimpleNamespace(
            Button=SimpleNamespace(x1="mouse_x1"), Listener=lambda **_kwargs: mouse_listener,
        ),
    }
    monkeypatch.setattr("voice_to_me.hotkeys.importlib.import_module", modules.__getitem__)
    manager = HotkeyManager(HotkeySettings(mouse_button="x1"), lambda: None)
    with pytest.raises(HotkeyError):
        manager.start()
    assert keyboard_listener.stopped and keyboard_listener.joined
    assert mouse_listener.stopped and mouse_listener.joined
    assert not manager._active


def test_start_and_stop_are_idempotent_and_old_callbacks_do_not_toggle(inputs):
    toggles = []
    manager = HotkeyManager(HotkeySettings(), lambda: toggles.append(True))
    manager.start()
    manager.start()
    assert len(inputs[0]) == 1
    previous_listener = inputs[0][0]
    manager.stop()
    manager.stop()
    press_chord(previous_listener)
    assert toggles == []
    manager.start()
    press_chord(inputs[0][1])
    assert toggles == [True]
    manager.stop()


def test_callback_exception_does_not_kill_toggle_listener(inputs):
    calls = []

    def fail():
        calls.append(True)
        raise RuntimeError("toggle failed")

    manager = HotkeyManager(HotkeySettings(), fail)
    manager.start()
    listener = inputs[0][0]
    press_chord(listener)
    release_chord(listener)
    press_chord(listener)
    assert len(calls) == 2
    manager.stop()


@pytest.mark.parametrize("button", ["left", "right", "middle", "x1", "x2"])
def test_all_standard_mouse_buttons_toggle_once_per_press(inputs, button):
    toggles = []
    manager = HotkeyManager(
        HotkeySettings(keyboard="", mouse_button=button), lambda: toggles.append(True)
    )
    manager.start()
    click = inputs[1][0].callbacks["on_click"]
    click(0, 0, "mouse_unknown", True)
    click(0, 0, f"mouse_{button}", False)
    click(0, 0, f"mouse_{button}", True)
    click(0, 0, f"mouse_{button}", True)
    assert len(toggles) == 1
    click(0, 0, f"mouse_{button}", False)
    click(0, 0, f"mouse_{button}", True)
    assert len(toggles) == 2
    assert inputs[1][0].callbacks["suppress"] is False
    manager.stop()


def test_mouse_filter_ignores_press_until_release_then_allows_next_click(inputs):
    toggles, filtered = [], []

    def allow_after_first_press(x, y):
        filtered.append((x, y))
        return len(filtered) > 1

    manager = HotkeyManager(
        HotkeySettings(keyboard="", mouse_button="left"),
        lambda: toggles.append(True),
        mouse_filter=allow_after_first_press,
    )
    manager.start()
    click = inputs[1][0].callbacks["on_click"]
    click(100, 200, "mouse_left", True)
    click(300, 400, "mouse_left", True)
    assert toggles == []
    assert filtered == [(100, 200)]
    click(300, 400, "mouse_left", False)
    assert filtered == [(100, 200)]
    click(300, 400, "mouse_left", True)
    assert toggles == [True]
    assert filtered == [(100, 200), (300, 400)]
    manager.stop()


def test_mouse_filter_failure_ignores_click_without_logging_coordinates(inputs, caplog):
    toggles = []

    def fail(x, y):
        raise OSError(f"private coordinate {x}, {y}")

    manager = HotkeyManager(
        HotkeySettings(keyboard="", mouse_button="right"),
        lambda: toggles.append(True),
        mouse_filter=fail,
    )
    manager.start()
    click = inputs[1][0].callbacks["on_click"]
    click(123456, 789012, "mouse_right", True)
    assert toggles == []
    assert "Mouse shortcut filter failed" in caplog.text
    assert "123456" not in caplog.text and "789012" not in caplog.text
    assert "private coordinate" not in caplog.text
    manager.stop()


def test_mouse_filter_is_preserved_when_shortcut_is_reconfigured(inputs):
    filtered = []
    manager = HotkeyManager(
        HotkeySettings(keyboard="", mouse_button="left"),
        lambda: None,
        mouse_filter=lambda x, y: filtered.append((x, y)) or False,
    )
    manager.start()
    manager.reconfigure(HotkeySettings(keyboard="", mouse_button="right"))
    inputs[1][-1].callbacks["on_click"](50, 60, "mouse_right", True)
    assert filtered == [(50, 60)]
    manager.stop()


def test_reconfigure_applies_new_shortcut_and_stale_callbacks_are_ignored(inputs):
    toggles = []
    manager = HotkeyManager(HotkeySettings(), lambda: toggles.append(True))
    manager.start()
    previous = inputs[0][0]
    replacement = HotkeySettings(keyboard="<ctrl>+m", mouse_button="x2")
    manager.reconfigure(replacement)
    assert manager.settings == replacement
    assert previous.stopped and previous.joined
    press_chord(previous)
    assert toggles == []
    current = inputs[0][1]
    current.callbacks["on_press"]("ctrl_l")
    current.callbacks["on_press"]("m")
    assert toggles == [True]
    manager.stop()


def test_reconfigure_bad_shortcut_restores_previous_active_configuration(inputs):
    toggles = []
    previous_settings = HotkeySettings()
    manager = HotkeyManager(previous_settings, lambda: toggles.append(True))
    manager.start()
    with pytest.raises(HotkeyError, match="previous configuration was restored"):
        manager.reconfigure(HotkeySettings(keyboard="invalid"))
    assert manager.settings == previous_settings
    assert manager._active
    press_chord(inputs[0][-1])
    assert toggles == [True]
    manager.stop()


def test_reconfigure_stopped_manager_starts_new_shortcut(inputs):
    manager = HotkeyManager(HotkeySettings(), lambda: None)
    replacement = HotkeySettings(keyboard="m", mouse_button="")
    manager.reconfigure(replacement)
    assert manager.settings == replacement
    assert manager._active
    assert len(inputs[0]) == 1
    manager.stop()


def test_reconfigure_stopped_manager_failure_restores_settings_without_listeners(inputs):
    previous_settings = HotkeySettings()
    manager = HotkeyManager(previous_settings, lambda: None)
    with pytest.raises(HotkeyError):
        manager.reconfigure(HotkeySettings(keyboard="invalid"))
    assert manager.settings == previous_settings
    assert not manager._active
    assert inputs == ([], [])


def test_reconfigure_can_switch_to_button_only_mode(inputs):
    manager = HotkeyManager(HotkeySettings(), lambda: None)
    manager.start()
    previous_listener = inputs[0][0]
    manager.reconfigure(HotkeySettings(keyboard="", mouse_button=""))
    assert manager._active
    assert manager._keyboard_listener is None
    assert previous_listener.stopped
    manager.stop()


def test_reconfigure_reports_when_rollback_listener_also_fails(monkeypatch):
    listeners = []

    def create_listener(**kwargs):
        listener = FakeListener(**kwargs)
        listener.start_error = bool(listeners)
        listeners.append(listener)
        return listener

    modules = {
        "pynput.keyboard": SimpleNamespace(HotKey=FakeHotKey, Listener=create_listener),
    }
    monkeypatch.setattr("voice_to_me.hotkeys.importlib.import_module", modules.__getitem__)
    previous_settings = HotkeySettings()
    manager = HotkeyManager(previous_settings, lambda: None)
    manager.start()
    with pytest.raises(HotkeyError, match="previous shortcut could not be restored"):
        manager.reconfigure(HotkeySettings(keyboard="m"))
    assert manager.settings == previous_settings
    assert not manager._active
    assert all(listener.stopped for listener in listeners)


def test_independent_mouse_buttons_record_on_press_and_paste_on_release(inputs):
    calls = []
    manager = HotkeyManager(
        HotkeySettings(keyboard="", mouse_button="x1", paste_mouse_button="x2"),
        lambda: calls.append("record"), on_paste=lambda: calls.append("paste"),
    )
    manager.start()
    assert inputs[0] == [] and len(inputs[1]) == 1
    click = inputs[1][0].callbacks["on_click"]
    click(0, 0, "mouse_x2", False)
    click(0, 0, "mouse_x1", True)
    click(0, 0, "mouse_x2", True)
    for _ in range(5):
        click(0, 0, "mouse_x2", True)
    assert calls == ["record"]
    click(0, 0, "mouse_x2", False)
    click(0, 0, "mouse_x2", False)
    assert calls == ["record", "paste"]
    click(0, 0, "mouse_x1", False)
    click(0, 0, "mouse_x1", True)
    click(0, 0, "mouse_x2", True)
    click(0, 0, "mouse_x2", False)
    assert calls == ["record", "paste", "record", "paste"]
    manager.stop()


def test_keyboard_paste_waits_for_every_chord_key_and_both_physical_modifiers(inputs):
    calls = []
    manager = HotkeyManager(
        HotkeySettings(keyboard="<f8>", paste_keyboard="<ctrl>+m"),
        lambda: calls.append("record"), on_paste=lambda: calls.append("paste"),
    )
    manager.start()
    listener = inputs[0][0]
    press, release = listener.callbacks["on_press"], listener.callbacks["on_release"]
    press("f8")
    release("f8")
    press("ctrl_l")
    press("ctrl_r")
    press("m")
    for _ in range(5):
        press("m")
    release("m")
    press("m")
    release("m")
    release("ctrl_l")
    assert calls == ["record"]
    release("ctrl_r")
    assert calls == ["record", "paste"]
    release("ctrl_r")
    press("m")  # An incomplete chord cannot paste.
    release("m")
    assert calls == ["record", "paste"]
    press("ctrl_l")
    press("m")
    release("ctrl_l")
    assert calls == ["record", "paste"]
    release("m")
    assert calls == ["record", "paste", "paste"]
    manager.stop()


def test_paste_only_keyboard_setting_works_and_fires_after_release(inputs):
    calls = []
    manager = HotkeyManager(
        HotkeySettings(keyboard="", paste_keyboard="<f9>"),
        lambda: calls.append("record"), on_paste=lambda: calls.append("paste"),
    )
    manager.start()
    listener = inputs[0][0]
    listener.callbacks["on_press"]("f9")
    assert calls == []
    listener.callbacks["on_release"]("f9")
    assert calls == ["paste"]
    manager.stop()


def test_paste_callback_failure_does_not_stop_later_shortcuts(inputs, caplog):
    calls = []

    def fail():
        calls.append("paste")
        raise RuntimeError("private paste text")

    manager = HotkeyManager(
        HotkeySettings(keyboard="", paste_mouse_button="middle"), lambda: None, on_paste=fail,
    )
    manager.start()
    click = inputs[1][0].callbacks["on_click"]
    for _ in range(2):
        click(0, 0, "mouse_middle", True)
        click(0, 0, "mouse_middle", False)
    assert calls == ["paste", "paste"]
    assert "Paste shortcut callback failed" in caplog.text
    assert "private paste text" not in caplog.text
    manager.stop()


def test_paste_mouse_guard_checks_press_and_release_and_ignores_filtered_clicks(inputs):
    calls, filtered = [], []

    def allow(x, y):
        filtered.append((x, y))
        return x > 0

    manager = HotkeyManager(
        HotkeySettings(keyboard="", mouse_button="x1", paste_mouse_button="left"),
        lambda: calls.append("record"), on_paste=lambda: calls.append("paste"),
        mouse_filter=lambda x, y: True, paste_mouse_filter=allow,
    )
    manager.start()
    click = inputs[1][0].callbacks["on_click"]
    click(-1, 0, "mouse_left", True)
    click(1, 0, "mouse_left", False)
    assert filtered == [(-1, 0)] and calls == []
    click(1, 0, "mouse_left", True)
    click(-1, 0, "mouse_left", False)
    assert calls == []
    click(1, 0, "mouse_left", True)
    click(1, 0, "mouse_left", False)
    click(-1, 0, "mouse_x1", True)
    assert calls == ["paste", "record"]
    manager.stop()


def test_tagged_ctrl_v_injection_cannot_trigger_recording_or_paste_shortcuts(inputs):
    calls = []
    manager = HotkeyManager(
        HotkeySettings(keyboard="<ctrl>+v", paste_keyboard="<f9>", paste_mouse_button="x2"),
        lambda: calls.append("record"), on_paste=lambda: calls.append("paste"),
    )
    manager.start()
    listener = inputs[0][0]
    event_filter = listener.callbacks["win32_event_filter"]
    injected = SimpleNamespace(dwExtraInfo=INPUT_MARKER)
    for name, key in (("on_press", "ctrl_l"), ("on_press", "v"),
                      ("on_release", "v"), ("on_release", "ctrl_l"),
                      ("on_press", "f9"), ("on_release", "f9")):
        if event_filter(0, injected):
            listener.callbacks[name](key)
    assert calls == []
    assert event_filter(0, SimpleNamespace(dwExtraInfo=0))
    assert event_filter(0, SimpleNamespace(dwExtraInfo=12345))
    assert not event_filter(0, SimpleNamespace(dwExtraInfo=SimpleNamespace(value=INPUT_MARKER)))
    assert not inputs[1][0].callbacks["win32_event_filter"](0, injected)
    listener.callbacks["on_press"]("ctrl_l")
    listener.callbacks["on_press"]("v")
    assert calls == ["record"]
    manager.stop()


@pytest.mark.parametrize("settings", [
    HotkeySettings(keyboard="", mouse_button="x1", paste_mouse_button="x1"),
    HotkeySettings(keyboard="<ctrl>+m", paste_keyboard="m+<ctrl>"),
    HotkeySettings(keyboard="<ctrl>+m", paste_keyboard="<ctrl>+<shift>+m"),
    HotkeySettings(keyboard="<ctrl>+<shift>+m", paste_keyboard="<ctrl>+m"),
    HotkeySettings(paste_keyboard="invalid"),
    HotkeySettings(paste_keyboard="<ctrl>+<ctrl>"),
    HotkeySettings(paste_mouse_button="wheel_up"),
    HotkeySettings(paste_keyboard="a+b"),
])
def test_bad_or_overlapping_paste_shortcut_never_installs_hooks(inputs, settings):
    manager = HotkeyManager(settings, lambda: None, on_paste=lambda: None)
    with pytest.raises(HotkeyError):
        manager.start()
    assert not manager._active
    assert inputs == ([], [])


def test_paste_binding_requires_callback(inputs):
    manager = HotkeyManager(HotkeySettings(paste_mouse_button="x2"), lambda: None)
    with pytest.raises(HotkeyError, match="paste action"):
        manager.start()
    assert inputs == ([], [])
    assert not manager._active


def test_reconfigure_clears_pending_paste_and_ignores_stale_release(inputs):
    calls = []
    manager = HotkeyManager(
        HotkeySettings(keyboard="", paste_keyboard="<f9>"), lambda: None,
        on_paste=lambda: calls.append("paste"),
    )
    manager.start()
    previous = inputs[0][0]
    previous.callbacks["on_press"]("f9")
    replacement = HotkeySettings(keyboard="", paste_mouse_button="x2")
    manager.reconfigure(replacement)
    previous.callbacks["on_release"]("f9")
    assert calls == []
    click = inputs[1][-1].callbacks["on_click"]
    click(0, 0, "mouse_x2", True)
    manager.stop()
    click(0, 0, "mouse_x2", False)
    assert calls == []


def test_reconfigure_conflict_restores_both_record_and_paste_bindings(inputs):
    calls = []
    original = HotkeySettings(keyboard="", mouse_button="x1", paste_mouse_button="x2")
    manager = HotkeyManager(original, lambda: calls.append("record"),
                            on_paste=lambda: calls.append("paste"))
    manager.start()
    with pytest.raises(HotkeyError, match="previous configuration was restored"):
        manager.reconfigure(HotkeySettings(keyboard="", mouse_button="x1", paste_mouse_button="x1"))
    assert manager.settings == original and manager._active
    click = inputs[1][-1].callbacks["on_click"]
    click(0, 0, "mouse_x1", True)
    click(0, 0, "mouse_x2", True)
    click(0, 0, "mouse_x2", False)
    assert calls == ["record", "paste"]
    manager.stop()


def native_keyboard(listener, message, vk, *, extra=0):
    """Model the Windows hook: filtered callbacks and OS propagation are distinct."""
    data = SimpleNamespace(vkCode=vk, dwExtraInfo=extra)
    try:
        propagate_callback = listener.callbacks["win32_event_filter"](message, data)
    except SuppressedEvent:
        return False
    if propagate_callback:
        callback = "on_press" if message in {0x100, 0x104} else "on_release"
        listener.callbacks[callback](listener._event_to_key(message, vk))
    return True


def native_mouse(listener, message, button, *, x=0, extra=0):
    code = {"x1": 1 << 16, "x2": 2 << 16}.get(button, 0)
    data = SimpleNamespace(mouseData=code, pt=SimpleNamespace(x=x, y=0), dwExtraInfo=extra)
    try:
        propagate_callback = listener.callbacks["win32_event_filter"](message, data)
    except SuppressedEvent:
        return False
    if propagate_callback:
        listener.callbacks["on_click"](
            x, 0, f"mouse_{button}", message in {0x201, 0x204, 0x207, 0x20B},
        )
    return True


@pytest.mark.parametrize("shortcut, vk", [("m", 0x4D), ("<f9>", 0x78)])
def test_native_keyboard_consumes_only_assigned_paste_trigger_and_balances_repeats(
    inputs, shortcut, vk,
):
    calls = []
    manager = HotkeyManager(
        HotkeySettings(keyboard="<f8>", paste_keyboard=shortcut),
        lambda: calls.append("record"), on_paste=lambda: calls.append("paste"),
    )
    manager.start()
    listener = inputs[0][0]
    assert native_keyboard(listener, 0x100, 0x41)  # Ordinary typing reaches the target.
    assert native_keyboard(listener, 0x101, 0x41)
    assert native_keyboard(listener, 0x100, 0x77)  # Recording keeps its native behavior.
    assert native_keyboard(listener, 0x101, 0x77)
    assert calls == ["record"]
    assert not native_keyboard(listener, 0x100, vk)
    assert not native_keyboard(listener, 0x100, vk)
    assert calls == ["record"]
    assert not native_keyboard(listener, 0x101, vk)
    assert calls == ["record", "paste"]
    assert listener.suppressed == 3
    assert not manager._pressed_raw and not manager._paste_suppressed_raw
    manager.stop()


def test_native_keyboard_chord_reserves_trigger_only_with_modifiers_held(inputs):
    calls = []
    manager = HotkeyManager(
        HotkeySettings(keyboard="<f8>", paste_keyboard="<ctrl>+m"), lambda: None,
        on_paste=lambda: calls.append("paste"),
    )
    manager.start()
    listener = inputs[0][0]
    assert native_keyboard(listener, 0x100, 0x4D)  # M without Ctrl remains ordinary typing.
    assert native_keyboard(listener, 0x101, 0x4D)
    assert calls == []
    assert native_keyboard(listener, 0x100, 0xA2)
    assert native_keyboard(listener, 0x100, 0xA3)
    assert native_keyboard(listener, 0x100, 0x41)  # Ctrl+A remains available.
    assert native_keyboard(listener, 0x101, 0x41)
    assert not native_keyboard(listener, 0x100, 0x4D)
    assert not native_keyboard(listener, 0x101, 0x4D)
    assert native_keyboard(listener, 0x101, 0xA2)
    assert calls == []
    assert native_keyboard(listener, 0x101, 0xA3)
    assert calls == ["paste"]
    assert listener.suppressed == 2
    manager.stop()


def test_native_trigger_pressed_before_modifier_does_not_paste_after_typing(inputs):
    calls = []
    manager = HotkeyManager(
        HotkeySettings(keyboard="<f8>", paste_keyboard="<ctrl>+m"), lambda: None,
        on_paste=lambda: calls.append("paste"),
    )
    manager.start()
    listener = inputs[0][0]
    assert native_keyboard(listener, 0x100, 0x4D)
    assert native_keyboard(listener, 0x100, 0xA2)
    assert native_keyboard(listener, 0x100, 0x4D)  # Repeats remain native, like the first press.
    assert native_keyboard(listener, 0x101, 0x4D)
    assert native_keyboard(listener, 0x101, 0xA2)
    assert calls == [] and listener.suppressed == 0
    manager.stop()


def test_native_trigger_release_stays_consumed_after_modifiers_release_first(inputs):
    calls = []
    manager = HotkeyManager(
        HotkeySettings(keyboard="", paste_keyboard="<ctrl>+m"), lambda: None,
        on_paste=lambda: calls.append("paste"),
    )
    manager.start()
    listener = inputs[0][0]
    assert native_keyboard(listener, 0x104, 0xA2)
    assert not native_keyboard(listener, 0x104, 0x4D)
    assert native_keyboard(listener, 0x105, 0xA2)
    assert not native_keyboard(listener, 0x105, 0x4D)
    assert calls == ["paste"]
    manager.stop()


def test_tagged_native_macro_passes_to_target_without_callbacks_or_suppression(inputs):
    calls = []
    manager = HotkeyManager(
        HotkeySettings(keyboard="<ctrl>+v", paste_keyboard="m", paste_mouse_button="x2"),
        lambda: calls.append("record"), on_paste=lambda: calls.append("paste"),
    )
    manager.start()
    listener = inputs[0][0]
    for message, vk in ((0x100, 0xA2), (0x100, 0x56), (0x101, 0x56), (0x101, 0xA2)):
        assert native_keyboard(listener, message, vk, extra=INPUT_MARKER)
    assert native_keyboard(listener, 0x100, 0x4D, extra=INPUT_MARKER)
    assert native_keyboard(listener, 0x101, 0x4D, extra=INPUT_MARKER)
    assert native_mouse(inputs[1][0], 0x20B, "x2", extra=INPUT_MARKER)
    assert calls == [] and listener.suppressed == 0
    assert inputs[1][0].suppressed == 0
    assert not manager._pressed_raw
    manager.stop()


@pytest.mark.parametrize("button, down, up", [
    ("x1", 0x20B, 0x20C), ("x2", 0x20B, 0x20C), ("middle", 0x207, 0x208),
])
def test_native_paste_mouse_consumes_default_action_but_preserves_other_buttons(
    inputs, button, down, up,
):
    calls = []
    manager = HotkeyManager(
        HotkeySettings(keyboard="", mouse_button="left", paste_mouse_button=button),
        lambda: calls.append("record"), on_paste=lambda: calls.append("paste"),
    )
    manager.start()
    listener = inputs[1][0]
    assert native_mouse(listener, 0x201, "left")
    assert native_mouse(listener, 0x202, "left")
    assert not native_mouse(listener, down, button)
    assert not native_mouse(listener, down, button)
    assert calls == ["record"]
    assert not native_mouse(listener, up, button)
    assert calls == ["record", "paste"]
    assert listener.suppressed == 3
    manager.stop()
    assert native_mouse(listener, down, button)  # Stale filters do not consume input.
    assert calls == ["record", "paste"]


@pytest.mark.parametrize("button, down, up", [
    ("left", 0x201, 0x202), ("right", 0x204, 0x205),
])
def test_native_primary_paste_buttons_preserve_click_and_focus(inputs, button, down, up):
    calls = []
    manager = HotkeyManager(
        HotkeySettings(keyboard="", paste_mouse_button=button), lambda: None,
        on_paste=lambda: calls.append("paste"),
    )
    manager.start()
    listener = inputs[1][0]
    assert native_mouse(listener, down, button)
    assert native_mouse(listener, up, button)
    assert calls == ["paste"] and listener.suppressed == 0
    manager.stop()


@pytest.mark.parametrize("shortcut", ["<ctrl>", "<shift>", "<alt>", "<cmd>", "<ctrl>+<alt>"])
def test_modifier_only_paste_is_rejected_with_actionable_error_before_hooks(inputs, shortcut):
    manager = HotkeyManager(
        HotkeySettings(paste_keyboard=shortcut), lambda: None, on_paste=lambda: None,
    )
    with pytest.raises(HotkeyError, match="Choose a regular key, function key or mouse button"):
        manager.start()
    assert not manager._active and inputs == ([], [])


@pytest.mark.parametrize("shortcut, keys", [
    ("m", [0x4D]), ("<f9>", [0x78]), ("<ctrl>+m", [0xA2, 0x4D]),
    ("<ctrl>+<shift>+m", [0xA2, 0xA0, 0x4D]),
    ("<alt>+<f9>", [0xA4, 0x78]),
])
def test_native_suppression_matches_locked_pynput_keycodes_without_real_hooks(
    monkeypatch, shortcut, keys,
):
    # The installed primary source supplies actual parse/canonical semantics.
    # The listeners and raw event delivery remain fake; no native hooks are installed.
    from pynput import keyboard
    from pynput.keyboard import _base

    class TypedListener(FakeListener):
        def _event_to_key(self, message, vk):
            if vk in keyboard.Listener._SPECIAL_KEYS:
                return keyboard.Listener._SPECIAL_KEYS[vk]
            return keyboard.KeyCode(vk=vk, char=chr(vk).lower())

        def canonical(self, key):
            return _base.Listener.canonical(self, key)

    listeners = []

    def create_listener(**kwargs):
        listener = TypedListener(**kwargs)
        listeners.append(listener)
        return listener

    fake_keyboard = SimpleNamespace(HotKey=keyboard.HotKey, Listener=create_listener)
    monkeypatch.setattr("voice_to_me.hotkeys.importlib.import_module", lambda _: fake_keyboard)
    calls = []
    manager = HotkeyManager(
        HotkeySettings(keyboard="", paste_keyboard=shortcut), lambda: None,
        on_paste=lambda: calls.append("paste"),
    )
    manager.start()
    listener = listeners[0]
    for vk in keys:
        assert native_keyboard(listener, 0x100, vk) is (vk != keys[-1])
    assert calls == []
    for vk in reversed(keys):
        assert native_keyboard(listener, 0x101, vk) is (vk != keys[-1])
    assert calls == ["paste"] and listener.suppressed == 2
    manager.stop()
