from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import voice_to_me.hotkey_capture as capture_module
from voice_to_me.config import HotkeySettings
from voice_to_me.hotkey_capture import ShortcutCapture


@dataclass(frozen=True)
class FakeKey:
    name: str | None = None
    char: str | None = None
    vk: int | None = None


class FakeListener:
    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.started = False
        self.stopped = False
        self.joined = False
        self.start_error = False
        self.canonical_error = False

    def canonical(self, key):
        if self.canonical_error:
            raise OSError("input backend failed")
        return key

    def start(self):
        self.started = True
        if self.start_error:
            raise OSError("listener startup failed")

    def wait(self):
        pass

    def stop(self):
        self.stopped = True

    def join(self, timeout):
        self.joined = True


class FakeTimer:
    def __init__(self, duration, callback):
        self.duration = duration
        self.callback = callback
        self.started = False
        self.cancelled = False
        self.daemon = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.callback()


@pytest.fixture
def capture_backend(monkeypatch):
    keyboards, mice, timers, parsed = [], [], [], []

    def keyboard_listener(**kwargs):
        listener = FakeListener(**kwargs)
        keyboards.append(listener)
        return listener

    def mouse_listener(**kwargs):
        listener = FakeListener(**kwargs)
        mice.append(listener)
        return listener

    def create_timer(duration, callback):
        timer = FakeTimer(duration, callback)
        timers.append(timer)
        return timer

    def parse(value):
        parsed.append(value)
        if "unsupported" in value:
            raise ValueError("not supported")
        return [value]

    modules = {
        "pynput.keyboard": SimpleNamespace(
            Listener=keyboard_listener, HotKey=SimpleNamespace(parse=parse)
        ),
        "pynput.mouse": SimpleNamespace(
            Listener=mouse_listener,
            Button=SimpleNamespace(left="left", right="right", middle="middle", x1="x1", x2="x2"),
        ),
    }
    monkeypatch.setattr(capture_module.importlib, "import_module", modules.__getitem__)
    monkeypatch.setattr(capture_module.threading, "Timer", create_timer)
    return SimpleNamespace(keyboards=keyboards, mice=mice, timers=timers, parsed=parsed)


def begin_capture(backend, timeout=10, *, mouse_filter=None):
    results, errors = [], []
    capture = ShortcutCapture(results.append, errors.append, timeout_seconds=timeout,
                              mouse_filter=mouse_filter)
    capture.start()
    return capture, results, errors, backend.keyboards[-1], backend.mice[-1]


def test_captures_full_chord_on_first_release(capture_backend):
    capture, results, errors, keyboard, mouse = begin_capture(capture_backend)
    ctrl = FakeKey(name="ctrl_l", vk=162)
    alt = FakeKey(name="alt_r", vk=165)
    char = FakeKey(char="M", vk=77)
    for key in (char, alt, ctrl):
        keyboard.callbacks["on_press"](key)
    assert results == []
    keyboard.callbacks["on_release"](char)
    assert results == [HotkeySettings(keyboard="<ctrl>+<alt>+m", mouse_button="")]
    assert errors == []
    assert keyboard.stopped and mouse.stopped
    assert capture_backend.timers[0].cancelled
    assert not capture._active


@pytest.mark.parametrize(
    ("key", "shortcut"),
    [
        (FakeKey(char="a", vk=65), "a"),
        (FakeKey(name="f8", vk=119), "<f8>"),
        (FakeKey(name="space", vk=32), "<space>"),
        (FakeKey(char=" ", vk=32), "<space>"),
        (FakeKey(name="enter", vk=13), "<enter>"),
        (FakeKey(vk=107), "<107>"),
        (FakeKey(char="é", vk=222), "é"),
    ],
)
def test_captures_single_key(capture_backend, key, shortcut):
    _capture, results, errors, keyboard, _mouse = begin_capture(capture_backend)
    keyboard.callbacks["on_press"](key)
    keyboard.callbacks["on_release"](key)
    assert results == [HotkeySettings(keyboard=shortcut, mouse_button="")]
    assert errors == []


def test_normalizes_modifiers_and_deduplicates_both_ctrl_keys(capture_backend):
    _capture, results, errors, keyboard, _mouse = begin_capture(capture_backend)
    keys = [
        FakeKey(name="ctrl_l", vk=162), FakeKey(name="ctrl_r", vk=163),
        FakeKey(name="shift_r", vk=161), FakeKey(name="cmd_r", vk=92),
        FakeKey(char="k", vk=75),
    ]
    for key in keys:
        keyboard.callbacks["on_press"](key)
    keyboard.callbacks["on_release"](keys[0])
    assert results[0].keyboard == "<ctrl>+<shift>+<cmd>+k"
    assert errors == []


