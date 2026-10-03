"""Embedded Settings lifecycle tests; no physical hooks, devices or windows."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from voice_to_me.config import (
    MOUSE_BUTTONS,
    AppSettings,
    AudioSettings,
    CodexSettings,
    ConfigurationError,
    HotkeySettings,
    WhisperSettings,
)
from voice_to_me.settings import SettingsDraft
from voice_to_me.settings_dialog import (
    SettingsDialog,
    describe_paste_shortcut,
    describe_shortcuts,
    input_device_options,
)


class Variable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class ImmediateThread:
    def __init__(self, *, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


@pytest.fixture
def dialog(tmp_path):
    settings = AppSettings(
        AudioSettings(max_seconds=123, minimum_seconds=0.8),
        WhisperSettings(
            model="custom-local",
            device="cpu",
            compute_type="float32",
            model_directory="models/custom",
            beam_size=3,
        ),
        HotkeySettings("<f8>", "x1"),
        CodexSettings(executable="C:/Tools/codex.exe", timeout_seconds=75, reasoning_effort="high"),
        tmp_path / "config.toml",
        tmp_path / "writing-profile.md",
    )
    service = Mock()
    service.read_draft.return_value = SettingsDraft(settings, "Preserve my tone.")
    service.save.side_effect = lambda draft, **kwargs: draft.settings
    page = SettingsDialog(Mock(), service, Mock(), Mock())
    page._draft = service.read_draft()
    page._frame = Mock()
    page._closed = False
    page._codex_ready = True
    page._codex_requested = settings.codex.enabled
    service.suspend_shortcuts()
    page._suspended = True
    page._keyboard, page._mouse = settings.hotkeys.keyboard, settings.hotkeys.mouse_button
    page._status_label = Mock()
    page._microphone_combo = Mock()
    page._codex_model_entry = Mock()
    page._profile_text = Mock()
    page._profile_text.get.return_value = "Keep the original meaning."
    page._vars = {
        key: Variable(value)
        for key, value in {
            "shortcut": "F8 or Mouse X1",
            "paste_shortcut": "Not set",
            "microphone": "Default input",
            "language": "pt",
            "transcription_profile": "Current setup",
            "backend": "Checking hardware…",
            "codex_model": "",
            "codex_enabled": True,
            "codex_status": "Codex CLI: Ready",
            "codex_hint": "Uses your writing style.",
        }.items()
    }
    page._profile_lookup = {
        "Fast": "fast",
        "Balanced": "balanced",
        "Best quality": "quality",
        "Current setup": "custom",
    }
    page._device_lookup = {"Default input": None, "USB microphone": 2}
    page._buttons = {
        key: Mock() for key in ("capture", "clear", "capture_paste", "clear_paste",
                               "stop_capture", "save", "download", "codex_enabled")
    }
    return page


def begin_capture(page, target="recording"):
    capture = Mock()
    page._make_capture = Mock(return_value=capture)
    page._start_capture(target)
    token = page._capture_token
    assert page._capture is None
    page._launch_capture(token)
    return token, capture


def test_keyboard_capture_replaces_legacy_mouse_and_stays_paused_until_exit(dialog):
    token, capture = begin_capture(dialog)
    dialog._events.put(("capture", token, HotkeySettings("<ctrl>+<f9>", "")))
    assert dialog._keyboard == "<f8>"
    dialog._poll_events()
    assert dialog._keyboard == "<ctrl>+<f9>"
    assert dialog._mouse == ""
    assert dialog._vars["shortcut"].get() == "Ctrl + F9"
    capture.stop.assert_called_once_with()
    dialog.service.resume_shortcuts.assert_not_called()
    assert dialog._suspended
    dialog.close()
    dialog.service.resume_shortcuts.assert_called_once_with()


@pytest.mark.parametrize("button", MOUSE_BUTTONS)
def test_any_mouse_button_replaces_legacy_keyboard(dialog, button):
    token, _capture = begin_capture(dialog)
    dialog._events.put(("capture", token, HotkeySettings("", button)))
    dialog._poll_events()
    assert dialog._keyboard == ""
    assert dialog._mouse == button
    assert dialog._gather_draft().settings.hotkeys == HotkeySettings("", button)
    dialog.service.resume_shortcuts.assert_not_called()


@pytest.mark.parametrize("binding,label", [
    (HotkeySettings("<f9>", ""), "F9"),
    *[(HotkeySettings("", button), name) for button, name in (
        ("left", "Left mouse button"), ("right", "Right mouse button"),
        ("middle", "Middle mouse button"), ("x1", "Mouse X1"), ("x2", "Mouse X2"),
    )],
])
def test_paste_capture_preserves_recording_and_clears_other_input_type(dialog, binding, label):
    dialog._paste_keyboard, dialog._paste_mouse = "<f10>", "middle"
    token, capture = begin_capture(dialog, "paste")
    dialog._events.put(("capture", token, binding))
    dialog._poll_events()
    assert dialog._keyboard == "<f8>" and dialog._mouse == "x1"
    assert (dialog._paste_keyboard, dialog._paste_mouse) == (binding.keyboard, binding.mouse_button)
    assert dialog._vars["paste_shortcut"].get() == label
    assert describe_paste_shortcut(dialog._gather_draft().settings.hotkeys) == label
    capture.stop.assert_called_once_with()
    dialog.service.resume_shortcuts.assert_not_called()


def test_record_capture_preserves_existing_paste_shortcut(dialog):
    dialog._paste_mouse = "x2"
    token, _ = begin_capture(dialog)
    dialog._events.put(("capture", token, HotkeySettings("<f9>", "")))
    dialog._poll_events()
    assert dialog._gather_draft().settings.hotkeys == HotkeySettings("<f9>", "", "", "x2")


def test_clear_paste_disables_only_the_macro(dialog):
    dialog._paste_mouse = "x2"
    dialog._clear_shortcut("paste")
    assert dialog._gather_draft().settings.hotkeys == HotkeySettings("<f8>", "x1")
    assert dialog._vars["paste_shortcut"].get() == "Not set"


def test_clear_recording_keeps_paste_shortcut(dialog):
    dialog._paste_mouse = "x2"
    dialog._clear_shortcut()
    assert dialog._gather_draft().settings.hotkeys == HotkeySettings("", "", "", "x2")


def test_second_capture_cannot_switch_target_during_first_capture(dialog):
    token, _ = begin_capture(dialog, "paste")
    dialog._start_capture("recording")
    assert dialog._capture_target == "paste"
    dialog._events.put(("capture", token, HotkeySettings("", "x2")))
    dialog._poll_events()
    assert dialog._paste_mouse == "x2" and dialog._mouse == "x1"


def test_cancelled_paste_capture_ignores_late_result_and_preserves_saved_binding(dialog):
    dialog._paste_keyboard = "<f9>"
    token, _ = begin_capture(dialog, "paste")
    dialog.cancel_capture()
    dialog._events.put(("capture", token, HotkeySettings("", "x2")))
    dialog._poll_events()
    assert dialog._paste_keyboard == "<f9>" and dialog._paste_mouse == ""
    dialog.service.resume_shortcuts.assert_not_called()


def test_save_includes_paste_binding_without_changing_recording(dialog):
    dialog._paste_mouse = "x2"
    dialog._save()
    saved = dialog.service.save.call_args.args[0].settings
    assert saved.hotkeys == HotkeySettings("<f8>", "x1", "", "x2")
    dialog.on_applied.assert_called_once_with(saved)


def test_show_restores_saved_paste_binding(dialog, monkeypatch):
    from voice_to_me import settings_dialog

    original = dialog._draft
    dialog.service.read_draft.return_value = replace(original, settings=replace(
        original.settings, hotkeys=HotkeySettings("<f8>", "", "", "x2"),
    ))
    dialog._closed, dialog._suspended, dialog._frame = True, False, None
    monkeypatch.setattr(settings_dialog.ttk, "Frame", Mock(return_value=Mock()))
    dialog._build_window = Mock()
    dialog._start_backend_query = Mock()
    dialog._start_codex_query = Mock()
    assert dialog.show()
    assert dialog._paste_keyboard == "" and dialog._paste_mouse == "x2"


def test_capture_factory_resolves_correct_class_without_starting_hooks(dialog):
    from voice_to_me.hotkey_capture import ShortcutCapture

    capture = dialog._make_capture(42)
    assert isinstance(capture, ShortcutCapture)
    assert not capture._active
    capture.on_capture(HotkeySettings("<f9>", ""))
    assert dialog._events.get_nowait() == ("capture", 42, HotkeySettings("<f9>", ""))
    capture.on_error("Capture cancelled.")
    assert dialog._events.get_nowait() == ("capture_error", 42, "Capture cancelled.")
    capture.stop()


def test_capture_filters_cancel_back_stop_clicks_with_immutable_bounds(dialog):
    for name, x in (("stop_capture", 10), ("cancel", 100), ("back", 200)):
        button = Mock()
        button.winfo_rootx.return_value = x
        button.winfo_rooty.return_value = 20
        button.winfo_width.return_value = 50
        button.winfo_height.return_value = 30
        dialog._buttons[name] = button
    capture = dialog._make_capture(42)
    assert not capture.mouse_filter(30, 30)
    assert not capture.mouse_filter(110, 30)
    assert not capture.mouse_filter(220, 30)
    assert capture.mouse_filter(80, 30)
    assert capture.mouse_filter(30, 60)
    for name in ("stop_capture", "cancel", "back"):
        dialog._buttons[name].winfo_rootx.assert_called_once_with()
    capture.stop()


@pytest.mark.parametrize("message", ["Capture timed out.", "Capture cancelled."])
def test_capture_errors_keep_editor_open_and_global_shortcuts_paused(dialog, message):
    token, capture = begin_capture(dialog)
    dialog._events.put(("capture_error", token, message))
    dialog._poll_events()
    assert not dialog.capturing
    assert dialog.is_open
    assert dialog._keyboard == "<f8>"
    capture.stop.assert_called_once_with()
    dialog.service.resume_shortcuts.assert_not_called()
    assert dialog._status_label.configure.call_args.kwargs["text"] == message


def test_start_failure_keeps_editing_safe(dialog):
    token, capture = begin_capture(dialog)
    capture.start.side_effect = RuntimeError("Listener unavailable.")
    dialog._launch_capture(token)
    assert not dialog.capturing
    dialog.service.resume_shortcuts.assert_not_called()
    assert "unavailable" in dialog._status_label.configure.call_args.kwargs["text"]


def test_cancel_before_delayed_launch_never_starts_hooks(dialog):
    dialog._make_capture = Mock()
    frame = dialog.frame
    dialog._start_capture()
    token = dialog._capture_token
    assert dialog.close()
    dialog._launch_capture(token)
    dialog._make_capture.assert_not_called()
    frame.after_cancel.assert_called_once()
    frame.destroy.assert_called_once_with()
    dialog.on_close.assert_called_once_with()
    dialog.service.resume_shortcuts.assert_called_once_with()


def test_late_cancelled_capture_does_not_mutate_draft(dialog):
    token, _capture = begin_capture(dialog)
    dialog.cancel_capture()
    dialog._events.put(("capture", token, HotkeySettings("<f10>", "")))
    dialog._poll_events()
    assert dialog._keyboard == "<f8>"
    assert dialog._mouse == "x1"
    dialog.service.resume_shortcuts.assert_not_called()


def test_show_suspends_before_creating_embedded_frame_without_toplevel(dialog, monkeypatch):
    from voice_to_me import settings_dialog

    dialog._closed, dialog._suspended, dialog._frame = True, False, None
    frame = Mock()
    factory = Mock(return_value=frame)
    monkeypatch.setattr(settings_dialog.ttk, "Frame", factory)
    monkeypatch.setattr(
        settings_dialog.tk, "Toplevel", Mock(side_effect=AssertionError("No new window"))
    )
    dialog._build_window = Mock()
    dialog._start_backend_query = Mock()
    dialog._start_codex_query = Mock()
    calls = Mock()
    calls.attach_mock(dialog.service.suspend_shortcuts, "suspend")
    calls.attach_mock(factory, "create_frame")
    calls.attach_mock(dialog._build_window, "build_editor")
    assert dialog.show()
    assert [call[0] for call in calls.mock_calls] == ["suspend", "create_frame", "build_editor"]
    factory.assert_called_once_with(dialog.parent, padding=(22, 14))
    assert dialog.window is frame
    assert dialog.frame is frame
    assert dialog._suspended
    dialog.service.resume_shortcuts.assert_not_called()


def test_busy_pipeline_rejects_settings_inline_before_any_widgets(dialog, monkeypatch):
    from voice_to_me import settings_dialog

    dialog._closed, dialog._suspended, dialog._frame = True, False, None
    factory = Mock()
    monkeypatch.setattr(settings_dialog.ttk, "Frame", factory)
    dialog.service.suspend_shortcuts.side_effect = ConfigurationError(
        "Finish or cancel recording first."
    )
    assert dialog.show() is False
    factory.assert_not_called()
    assert "recording" in dialog.error_message
    assert not dialog.is_open


def test_frame_creation_failure_resumes_shortcuts(dialog, monkeypatch):
    from voice_to_me import settings_dialog

    dialog._closed, dialog._suspended, dialog._frame = True, False, None
    monkeypatch.setattr(
        settings_dialog.ttk, "Frame", Mock(side_effect=RuntimeError("Could not build page"))
    )
    assert not dialog.show()
    dialog.service.resume_shortcuts.assert_called_once_with()
    assert dialog._closed


def test_capture_stop_failure_requires_retry_before_leaving(dialog):
    _token, capture = begin_capture(dialog)
    capture.stop.side_effect = RuntimeError("Stop failed")
    frame = dialog.frame
    assert not dialog.close()
    frame.destroy.assert_not_called()
    dialog.service.resume_shortcuts.assert_not_called()
    capture.stop.side_effect = None
    assert dialog.close()
    dialog.service.resume_shortcuts.assert_called_once_with()


def test_clear_supports_record_button_only(dialog):
    dialog._clear_shortcut()
    draft = dialog._gather_draft()
    assert draft.settings.hotkeys == HotkeySettings("", "")
    assert describe_shortcuts(draft.settings.hotkeys) == "Record button only"


def test_save_preserves_hidden_backend_settings_and_paths(dialog):
    original = dialog._draft.settings
    dialog._vars["microphone"].set("USB microphone")
    dialog._vars["language"].set("en")
    dialog._vars["codex_model"].set("optional-model")
    draft = dialog._gather_draft()
    assert draft.settings.audio == replace(original.audio, device=2)
    assert draft.settings.whisper == replace(original.whisper, language="en")
    assert draft.settings.codex == replace(original.codex, model="optional-model")
    assert draft.settings.config_path == original.config_path
    assert draft.settings.profile_path == original.profile_path
    assert draft.profile_text == "Keep the original meaning."
    dialog._save()
    assert dialog.service.save.call_args.kwargs == {"resume_after_save": True}
    dialog.service.resume_shortcuts.assert_not_called()
    dialog.on_applied.assert_called_once()
    dialog.on_close.assert_called_once_with()
    assert dialog._closed


@pytest.mark.parametrize(
    "label,model", [("Fast", "small"), ("Balanced", "turbo"), ("Best quality", "large-v3")]
)
def test_simple_profile_applies_local_automatic_transcription(dialog, label, model):
    dialog._vars["transcription_profile"].set(label)
    whisper = dialog._gather_draft().settings.whisper
    assert whisper.model == model
    assert whisper.device == "auto"
    assert whisper.compute_type == "auto"
    assert whisper.beam_size == 1
    assert whisper.local_files_only
    assert whisper.model_directory == ""


def test_invalid_profile_keeps_editor_and_edits(dialog):
    dialog._vars["transcription_profile"].set("Invalid")
    dialog._save()
    dialog.service.save.assert_not_called()
    assert dialog.is_open
    assert "profile" in dialog._status_label.configure.call_args.kwargs["text"]


def test_save_failure_repauses_editor_after_backend_rollback(dialog):
    dialog.service.save.side_effect = ConfigurationError("Could not save settings.")
    dialog._save()
    dialog.on_applied.assert_not_called()
    assert dialog.is_open
    assert dialog._suspended
    assert dialog.service.suspend_shortcuts.call_count == 2
    assert dialog._profile_text.get.return_value == "Keep the original meaning."


def test_resume_failure_stays_on_page_until_retry(dialog):
    frame = dialog.frame
    dialog.service.resume_shortcuts.side_effect = [RuntimeError("Listener failed"), None]
    assert not dialog.close()
    assert dialog._suspended
    frame.destroy.assert_not_called()
    dialog.on_close.assert_not_called()
    assert "Could not resume" in dialog._status_label.configure.call_args.kwargs["text"]
    assert dialog.close()
    frame.destroy.assert_called_once_with()
    dialog.on_close.assert_called_once_with()


def test_forced_app_quit_stops_capture_without_resuming_closed_runtime(dialog):
    _token, capture = begin_capture(dialog)
    assert dialog.close(force=True)
    capture.stop.assert_called_once_with()
    dialog.service.resume_shortcuts.assert_not_called()
    dialog.on_close.assert_not_called()


def test_capture_blocks_save(dialog):
    begin_capture(dialog)
    dialog._save()
    dialog.service.save.assert_not_called()
    assert "Finish capture" in dialog._status_label.configure.call_args.kwargs["text"]


def test_microphone_enumeration_never_opens_stream_and_preserves_indexes():
    query = Mock(
        return_value=[
            {"name": "Speakers", "max_input_channels": 0},
            {"name": "Mic", "max_input_channels": 1},
        ]
    )
    assert input_device_options(query) == [("Default input", None), ("Mic (device 1)", 1)]
    query.assert_called_once_with()


def test_configured_unavailable_microphone_is_preserved(dialog, monkeypatch):
    from voice_to_me import settings_dialog

    monkeypatch.setattr(settings_dialog, "input_device_options", lambda: [("Default input", None)])
    dialog._refresh_microphones("USB named device")
    label = dialog._vars["microphone"].get()
    assert dialog._device_lookup[label] == "USB named device"


@pytest.mark.parametrize("failure", [False, True])
def test_model_download_updates_widgets_only_via_queue(dialog, monkeypatch, failure):
    from voice_to_me import settings_dialog

    monkeypatch.setattr(settings_dialog, "Thread", ImmediateThread)
    dialog._vars["transcription_profile"].set("Fast")
    dialog.service.download_model.return_value = "models/small"
    if failure:
        dialog.service.download_model.side_effect = RuntimeError("Connection unavailable")
    dialog._download_model()
    assert dialog._downloading
    assert dialog._downloaded_models == {}
    dialog._poll_events()
    assert not dialog._downloading
    dialog.service.download_model.assert_called_once_with("small")
    if failure:
        assert dialog._downloaded_models == {}
        assert "unavailable" in dialog._status_label.configure.call_args.kwargs["text"]
    else:
        assert dialog._gather_draft().settings.whisper.model_directory == "models/small"
        dialog._vars["transcription_profile"].set("Balanced")
        assert dialog._gather_draft().settings.whisper.model_directory == ""


@pytest.mark.parametrize(
    "device,cuda,runtime,text",
    [
        ("cuda", True, True, "GPU acceleration: Ready"),
        ("cpu", True, False, "GPU acceleration needs local runtime support."),
        ("cpu", False, True, "CPU transcription: Ready"),
    ],
)
def test_hardware_readiness_is_queued_and_hides_compute_details(
    dialog, monkeypatch, device, cuda, runtime, text
):
    from voice_to_me import performance, settings_dialog

    monkeypatch.setattr(settings_dialog, "Thread", ImmediateThread)
    monkeypatch.setattr(
        performance,
        "detect_backend",
        lambda: SimpleNamespace(device=device, cuda_available=cuda, runtime_ready=runtime),
    )
    dialog._start_backend_query()
    assert dialog._vars["backend"].get() == "Checking hardware…"
    dialog._poll_events()
    assert dialog._vars["backend"].get() == text


def test_late_background_generation_does_not_touch_reopened_editor(dialog):
    dialog._events.put(("backend", dialog._generation - 1, "Old status"))
    dialog._events.put(("download", dialog._generation - 1, ("small", "old/path")))
    dialog._poll_events()
    assert dialog._vars["backend"].get() == "Checking hardware…"
    assert dialog._downloaded_models == {}


def test_disabling_codex_preserves_model_writing_style_and_hidden_settings(dialog):
    original = dialog._draft.settings.codex
    dialog._vars["codex_model"].set("my-preferred-model")
    dialog._vars["codex_enabled"].set(False)
    dialog._codex_toggle()
    dialog._codex_model_entry.configure.assert_called_with(state="disabled")
    dialog._buttons["codex_enabled"].configure.assert_called_with(state="normal")
    assert "Local transcription only" in dialog._vars["codex_hint"].get()
    draft = dialog._gather_draft()
    assert draft.settings.codex == replace(original, model="my-preferred-model", enabled=False)
    assert draft.profile_text == "Keep the original meaning."
    dialog._profile_text.configure.assert_not_called()
    dialog._save()
    assert dialog.service.save.call_args.args[0] == draft


def test_enabling_ready_codex_enables_model_and_preserves_style(dialog):
    dialog._vars["codex_enabled"].set(False)
    dialog._codex_toggle()
    dialog._vars["codex_enabled"].set(True)
    dialog._codex_toggle()
    dialog._codex_model_entry.configure.assert_called_with(state="normal")
    assert "writing style" in dialog._vars["codex_hint"].get()
    assert dialog._gather_draft().settings.codex.enabled


def test_not_configured_codex_turns_enabled_saved_draft_off_without_autosave(dialog):
    assert dialog._draft.settings.codex.enabled
    dialog._events.put(("codex_status", dialog._generation,
                        (False, "Install Codex CLI and run codex login, then reopen Settings.")))
    dialog._poll_events()
    assert dialog._draft.settings.codex.enabled  # Saved settings remain unchanged.
    assert not dialog._vars["codex_enabled"].get()
    assert dialog._vars["codex_status"].get() == "Codex CLI: Not configured"
    assert "codex login" in dialog._vars["codex_hint"].get()
    dialog._buttons["codex_enabled"].configure.assert_called_with(state="disabled")
    dialog._codex_model_entry.configure.assert_called_with(state="disabled")
    dialog.service.save.assert_not_called()
    dialog._save()
    saved = dialog.service.save.call_args.args[0]
    assert not saved.settings.codex.enabled
    assert saved.profile_text == "Keep the original meaning."


def test_ready_codex_restores_saved_disabled_choice_without_enabling_it(dialog):
    dialog._codex_requested = False
    dialog._events.put(("codex_status", dialog._generation, (True, "Ready.")))
    dialog._poll_events()
    assert dialog._codex_ready
    assert not dialog._vars["codex_enabled"].get()
    assert dialog._vars["codex_status"].get() == "Codex CLI: Ready"
    dialog._buttons["codex_enabled"].configure.assert_called_with(state="normal")
    dialog._codex_model_entry.configure.assert_called_with(state="disabled")
    assert not dialog._gather_draft().settings.codex.enabled


def test_pending_codex_check_keeps_refinement_unavailable_and_can_save_local_only(dialog):
    dialog._codex_ready = None
    dialog._vars["codex_enabled"].set(True)
    dialog._update_codex_controls()
    assert not dialog._vars["codex_enabled"].get()
    dialog._buttons["codex_enabled"].configure.assert_called_with(state="disabled")
    dialog._codex_model_entry.configure.assert_called_with(state="disabled")
    dialog._save()
    assert not dialog.service.save.call_args.args[0].settings.codex.enabled


def test_cancel_after_codex_readiness_failure_does_not_save_draft_off(dialog):
    dialog._events.put(("codex_status", dialog._generation, (False, "Run codex login.")))
    dialog._poll_events()
    assert dialog.close()
    dialog.service.save.assert_not_called()
    dialog.on_applied.assert_not_called()
    assert dialog._draft.settings.codex.enabled


def test_capture_temporarily_disables_ready_codex_controls_then_restores_them(dialog):
    begin_capture(dialog)
    dialog._buttons["codex_enabled"].configure.assert_called_with(state="disabled")
    dialog._codex_model_entry.configure.assert_called_with(state="disabled")
    dialog.cancel_capture()
    dialog._buttons["codex_enabled"].configure.assert_called_with(state="normal")
    dialog._codex_model_entry.configure.assert_called_with(state="normal")
    assert dialog._vars["codex_enabled"].get()


def test_late_codex_status_does_not_change_reopened_editor_or_save_settings(dialog):
    dialog._codex_ready = None
    dialog._vars["codex_enabled"].set(False)
    dialog._vars["codex_status"].set("Checking Codex CLI…")
    dialog._events.put(("codex_status", dialog._generation - 1, (True, "Old status")))
    dialog._poll_events()
    assert dialog._codex_ready is None
    assert not dialog._vars["codex_enabled"].get()
    assert dialog._vars["codex_status"].get() == "Checking Codex CLI…"
    dialog.service.save.assert_not_called()


@pytest.mark.parametrize("ready, message", [
    (True, "Ready."), (False, "Install Codex CLI and run codex login."),
])
def test_codex_readiness_check_is_async_queued_and_never_updates_widgets_in_worker(
    dialog, monkeypatch, ready, message,
):
    from voice_to_me import codex_status, settings_dialog

    check = Mock(return_value=SimpleNamespace(ready=ready, message=message))
    monkeypatch.setattr(codex_status, "check_codex_readiness", check)
    monkeypatch.setattr(settings_dialog, "Thread", ImmediateThread)
    dialog._codex_ready = None
    dialog._vars["codex_enabled"].set(False)
    dialog._vars["codex_status"].set("Checking Codex CLI…")
    dialog._start_codex_query()
    check.assert_called_once_with(dialog._draft.settings.codex)
    assert dialog._vars["codex_status"].get() == "Checking Codex CLI…"
    assert dialog._codex_ready is None
    dialog._poll_events()
    assert dialog._codex_ready is ready
    assert dialog._vars["codex_enabled"].get() is ready
    assert dialog._vars["codex_status"].get() == (
        "Codex CLI: Ready" if ready else "Codex CLI: Not configured"
    )
    if not ready:
        assert dialog._vars["codex_hint"].get() == message
    dialog.service.save.assert_not_called()


def test_codex_readiness_failure_shows_safe_install_login_hint_without_raw_error(
    dialog, monkeypatch,
):
    from voice_to_me import codex_status, settings_dialog

    monkeypatch.setattr(settings_dialog, "Thread", ImmediateThread)
    monkeypatch.setattr(codex_status, "check_codex_readiness",
                        Mock(side_effect=RuntimeError("private login response")))
    dialog._start_codex_query()
    dialog._poll_events()
    assert not dialog._codex_ready
    assert not dialog._gather_draft().settings.codex.enabled
    assert "Install Codex CLI" in dialog._vars["codex_hint"].get()
    assert "codex login" in dialog._vars["codex_hint"].get()
    assert "private login response" not in dialog._vars["codex_hint"].get()
