"""State and page dispatch tests without opening devices or a desktop."""

from enum import Enum
from pathlib import Path
from threading import Thread
from unittest.mock import Mock

import pytest

from voice_to_me.config import (
    AppSettings,
    AudioSettings,
    CodexSettings,
    HotkeySettings,
    WhisperSettings,
)
from voice_to_me.ui import NOTIFICATIONS, VoiceUI


@pytest.fixture
def ui(monkeypatch):
    monkeypatch.setattr("voice_to_me.ui.SoundCues", Mock)
    interface = VoiceUI(
        Mock(), config_path=Path("config.toml"), profile_path=Path("profile.md"), hotkey_label="F8"
    )
    for name in (
        "_status_symbol",
        "_status_title",
        "_status_detail",
        "_record_button",
        "_cancel_button",
        "_retry_button",
        "_page_host",
        "_main_page",
        "_main_canvas",
        "_inline_notice",
    ):
        setattr(interface, name, Mock())
    interface._root = Mock()
    return interface


@pytest.mark.parametrize(
    "state,cancel,retry,busy",
    [
        ("ready", False, False, False),
        ("recording", True, False, False),
        ("preparing", True, False, True),
        ("loading_model", True, False, True),
        ("transcribing", True, False, True),
        ("refining", True, False, True),
        ("copied", False, False, False),
        ("error", False, True, False),
    ],
)
def test_pipeline_states_have_actionable_controls(ui, state, cancel, retry, busy):
    ui._render_state(state, "")
    assert ui._cancel_button.configure.call_args.kwargs["state"] == (
        "normal" if cancel else "disabled"
    )
    assert ui._retry_button.configure.call_args.kwargs["state"] == (
        "normal" if retry else "disabled"
    )
    assert ui._record_button.configure.call_args.kwargs["state"] == (
        "disabled" if busy else "normal"
    )
    assert ui._status_title.configure.call_args.kwargs["text"]
    assert ui._status_symbol.configure.call_args.kwargs["text"]
    assert "Processing" not in ui._record_button.configure.call_args.kwargs["text"]
    if state == "copied":
        assert "Ctrl + V" in ui._status_detail.configure.call_args.kwargs["text"]


def test_background_observer_enqueues_safe_progress_until_main_thread_dispatch(ui):
    class State(Enum):
        PREPARING = "preparing"

    worker = Thread(target=ui.publish, args=(State.PREPARING, "Loading small · GPU · 2s elapsed"))
    worker.start()
    worker.join()
    ui._status_title.configure.assert_not_called()
    ui._drain_events()
    assert ui._state == "preparing"
    assert "2s elapsed" in ui._status_detail.configure.call_args.kwargs["text"]
    ui._root.after.assert_called_once()


def test_sound_events_wait_for_ui_dispatch_and_preserve_order_while_minimized(ui):
    ui._hide_window()
    worker = Thread(target=lambda: [ui.publish_sound(cue) for cue in (
        "recording_started", "recording_stopped", "ready",
    )])
    worker.start()
    worker.join()
    assert not ui._sounds.mock_calls
    ui._drain_events()
    assert [call[0] for call in ui._sounds.mock_calls] == [
        "recording_started", "recording_stopped", "ready",
    ]


def test_fast_pipeline_keeps_real_sound_events_before_one_ui_drain(ui, tmp_path):
    import numpy as np

    from voice_to_me.config import load_settings
    from voice_to_me.controller import AppController

    recorder = Mock()
    recorder.stop.return_value = np.ones(16000, dtype=np.float32)
    transcriber = Mock()
    transcriber.transcribe.return_value = "A synthetic message."
    refiner = Mock()
    refiner.refine.return_value = "A refined synthetic message."
    clipboard = Mock()
    controller = AppController(
        load_settings(tmp_path / "config.toml"), recorder, transcriber, refiner, clipboard,
    )
    controller._model_prepared = True
    controller.set_observer(ui.publish)
    controller.set_sound_observer(ui.publish_sound)
    try:
        controller.toggle()
        controller.toggle()
        assert controller.wait_for_idle()
        clipboard.write_text.assert_called_once_with("A refined synthetic message.")
        assert not ui._sounds.mock_calls
        ui._drain_events()
        assert [call[0] for call in ui._sounds.mock_calls] == [
            "recording_started", "recording_stopped", "ready",
        ]
        assert ui._state == "copied"
    finally:
        controller.shutdown()


