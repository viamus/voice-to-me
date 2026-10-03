import os
import threading
from dataclasses import replace
from unittest.mock import Mock

import pytest

from voice_to_me.config import HotkeySettings, load_settings
from voice_to_me.settings import SettingsDraft


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from voice_to_me import runtime as module
    from voice_to_me.mouse_guard import MouseShortcutGuard, WindowsMouseAPI

    for name in ("WindowsClipboard", "AudioRecorder", "WhisperTranscriber", "CodexRefiner", "PasteMacro"):
        monkeypatch.setattr(module, name, lambda *_, **__: Mock())

    api = Mock(spec=WindowsMouseAPI)
    api.window_from_point.return_value = 100
    api.process_id.return_value = os.getpid() + 1
    api.root_window.return_value = 200
    api.class_name.return_value = "ExternalApplicationWindow"
    monkeypatch.setattr(
        module, "MouseShortcutGuard",
        lambda provider: MouseShortcutGuard(provider, api=api, is_windows=True),
    )

    def create_hotkeys(settings, on_toggle, *, mouse_filter=None, on_paste=None,
                       paste_mouse_filter=None):
        manager = Mock()
        manager.mouse_filter = mouse_filter
        manager.on_toggle = on_toggle
        manager.on_paste = on_paste
        manager.paste_mouse_filter = paste_mouse_filter
        return manager

    monkeypatch.setattr(module, "HotkeyManager", create_hotkeys)
    return module.AppRuntime(load_settings(tmp_path / "config.toml"))


def test_capture_temporarily_blocks_all_recording_inputs(runtime):
    runtime.settings_service.suspend_shortcuts()
    runtime.controller.toggle()
    runtime.controller.recorder.start.assert_not_called()
    runtime.hotkeys.stop.assert_called_once()
    runtime.settings_service.resume_shortcuts()
    assert not runtime.controller._input_suspended
    runtime.hotkeys.start.assert_called_once()


def test_hotkey_and_style_apply_preserves_loaded_models(runtime):
    controller = runtime.controller
    adapters = controller.recorder, controller.transcriber, controller.refiner
    current = controller.settings
    updated = replace(current, hotkeys=HotkeySettings("<f8>", "x1"))
    runtime.settings_service.save(SettingsDraft(updated, "Short, natural sentences."))
    assert controller.settings == updated
    assert (controller.recorder, controller.transcriber, controller.refiner) == adapters
    runtime.hotkeys.reconfigure.assert_called_once_with(updated.hotkeys)
    assert not controller._input_suspended


def test_adapter_changes_apply_only_when_the_settings_change(runtime):
    controller = runtime.controller
    original = controller.recorder, controller.transcriber, controller.refiner
    current = controller.settings
    updated = replace(current, whisper=replace(current.whisper, language="en"))
    runtime.settings_service.save(SettingsDraft(updated, "Preserve the language of my dictation."))
    assert controller.recorder is original[0]
    assert controller.transcriber is not original[1]
    assert controller.refiner is original[2]
    runtime.hotkeys.reconfigure.assert_not_called()


def test_listener_failure_keeps_previous_runtime_and_files(runtime):
    current = runtime.controller.settings
    updated = replace(current, hotkeys=HotkeySettings("<f8>", ""))
    runtime.hotkeys.reconfigure.side_effect = RuntimeError("Could not register the shortcut.")
    original = current.config_path.read_bytes(), current.profile_path.read_bytes()
    with pytest.raises(RuntimeError, match="shortcut"):
        runtime.settings_service.save(SettingsDraft(updated, "New style."))
    assert runtime.controller.settings == current
    assert original == (current.config_path.read_bytes(), current.profile_path.read_bytes())
    assert not runtime.controller._input_suspended


def test_resume_failure_keeps_capture_guard_consistent(runtime):
    runtime.settings_service.suspend_shortcuts()
    runtime.hotkeys.start.side_effect = RuntimeError("Could not start shortcuts.")
    with pytest.raises(RuntimeError):
        runtime.settings_service.resume_shortcuts()
    assert runtime.settings_service._suspended
    assert runtime.controller._input_suspended
    runtime.settings_service.suspend_shortcuts()
    runtime.controller.toggle()
    runtime.controller.recorder.start.assert_not_called()
    runtime.hotkeys.start.side_effect = None
    runtime.settings_service.resume_shortcuts()
    assert not runtime.settings_service._suspended
    assert not runtime.controller._input_suspended