def test_autorepeat_does_not_add_duplicate_keys(capture_backend):
    _capture, results, errors, keyboard, _mouse = begin_capture(capture_backend)
    key = FakeKey(char="a", vk=65)
    for _ in range(10):
        keyboard.callbacks["on_press"](key)
    keyboard.callbacks["on_release"](key)
    keyboard.callbacks["on_release"](key)
    capture_backend.timers[0].fire()
    assert results == [HotkeySettings(keyboard="a", mouse_button="")]
    assert errors == []


@pytest.mark.parametrize("button", ["left", "right", "middle", "x1", "x2"])
def test_mouse_capture_accepts_supported_button_only(capture_backend, button):
    _capture, results, errors, keyboard, mouse = begin_capture(capture_backend)
    click = mouse.callbacks["on_click"]
    click(0, 0, "wheel_up", True)
    click(0, 0, "unknown", True)
    click(0, 0, button, False)
    assert results == []
    click(0, 0, button, True)
    click(0, 0, button, True)
    assert results == [HotkeySettings(keyboard="", mouse_button=button)]
    assert errors == []
    assert keyboard.stopped and mouse.stopped


def test_capture_mouse_filter_ignores_cancel_click_and_accepts_next_click(capture_backend):
    filtered = []

    def filter_cancel_button(x, y):
        filtered.append((x, y))
        return (x, y) != (10, 20)

    capture, results, errors, _keyboard, mouse = begin_capture(
        capture_backend, mouse_filter=filter_cancel_button
    )
    click = mouse.callbacks["on_click"]
    click(10, 20, "left", True)
    click(30, 40, "left", True)
    assert results == errors == []
    assert capture._active
    assert filtered == [(10, 20)]
    click(10, 20, "left", False)
    click(30, 40, "left", True)
    assert results == [HotkeySettings(keyboard="", mouse_button="left")]
    assert errors == []
    assert filtered == [(10, 20), (30, 40)]


def test_capture_mouse_filter_failure_is_safely_ignored_without_coordinates(
    capture_backend, caplog
):
    def fail(x, y):
        raise OSError(f"private coordinate {x}, {y}")

    capture, results, errors, _keyboard, mouse = begin_capture(
        capture_backend, mouse_filter=fail
    )
    mouse.callbacks["on_click"](123456, 789012, "right", True)
    assert results == errors == []
    assert capture._active
    assert "Shortcut capture mouse filter failed" in caplog.text
    assert "123456" not in caplog.text and "789012" not in caplog.text
    assert "private coordinate" not in caplog.text
    capture.stop()


def test_stopping_capture_after_filtered_click_cannot_produce_result(capture_backend):
    capture, results, errors, _keyboard, mouse = begin_capture(
        capture_backend, mouse_filter=lambda _x, _y: False
    )
    click = mouse.callbacks["on_click"]
    click(10, 20, "left", True)
    capture.stop()
    click(10, 20, "left", False)
    capture_backend.timers[0].fire()
    assert results == errors == []


def test_escape_cancels_once_and_does_not_capture(capture_backend):
    _capture, results, errors, keyboard, _mouse = begin_capture(capture_backend)
    escape = FakeKey(name="esc", vk=27)
    keyboard.callbacks["on_press"](escape)
    keyboard.callbacks["on_release"](escape)
    capture_backend.timers[0].fire()
    assert results == []
    assert errors == ["Shortcut capture cancelled."]


@pytest.mark.parametrize("name", ["ctrl_l", "alt_r", "shift_r", "cmd_r"])
def test_modifier_only_shortcut_is_rejected(capture_backend, name):
    _capture, results, errors, keyboard, _mouse = begin_capture(capture_backend)
    key = FakeKey(name=name, vk=17)
    keyboard.callbacks["on_press"](key)
    keyboard.callbacks["on_release"](key)
    assert results == []
    assert errors == ["Choose a regular key or combine it with Ctrl, Alt, Shift, or Win."]


def test_timeout_stops_listeners_and_reports_once(capture_backend):
    capture, results, errors, keyboard, mouse = begin_capture(capture_backend, timeout=3)
    timer = capture_backend.timers[0]
    assert timer.started and timer.daemon and timer.duration == 3
    timer.fire()
    timer.fire()
    assert results == []
    assert errors == ["Shortcut capture timed out. Click Capture and try again."]
    assert keyboard.stopped and mouse.stopped and timer.cancelled
    assert not capture._active


def test_explicit_stop_is_silent_and_stale_events_are_ignored(capture_backend):
    capture, results, errors, keyboard, mouse = begin_capture(capture_backend)
    old_timer = capture_backend.timers[0]
    capture.stop()
    capture.stop()
    key = FakeKey(char="a", vk=65)
    keyboard.callbacks["on_press"](key)
    keyboard.callbacks["on_release"](key)
    mouse.callbacks["on_click"](0, 0, "x1", True)
    old_timer.fire()
    assert results == errors == []
    assert keyboard.stopped and keyboard.joined and mouse.stopped and mouse.joined