def test_state_redraws_and_elapsed_updates_never_replay_sounds(ui):
    for state in ("preparing", "ready", "recording", "transcribing", "refining", "copied"):
        ui.publish(state, "Status")
        ui.publish(state, "Updated elapsed time")
    ui._drain_events()
    ui._close_settings()
    assert not ui._sounds.mock_calls


def test_quit_discards_pending_and_future_sound_events(ui):
    ui._events.put(("command", "quit", ""))
    ui.publish_sound("ready")
    ui._drain_events()
    ui.publish_sound("recording_started")
    ui._drain_events()
    ui._sounds.close.assert_called_once_with()
    assert [call[0] for call in ui._sounds.mock_calls] == ["close"]
    assert ui._events.qsize() == 1


def test_unknown_sound_event_is_ignored(ui):
    ui.publish_sound("unknown")
    ui._drain_events()
    assert not ui._sounds.mock_calls


def test_tray_commands_are_dispatched_on_ui_thread(ui):
    ui._events.put(("command", "cancel", ""))
    ui.controller.cancel.assert_not_called()
    ui._drain_events()
    ui.controller.cancel.assert_called_once_with()


def test_quit_drops_remaining_and_future_events(ui):
    ui._events.put(("command", "quit", ""))
    ui.publish("copied", "")
    ui._drain_events()
    ui.publish("refining", "Late callback")
    ui._render_state("copied", "Late callback")
    ui._drain_events()
    ui.controller.shutdown.assert_called_once_with()
    ui._root.destroy.assert_called_once_with()
    ui._status_title.configure.assert_not_called()
    ui._root.after.assert_not_called()
    assert ui._events.qsize() == 1  # It was never dispatched after Quit.


@pytest.mark.parametrize("state", list(NOTIFICATIONS))
def test_native_stage_notifications_do_not_include_private_backend_details(ui, state):
    ui._tray = Mock()
    ui._render_state(state, "PRIVATE transcript, style and error detail")
    assert ui._tray.notify.call_args.args[0] == NOTIFICATIONS[state]
    assert "PRIVATE" not in " ".join(ui._tray.notify.call_args.args)
    assert "PRIVATE" not in ui._tray.title
    ui._render_state(state, "New elapsed time")
    ui._tray.notify.assert_called_once()


def test_minimize_keeps_standard_taskbar_access(ui):
    ui._tray = Mock(visible=True)
    ui._hide_window()
    ui._root.iconify.assert_called_once_with()
    ui._root.withdraw.assert_not_called()


def test_settings_uses_same_page_host_without_replacing_root(ui, monkeypatch):
    from voice_to_me import ui as ui_module

    root = ui.root
    ui.settings_service = Mock()
    page = Mock()
    page.show.return_value = True
    factory = Mock(return_value=page)
    monkeypatch.setattr(ui_module, "SettingsDialog", factory)
    ui._open_settings()
    factory.assert_called_once_with(
        ui._page_host, ui.settings_service, ui._settings_applied, ui._close_settings
    )
    ui._main_page.pack_forget.assert_called_once_with()
    page.frame.pack.assert_called_once_with(fill="both", expand=True)
    assert ui.root is root
    ui._open_settings()
    assert factory.call_count == 1


def test_rejected_settings_keeps_main_page_and_reports_inline(ui, monkeypatch):
    from voice_to_me import ui as ui_module

    ui.settings_service = Mock()
    page = Mock(error_message="Finish or cancel recording first.")
    page.show.return_value = False
    monkeypatch.setattr(ui_module, "SettingsDialog", Mock(return_value=page))
    ui._open_settings()
    ui._main_page.pack_forget.assert_not_called()
    assert "recording" in ui._inline_notice.configure.call_args.kwargs["text"]


def test_settings_unavailable_is_inline(ui):
    ui._open_settings()
    assert "unavailable" in ui._inline_notice.configure.call_args.kwargs["text"]


def test_entire_settings_page_locks_record_actions_and_escape_targets_editor(ui):
    ui._settings_dialog = Mock(is_open=True, capturing=False)
    ui._toggle()
    ui._retry()
    ui._cancel()
    ui._save_settings()
    ui.controller.toggle.assert_not_called()
    ui.controller.retry.assert_not_called()
    ui.controller.cancel.assert_not_called()
    ui._settings_dialog._escape.assert_called_once_with()
    ui._settings_dialog._save.assert_called_once_with()
    ui._render_state("error", "")
    assert ui._record_button.configure.call_args.kwargs["state"] == "disabled"
    assert ui._retry_button.configure.call_args.kwargs["state"] == "disabled"