def test_late_resume_cannot_restart_listeners_after_shutdown(runtime):
    runtime.settings_service.suspend_shortcuts()
    runtime.shutdown()
    runtime.settings_service.resume_shortcuts()
    runtime.hotkeys.start.assert_not_called()
    with pytest.raises(RuntimeError, match="closing"):
        runtime._apply_settings(runtime.controller.settings)


def test_runtime_installs_the_guard_as_the_hotkey_mouse_filter(runtime):
    assert runtime.hotkeys.mouse_filter is runtime.mouse_guard


def test_saving_mouse_button_updates_the_live_window_guard_immediately(runtime):
    guard = runtime.mouse_guard
    api = guard._api
    api.process_id.return_value = os.getpid()
    current = runtime.controller.settings
    assert guard(10, 20)  # No global mouse shortcut configured.
    api.window_from_point.assert_not_called()

    for button, allowed, native_calls in (("left", False, 1), ("x1", True, 1), ("right", False, 2)):
        updated = replace(current, hotkeys=HotkeySettings(keyboard="", mouse_button=button))
        runtime.settings_service.save(SettingsDraft(updated, "Keep my meaning and tone."))
        assert runtime.mouse_guard is guard
        assert runtime.hotkeys.mouse_filter(10, 20) is allowed
        assert api.window_from_point.call_count == native_calls


def test_own_window_click_is_ignored_but_record_button_still_works(runtime):
    current = runtime.controller.settings
    updated = replace(current, hotkeys=HotkeySettings(keyboard="", mouse_button="left"))
    runtime.settings_service.save(SettingsDraft(updated, "Keep my meaning and tone."))
    runtime.mouse_guard._api.process_id.return_value = os.getpid()

    if runtime.hotkeys.mouse_filter(10, 20):
        runtime.hotkeys.on_toggle()
    runtime.controller.recorder.start.assert_not_called()

    try:
        runtime.controller.toggle()  # The window's Record command bypasses the global mouse filter.
        runtime.controller.recorder.start.assert_called_once()
    finally:
        runtime.shutdown()


def test_runtime_warms_model_once_after_observer_is_installed(runtime):
    states = []
    runtime.controller.set_observer(lambda state, message: states.append(state.value))
    runtime.start()
    assert runtime.controller.wait_for_idle()
    assert states == ["ready", "preparing", "ready"]
    runtime.controller.transcriber.prepare.assert_called_once()
    runtime.controller.recorder.start.assert_not_called()
    runtime.controller.clipboard.write_text.assert_not_called()
    runtime.start()
    runtime.hotkeys.start.assert_called_once()
    runtime.controller.transcriber.prepare.assert_called_once()
    runtime.shutdown()


def test_startup_shortcut_cannot_record_before_warmup_worker_exists(runtime):
    runtime.hotkeys.start.side_effect = runtime.hotkeys.on_toggle
    runtime.start()
    assert runtime.controller.wait_for_idle()
    runtime.controller.recorder.start.assert_not_called()
    runtime.controller.transcriber.prepare.assert_called_once()
    assert not runtime.controller._input_suspended
    runtime.shutdown()


def test_style_shortcut_and_audio_changes_reuse_warm_model(runtime):
    runtime.start()
    assert runtime.controller.wait_for_idle()
    original = runtime.controller.transcriber
    current = runtime.controller.settings
    updated = replace(current, hotkeys=HotkeySettings("<f8>", "middle"),
                      audio=replace(current.audio, device="USB microphone"))
    runtime.settings_service.save(SettingsDraft(updated, "A new writing style."))
    assert runtime.controller.transcriber is original
    original.prepare.assert_called_once()
    runtime.shutdown()


def test_changed_whisper_is_prepared_after_shortcuts_resume_successfully(runtime):
    runtime.start()
    assert runtime.controller.wait_for_idle()
    previous = runtime.controller.transcriber
    current = runtime.controller.settings
    updated = replace(current, whisper=replace(current.whisper, model="base"))
    runtime.settings_service.save(SettingsDraft(updated, "Keep my meaning."))
    assert runtime.controller.wait_for_idle()
    assert runtime.controller.transcriber is not previous
    runtime.controller.transcriber.prepare.assert_called_once()
    previous.prepare.assert_called_once()
    runtime.shutdown()