def test_capture_can_restart_and_old_events_cannot_affect_new_session(capture_backend):
    capture, results, errors, keyboard, _mouse = begin_capture(capture_backend)
    old_timer = capture_backend.timers[0]
    capture.stop()
    capture.start()
    current = capture_backend.keyboards[-1]
    key = FakeKey(char="a", vk=65)
    keyboard.callbacks["on_press"](key)
    keyboard.callbacks["on_release"](key)
    old_timer.fire()
    assert results == errors == []
    current.callbacks["on_press"](key)
    current.callbacks["on_release"](key)
    assert results == [HotkeySettings(keyboard="a", mouse_button="")]


def test_start_is_idempotent_and_does_not_suppress_input(capture_backend):
    capture, _results, _errors, keyboard, mouse = begin_capture(capture_backend)
    capture.start()
    assert len(capture_backend.keyboards) == len(capture_backend.mice) == 1
    assert keyboard.callbacks["suppress"] is False
    assert mouse.callbacks["suppress"] is False
    capture.stop()


def test_partial_start_failure_releases_both_listeners(monkeypatch):
    keyboard, mouse = FakeListener(), FakeListener()
    mouse.start_error = True
    modules = {
        "pynput.keyboard": SimpleNamespace(Listener=lambda **_kwargs: keyboard),
        "pynput.mouse": SimpleNamespace(
            Listener=lambda **_kwargs: mouse,
            Button=SimpleNamespace(left="left", right="right", middle="middle", x1="x1", x2="x2"),
        ),
    }
    monkeypatch.setattr("voice_to_me.hotkey_capture.importlib.import_module", modules.__getitem__)
    results, errors = [], []
    capture = ShortcutCapture(results.append, errors.append)
    capture.start()
    assert results == []
    assert errors == ["Shortcut capture could not start. Check your input devices and try again."]
    assert keyboard.stopped and keyboard.joined and mouse.stopped and mouse.joined
    assert not capture._active
    assert capture._timer is None


def test_import_failure_is_safe_and_actionable(monkeypatch):
    def fail(_name):
        raise ImportError("pynput not installed")

    monkeypatch.setattr("voice_to_me.hotkey_capture.importlib.import_module", fail)
    results, errors = [], []
    capture = ShortcutCapture(results.append, errors.append)
    capture.start()
    assert results == []
    assert len(errors) == 1
    assert not capture._active


def test_input_backend_failure_reports_error_and_cleans_up(capture_backend):
    _capture, results, errors, keyboard, mouse = begin_capture(capture_backend)
    keyboard.canonical_error = True
    keyboard.callbacks["on_press"](FakeKey(char="a", vk=65))
    assert results == []
    assert errors == ["This key cannot be used as a shortcut. Try another key."]
    assert keyboard.stopped and mouse.stopped


def test_unsupported_key_does_not_leave_capture_running(capture_backend):
    _capture, results, errors, keyboard, mouse = begin_capture(capture_backend)
    keyboard.callbacks["on_press"](FakeKey())
    assert results == []
    assert errors == ["This key cannot be used as a shortcut. Try another key."]
    assert keyboard.stopped and mouse.stopped


def test_unsupported_shortcut_does_not_return_result(capture_backend):
    _capture, results, errors, keyboard, _mouse = begin_capture(capture_backend)
    key = FakeKey(name="unsupported", vk=12)
    keyboard.callbacks["on_press"](key)
    keyboard.callbacks["on_release"](key)
    assert results == []
    assert errors == ["This shortcut is not supported. Try another key."]


def test_native_join_failure_does_not_skip_remaining_capture_cleanup(capture_backend):
    _capture, results, errors, keyboard, mouse = begin_capture(capture_backend)

    def fail_join(timeout):
        raise OSError("native input listener failed")

    keyboard.join = fail_join
    key = FakeKey(char="a", vk=65)
    keyboard.callbacks["on_press"](key)
    keyboard.callbacks["on_release"](key)
    assert results == [HotkeySettings(keyboard="a", mouse_button="")]
    assert errors == []
    assert keyboard.stopped and mouse.stopped and mouse.joined
    assert capture_backend.timers[0].cancelled


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_timeout_must_be_positive_and_finite(timeout):
    with pytest.raises(ValueError, match="positive"):
        ShortcutCapture(lambda _settings: None, lambda _error: None, timeout)


def test_captured_pynput_tokens_round_trip_without_real_listeners():
    from pynput.keyboard import HotKey, Key, KeyCode

    listener = SimpleNamespace(canonical=lambda key: key)
    examples = [
        [Key.ctrl_l, Key.alt_r, KeyCode.from_char("M")],
        [Key.f8], [Key.space], [KeyCode.from_char("+")],
        [Key.ctrl_l, KeyCode.from_char("+")],
    ]
    for keys in examples:
        tokens = {ShortcutCapture._token(key, listener) for key in keys}
        shortcut = "+".join(sorted(tokens, key=ShortcutCapture._token_order))
        assert len(HotKey.parse(shortcut)) == len(tokens)