def test_settings_close_returns_to_same_main_page(ui):
    ui._settings_dialog = Mock(is_open=False)
    root = ui.root
    ui._close_settings()
    ui._main_page.pack.assert_called_once_with(fill="both", expand=True)
    assert ui.root is root


def test_scrolling_routes_to_visible_page(ui):
    ui._settings_dialog = Mock(is_open=True)
    ui._scroll_page(2)
    ui._settings_dialog.scroll.assert_called_once_with(2)
    ui._main_canvas.yview_scroll.assert_not_called()


def test_applied_single_shortcut_updates_hint(ui):
    ui._hotkey_hint = Mock()
    settings = AppSettings(
        AudioSettings(),
        WhisperSettings(),
        HotkeySettings("<f8>", ""),
        CodexSettings(),
        Path("config.toml"),
        Path("writing-profile.md"),
    )
    ui._settings_applied(settings)
    assert ui.hotkey_label == "F8"
    assert "F8" in ui._hotkey_hint.configure.call_args.kwargs["text"]


def test_applied_paste_shortcut_updates_hint_separately(ui):
    ui._hotkey_hint = Mock()
    settings = AppSettings(
        AudioSettings(), WhisperSettings(), HotkeySettings("", "x1", "", "x2"),
        CodexSettings(), Path("config.toml"), Path("writing-profile.md"),
    )
    ui._settings_applied(settings)
    hint = ui._hotkey_hint.configure.call_args.kwargs["text"]
    assert "Record: Mouse X1" in hint and "Paste: Mouse X2" in hint


def test_quit_removes_notification_and_icon_before_bounded_shutdown(ui):
    tray = ui._tray = Mock()
    ui._settings_dialog = Mock(is_open=True)
    calls = Mock()
    calls.attach_mock(ui._sounds, "sounds")
    calls.attach_mock(tray, "tray")
    calls.attach_mock(ui.controller, "controller")
    calls.attach_mock(ui._settings_dialog, "page")
    calls.attach_mock(ui._root, "root")
    ui._quit()
    assert [call[0] for call in calls.mock_calls] == [
        "sounds.close",
        "tray.remove_notification",
        "tray.stop",
        "controller.shutdown",
        "page.close",
        "root.destroy",
    ]
    assert tray.visible is False
    assert ui._tray is None
    ui._settings_dialog.close.assert_called_once_with(force=True)
    ui._quit()
    ui.controller.shutdown.assert_called_once_with()


def test_quit_uses_complete_runtime_cleanup_when_supplied(ui):
    cleanup = ui._shutdown = Mock()
    ui._quit()
    cleanup.assert_called_once_with()
    ui.controller.shutdown.assert_not_called()
    ui._root.destroy.assert_called_once_with()


@pytest.mark.parametrize("failure", ["sound_close", "remove_notification", "stop", "shutdown", "page_close"])
def test_cleanup_failure_never_strands_window_or_skips_remaining_cleanup(ui, failure):
    tray = ui._tray = Mock()
    ui._settings_dialog = Mock(is_open=True)
    failing = {
        "sound_close": ui._sounds.close,
        "remove_notification": tray.remove_notification,
        "stop": tray.stop,
        "shutdown": ui.controller.shutdown,
        "page_close": ui._settings_dialog.close,
    }[failure]
    failing.side_effect = RuntimeError("Cleanup failed")
    with pytest.raises(RuntimeError):
        ui._quit()
    tray.stop.assert_called_once_with()
    assert tray.visible is False
    ui.controller.shutdown.assert_called_once_with()
    ui._settings_dialog.close.assert_called_once_with(force=True)
    ui._root.destroy.assert_called_once_with()
    assert ui._quitting


def test_delayed_tray_setup_after_quit_stops_backend_again(ui, monkeypatch):
    import sys
    from types import SimpleNamespace

    tray = Mock()
    fake_menu = Mock()
    fake_menu.SEPARATOR = object()
    fake_pystray = SimpleNamespace(Icon=Mock(return_value=tray), Menu=fake_menu, MenuItem=Mock())
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
    ui._start_tray()
    ready = tray.run_detached.call_args.kwargs["setup"]
    ui._quit()
    assert tray.visible is False
    ready(tray)
    assert tray.stop.call_count == 2
    assert tray.visible is False