def test_shutdown_always_clears_hooks_even_when_controller_shutdown_fails(runtime):
    runtime.controller.shutdown = Mock(side_effect=RuntimeError("native cleanup failed"))
    with pytest.raises(RuntimeError, match="cleanup"):
        runtime.shutdown()
    runtime.hotkeys.stop.assert_called_once()
    assert runtime._closed
    runtime.shutdown()
    runtime.hotkeys.stop.assert_called_once()


def test_runtime_reports_pending_native_work_after_bounded_shutdown(runtime):
    started, release = threading.Event(), threading.Event()

    def prepare(**kwargs):
        started.set()
        assert release.wait(3)

    runtime.controller.transcriber.prepare.side_effect = prepare
    runtime.start()
    assert started.wait(3)
    try:
        assert not runtime.shutdown()
        assert runtime.has_active_worker
        runtime.hotkeys.stop.assert_called_once()
        runtime._resume_shortcuts()
        runtime.hotkeys.start.assert_called_once()
    finally:
        release.set()
    assert runtime.controller.wait_for_idle()
    assert not runtime.has_active_worker


def test_paste_shortcut_is_separate_from_recording_and_does_not_copy_text(runtime):
    runtime.hotkeys.on_paste()
    runtime.paste_macro.paste.assert_called_once_with()
    runtime.controller.recorder.start.assert_not_called()
    runtime.controller.clipboard.write_text.assert_not_called()


def test_settings_capture_and_shutdown_block_paste_callbacks(runtime):
    runtime.settings_service.suspend_shortcuts()
    runtime.hotkeys.on_paste()
    runtime.paste_macro.paste.assert_not_called()
    runtime.settings_service.resume_shortcuts()
    runtime.hotkeys.on_paste()
    runtime.paste_macro.paste.assert_called_once()
    runtime.shutdown()
    runtime.hotkeys.on_paste()
    runtime.paste_macro.paste.assert_called_once()
    runtime.paste_macro.close.assert_called_once()


def test_paste_only_changes_apply_and_preserve_loaded_adapters(runtime):
    controller = runtime.controller
    adapters = controller.recorder, controller.transcriber, controller.refiner
    current = controller.settings
    updated = replace(current, hotkeys=replace(current.hotkeys, paste_mouse_button="x2"))
    runtime.settings_service.save(SettingsDraft(updated, "Keep my style."))
    runtime.hotkeys.reconfigure.assert_called_once_with(updated.hotkeys)
    assert controller.settings == updated
    assert (controller.recorder, controller.transcriber, controller.refiner) == adapters
    assert not runtime._shortcuts_suspended


def test_paste_guard_uses_its_own_current_mouse_button(runtime):
    current = runtime.controller.settings
    updated = replace(current, hotkeys=replace(
        current.hotkeys, mouse_button="x1", paste_mouse_button="left",
    ))
    runtime.settings_service.save(SettingsDraft(updated, "Keep my style."))
    runtime.mouse_guard._api.process_id.return_value = os.getpid()
    runtime.paste_mouse_guard._api.process_id.return_value = os.getpid()
    assert runtime.hotkeys.mouse_filter(10, 20)
    assert not runtime.hotkeys.paste_mouse_filter(10, 20)


def test_paste_remains_blocked_after_shortcut_resume_failure(runtime):
    runtime.settings_service.suspend_shortcuts()
    runtime.hotkeys.start.side_effect = RuntimeError("Could not start shortcuts.")
    with pytest.raises(RuntimeError):
        runtime.settings_service.resume_shortcuts()
    runtime.hotkeys.on_paste()
    runtime.paste_macro.paste.assert_not_called()
    assert runtime._shortcuts_suspended


def test_failed_controller_suspension_does_not_disable_paste_forever(runtime):
    runtime.controller.suspend_input = Mock(side_effect=RuntimeError("Recording became busy"))
    with pytest.raises(RuntimeError, match="busy"):
        runtime._suspend_shortcuts()
    assert not runtime._shortcuts_suspended
    runtime.hotkeys.on_paste()
    runtime.paste_macro.paste.assert_called_once_with()


def test_completed_dictation_never_automatically_pastes(runtime):
    import numpy as np

    controller = runtime.controller
    controller._model_prepared = True
    controller.recorder.stop.return_value = np.ones(16000, dtype=np.float32)
    controller.transcriber.transcribe.return_value = "A synthetic message."
    controller.refiner.refine.return_value = "A refined message."
    try:
        controller.toggle()
        controller.toggle()
        assert controller.wait_for_idle()
        controller.clipboard.write_text.assert_called_once_with("A refined message.")
        runtime.paste_macro.paste.assert_not_called()
        runtime.hotkeys.on_paste()
        runtime.paste_macro.paste.assert_called_once_with()
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("failing_cleanup", ["paste_macro", "controller", "hotkeys"])
def test_paste_macro_is_closed_even_when_other_shutdown_cleanup_fails(runtime, failing_cleanup):
    target = {"paste_macro": runtime.paste_macro, "controller": runtime.controller,
              "hotkeys": runtime.hotkeys}[failing_cleanup]
    method = {"paste_macro": "close", "controller": "shutdown", "hotkeys": "stop"}[failing_cleanup]
    setattr(target, method,
            Mock(side_effect=RuntimeError("cleanup failed")))
    with pytest.raises(RuntimeError, match="cleanup failed"):
        runtime.shutdown()
    runtime.paste_macro.close.assert_called_once()
    runtime.hotkeys.on_paste()
    runtime.paste_macro.paste.assert_not_called()


def test_shutdown_cancels_paste_before_waiting_for_native_speech(runtime):
    calls = Mock()
    runtime.controller.shutdown = Mock(return_value=True)
    calls.attach_mock(runtime.paste_macro.close, "close_paste")
    calls.attach_mock(runtime.controller.shutdown, "shutdown_speech")
    calls.attach_mock(runtime.hotkeys.stop, "stop_hooks")
    assert runtime.shutdown()
    assert [call[0] for call in calls.mock_calls] == ["close_paste", "shutdown_speech", "stop_hooks"]


def test_runtime_in_local_mode_does_not_construct_or_require_codex(runtime, monkeypatch):
    from voice_to_me import runtime as module

    constructor = Mock(side_effect=AssertionError("Local mode must not initialize Codex"))
    monkeypatch.setattr(module, "CodexRefiner", constructor)
    settings = runtime.controller.settings
    local = module.AppRuntime(replace(settings, codex=replace(settings.codex, enabled=False)))
    try:
        assert local.controller.refiner is None
        constructor.assert_not_called()
    finally:
        local.shutdown()


def test_refinement_toggle_preserves_resident_whisper_and_persists_setting(runtime, monkeypatch):
    from voice_to_me import runtime as module

    constructor = Mock(return_value=Mock())
    monkeypatch.setattr(module, "CodexRefiner", constructor)
    controller = runtime.controller
    recorder, transcriber = controller.recorder, controller.transcriber
    settings = controller.settings
    disabled = replace(settings, codex=replace(settings.codex, enabled=False))
    runtime.settings_service.save(SettingsDraft(disabled, "A retained optional writing style."))
    assert controller.refiner is None
    constructor.assert_not_called()
    assert not load_settings(settings.config_path).codex.enabled
    assert controller.recorder is recorder and controller.transcriber is transcriber

    enabled = replace(disabled, codex=replace(disabled.codex, enabled=True))
    runtime.settings_service.save(SettingsDraft(enabled, "A retained optional writing style."))
    constructor.assert_called_once_with(enabled.codex)
    assert controller.refiner is constructor.return_value
    assert load_settings(settings.config_path).codex.enabled
    assert controller.recorder is recorder and controller.transcriber is transcriber
    runtime.hotkeys.reconfigure.assert_not_called()


def test_model_changes_in_local_mode_do_not_construct_codex(runtime, monkeypatch):
    from voice_to_me import runtime as module

    constructor = Mock(side_effect=AssertionError("Local mode must not initialize Codex"))
    monkeypatch.setattr(module, "CodexRefiner", constructor)
    settings = runtime.controller.settings
    disabled = replace(settings, codex=replace(settings.codex, enabled=False))
    runtime.settings_service.save(SettingsDraft(disabled, "A retained writing style."))
    changed = replace(disabled, whisper=replace(disabled.whisper, language="en"))
    runtime.settings_service.save(SettingsDraft(changed, "A retained writing style."))
    assert runtime.controller.refiner is None
    constructor.assert_not_called()
