from __future__ import annotations

import threading
import time
from dataclasses import replace
from unittest.mock import Mock

import numpy as np
import pytest

from voice_to_me.config import load_settings
from voice_to_me.controller import AppController, AppState


@pytest.fixture
def pipeline(tmp_path):
    settings = load_settings(tmp_path / "config.toml")
    recorder = Mock()
    recorder.stop.return_value = np.ones(16000, dtype=np.float32)
    transcriber = Mock()
    transcriber.transcribe.return_value = "olá eh pessoal revisão hoje"
    refiner = Mock()
    refiner.refine.return_value = "Olá, pessoal. A revisão será hoje."
    clipboard = Mock()
    controller = AppController(settings, recorder, transcriber, refiner, clipboard)
    controller._model_prepared = True  # These adapters represent an already warm model.
    states = []
    controller.set_observer(lambda state, message: states.append(state))
    yield controller, recorder, transcriber, refiner, clipboard, states
    controller.shutdown()


def finish_recording(controller):
    controller.toggle()
    controller.toggle()
    assert controller.wait_for_idle()


def test_toggle_complete_pipeline_copies_only_final(pipeline):
    controller, recorder, transcriber, refiner, clipboard, states = pipeline
    finish_recording(controller)
    assert states == [AppState.READY, AppState.RECORDING, AppState.TRANSCRIBING,
                      AppState.REFINING, AppState.COPIED]
    clipboard.write_text.assert_called_once_with("Olá, pessoal. A revisão será hoje.")
    recorder.start.assert_called_once()
    recorder.stop.assert_called_once()
    recorder.cancel.assert_called_once()
    assert controller._audio is None and not controller._transcript


def disable_refinement(controller):
    controller.settings = replace(controller.settings,
                                  codex=replace(controller.settings.codex, enabled=False))


def test_local_mode_copies_valid_transcription_without_profile_or_refiner(pipeline, monkeypatch):
    from voice_to_me import controller as module

    controller, _, transcriber, refiner, clipboard, states = pipeline
    disable_refinement(controller)
    profile = Mock(side_effect=AssertionError("The profile must not be read in local mode"))
    monkeypatch.setattr(module, "read_profile", profile)
    controller.settings.profile_path.unlink()
    transcriber.transcribe.return_value = "  Olá, equipe! 👋\nA revisão é hoje.  "
    cues = []
    controller.set_sound_observer(cues.append)
    finish_recording(controller)
    assert controller.state is AppState.COPIED
    clipboard.write_text.assert_called_once_with("Olá, equipe! 👋\nA revisão é hoje.")
    refiner.refine.assert_not_called()
    profile.assert_not_called()
    assert AppState.REFINING not in states
    assert "refinement" not in controller.timings
    assert cues == ["recording_started", "recording_stopped", "ready"]


@pytest.mark.parametrize("result", ["", "  ", None, "invalid\x00text"])
def test_invalid_local_transcription_preserves_clipboard(pipeline, result):
    controller, _, transcriber, refiner, clipboard, _ = pipeline
    disable_refinement(controller)
    transcriber.transcribe.return_value = result
    finish_recording(controller)
    assert controller.state is AppState.ERROR
    refiner.refine.assert_not_called()
    clipboard.write_text.assert_not_called()


def test_local_clipboard_retry_reuses_transcript_without_recording_or_codex(pipeline):
    controller, recorder, transcriber, refiner, clipboard, states = pipeline
    disable_refinement(controller)
    controller.refiner = None
    clipboard.write_text.side_effect = [RuntimeError("Clipboard busy"), None]
    cues = []
    controller.set_sound_observer(cues.append)
    finish_recording(controller)
    assert controller.state is AppState.ERROR
    controller.retry()
    assert controller.wait_for_idle()
    assert controller.state is AppState.COPIED
    recorder.start.assert_called_once()
    transcriber.transcribe.assert_called_once()
    refiner.refine.assert_not_called()
    assert clipboard.write_text.call_count == 2
    assert clipboard.write_text.call_args.args == (transcriber.transcribe.return_value,)
    assert AppState.REFINING not in states
    assert cues.count("ready") == 1


def test_cancelling_local_transcription_preserves_clipboard_and_has_no_ready_sound(pipeline):
    controller, _, transcriber, refiner, clipboard, _ = pipeline
    disable_refinement(controller)
    entered, release = threading.Event(), threading.Event()

    def transcribe(*_, **__):
        entered.set()
        assert release.wait(2)
        return "A cancelled local message."

    transcriber.transcribe.side_effect = transcribe
    cues = []
    controller.set_sound_observer(cues.append)
    controller.toggle()
    controller.toggle()
    assert entered.wait(2)
    controller.cancel()
    release.set()
    assert controller.wait_for_idle()
    assert controller.state is AppState.READY
    refiner.refine.assert_not_called()
    clipboard.write_text.assert_not_called()
    assert "ready" not in cues


def test_local_transcription_retry_reuses_recorded_audio(pipeline):
    controller, recorder, transcriber, refiner, clipboard, _ = pipeline
    disable_refinement(controller)
    transcriber.transcribe.side_effect = [RuntimeError("Model busy"), "A local message."]
    finish_recording(controller)
    assert controller.state is AppState.ERROR
    controller.retry()
    assert controller.wait_for_idle()
    assert controller.state is AppState.COPIED
    recorder.start.assert_called_once()
    assert transcriber.transcribe.call_count == 2
    refiner.refine.assert_not_called()
    clipboard.write_text.assert_called_once_with("A local message.")


@pytest.mark.parametrize("result", ["", "  ", None, "bad\x00text"])
def test_invalid_refined_output_preserves_clipboard(pipeline, result):
    controller, _, _, refiner, clipboard, _ = pipeline
    refiner.refine.return_value = result
    finish_recording(controller)
    assert controller.state is AppState.ERROR
    clipboard.write_text.assert_not_called()


def test_empty_transcription_does_not_call_codex(pipeline):
    controller, _, transcriber, refiner, clipboard, _ = pipeline
    transcriber.transcribe.return_value = "  "
    finish_recording(controller)
    assert controller.state is AppState.ERROR
    refiner.refine.assert_not_called()
    clipboard.write_text.assert_not_called()


def test_retry_refiner_failure_reuses_transcript_and_reloads_profile(pipeline):
    controller, recorder, transcriber, refiner, clipboard, _ = pipeline
    refiner.refine.side_effect = [RuntimeError("Codex indisponível."), "Mensagem revisada."]
    finish_recording(controller)
    assert controller.state is AppState.ERROR
    controller.settings.profile_path.write_text("Use frases mais curtas.", encoding="utf-8")
    controller.retry()
    assert controller.wait_for_idle()
    assert controller.state is AppState.COPIED
    recorder.start.assert_called_once()
    transcriber.transcribe.assert_called_once()
    assert refiner.refine.call_args.args[1] == "Use frases mais curtas."
    clipboard.write_text.assert_called_once_with("Mensagem revisada.")


def test_retry_transcription_failure_reuses_audio(pipeline):
    controller, recorder, transcriber, _, clipboard, _ = pipeline
    transcriber.transcribe.side_effect = [RuntimeError("Modelo indisponível."), "olá"]
    finish_recording(controller)
    controller.retry()
    assert controller.wait_for_idle()
    assert controller.state is AppState.COPIED
    recorder.start.assert_called_once()
    assert transcriber.transcribe.call_count == 2
    clipboard.write_text.assert_called_once()


def test_cancel_refinement_discards_result_and_busy_toggle_does_nothing(pipeline):
    controller, recorder, _, refiner, clipboard, _ = pipeline
    started, release = threading.Event(), threading.Event()

    def wait(*_, **__):
        started.set()
        assert release.wait(3)
        return "This must not be copied"

    refiner.refine.side_effect = wait
    controller.toggle()
    controller.toggle()
    assert started.wait(3)
    controller.toggle()
    recorder.start.assert_called_once()
    controller.cancel()
    release.set()
    assert controller.wait_for_idle()
    assert controller.state is AppState.READY
    clipboard.write_text.assert_not_called()
    assert controller._audio is None and not controller._transcript


def test_short_recording_preserves_clipboard_and_closes_recorder(pipeline):
    controller, recorder, transcriber, _, clipboard, _ = pipeline
    recorder.stop.return_value = np.zeros(100, dtype=np.float32)
    finish_recording(controller)
    assert controller.state is AppState.ERROR
    transcriber.transcribe.assert_not_called()
    clipboard.write_text.assert_not_called()
    recorder.cancel.assert_called_once()


def test_start_failure_can_be_recovered_by_new_toggle(pipeline):
    controller, recorder, _, _, clipboard, _ = pipeline
    recorder.start.side_effect = [RuntimeError("Microfone indisponível."), None]
    controller.toggle()
    assert controller.wait_for_idle()
    assert controller.state is AppState.ERROR
    finish_recording(controller)
    assert controller.state is AppState.COPIED
    clipboard.write_text.assert_called_once()


def test_invalid_profile_preserves_clipboard(pipeline):
    controller, _, _, refiner, clipboard, _ = pipeline
    controller.settings.profile_path.write_text("", encoding="utf-8")
    finish_recording(controller)
    assert controller.state is AppState.ERROR
    refiner.refine.assert_not_called()
    clipboard.write_text.assert_not_called()


def test_error_logs_never_include_transcript(pipeline, caplog):
    controller, _, transcriber, refiner, _, _ = pipeline
    transcriber.transcribe.return_value = "sensitive invented marker"
    refiner.refine.side_effect = RuntimeError("Refinement unavailable")
    finish_recording(controller)
    assert "sensitive invented marker" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_shutdown_cancels_recording_and_does_not_restart(pipeline):
    controller, recorder, _, refiner, clipboard, _ = pipeline
    controller.toggle()
    controller.shutdown()
    controller.toggle()
    assert controller.wait_for_idle()
    refiner.refine.assert_not_called()
    clipboard.write_text.assert_not_called()


def test_clipboard_failure_is_visible_and_retryable(pipeline):
    controller, _, _, _, clipboard, _ = pipeline
    clipboard.write_text.side_effect = [RuntimeError("Clipboard ocupado."), None]
    finish_recording(controller)
    assert controller.state is AppState.ERROR
    controller.retry()
    assert controller.wait_for_idle()
    assert controller.state is AppState.COPIED


def test_startup_model_preparation_never_opens_microphone_or_changes_clipboard(pipeline):
    controller, recorder, transcriber, refiner, clipboard, states = pipeline
    controller._model_prepared = False
    transcriber.backend_description = "CUDA / int8_float16"
    assert controller.prepare()
    assert controller.wait_for_idle()
    assert states == [AppState.READY, AppState.PREPARING, AppState.READY]
    transcriber.prepare.assert_called_once()
    recorder.start.assert_not_called()
    refiner.refine.assert_not_called()
    clipboard.write_text.assert_not_called()
    assert "GPU" in controller.message
    assert "int8_float16" not in controller.message


@pytest.mark.parametrize("backend, label", [
    ("GPU (CUDA) / int8_float16", "GPU"),
    ("CPU / int8", "CPU"),
    ("Local model ready (backend not reported).", "your computer"),
])
def test_ready_status_uses_friendly_backend_names_while_logs_keep_precision(pipeline, caplog,
                                                                         backend, label):
    controller, _, transcriber, _, _, _ = pipeline
    transcriber.backend_description = backend
    controller._model_prepared = False
    caplog.set_level("INFO", logger="voice_to_me.controller")
    assert controller.prepare()
    assert controller.wait_for_idle()
    assert f"ready on {label}" in controller.message
    assert "int8" not in controller.message and "CUDA" not in controller.message
    assert f"backend={backend}" in caplog.text
    assert "preparation" in controller.timings
    assert not controller.prepare()  # The resident model is reused.


def test_warming_blocks_recording_and_settings_until_prepared(pipeline):
    controller, recorder, transcriber, _, clipboard, _ = pipeline
    started, release = threading.Event(), threading.Event()

    def warm(**kwargs):
        started.set()
        assert release.wait(3)

    controller._model_prepared = False
    transcriber.prepare.side_effect = warm
    assert controller.prepare()
    assert started.wait(3)
    try:
        assert controller.is_busy
        assert controller.state is AppState.PREPARING
        controller.toggle()
        recorder.start.assert_not_called()
        assert not controller.prepare()
        with pytest.raises(RuntimeError, match="Finish or cancel"):
            controller.suspend_input()
    finally:
        release.set()
    assert controller.wait_for_idle()
    assert controller.state is AppState.READY
    clipboard.write_text.assert_not_called()


def test_missing_model_error_is_actionable_and_retry_prepares_again(pipeline):
    controller, recorder, transcriber, _, clipboard, _ = pipeline
    controller._model_prepared = False
    transcriber.prepare.side_effect = [RuntimeError("Download the local model in Settings."), None]
    controller.prepare()
    assert controller.wait_for_idle()
    assert controller.state is AppState.ERROR
    assert "Download" in controller.message
    controller.retry()
    assert controller.wait_for_idle()
    assert controller.state is AppState.READY
    assert transcriber.prepare.call_count == 2
    recorder.start.assert_not_called()
    clipboard.write_text.assert_not_called()


def test_cancel_warmup_waits_safely_for_native_call_and_preserves_clipboard(pipeline):
    controller, _, transcriber, _, clipboard, _ = pipeline
    started, release = threading.Event(), threading.Event()

    def warm(**kwargs):
        started.set()
        assert release.wait(3)

    controller._model_prepared = False
    transcriber.prepare.side_effect = warm
    controller.prepare()
    assert started.wait(3)
    controller.cancel()
    assert "Cancelling" in controller.message
    assert controller.is_busy
    release.set()
    assert controller.wait_for_idle()
    assert controller.state is AppState.READY
    assert not controller._model_prepared
    clipboard.write_text.assert_not_called()


def test_shutdown_is_bounded_during_native_warmup_and_suppresses_late_progress(pipeline):
    controller, _, transcriber, _, clipboard, states = pipeline
    started, release = threading.Event(), threading.Event()
    progress = []

    def warm(**kwargs):
        progress.append(kwargs["progress"])
        started.set()
        assert release.wait(3)
        kwargs["progress"]("transcribing", "A stale update that must never reach the UI")

    controller._model_prepared = False
    transcriber.prepare.side_effect = warm
    controller.prepare()
    assert started.wait(3)
    try:
        before = time.monotonic()
        assert not controller.shutdown(timeout_seconds=0.02)
        assert time.monotonic() - before < 0.3
        assert controller.has_active_worker
        assert controller._worker.daemon and controller._heartbeat.daemon
        assert not controller._heartbeat.is_alive()
        original = list(states)
        progress[0]("preparing", "Another stale update")
        controller.set_observer(lambda *_: states.append("should not run"))
        controller.toggle()
        controller.cancel()
        assert states == original
        assert not controller.shutdown(timeout_seconds=0.02)
    finally:
        release.set()
    assert controller.wait_for_idle()
    assert not controller.has_active_worker
    assert states == original
    clipboard.write_text.assert_not_called()


def test_shutdown_during_native_transcription_never_copies_or_refines(pipeline):
    controller, recorder, transcriber, refiner, clipboard, states = pipeline
    started, release = threading.Event(), threading.Event()

    def decode(*args, **kwargs):
        started.set()
        assert release.wait(3)
        kwargs["progress"]("transcribing", "Late native decoder progress")
        return "This result must not be used."

    transcriber.transcribe.side_effect = decode
    controller.toggle()
    controller.toggle()
    assert started.wait(3)
    try:
        assert not controller.shutdown(timeout_seconds=0.02)
        previous = list(states)
        assert controller.has_active_worker
    finally:
        release.set()
    assert controller.wait_for_idle()
    assert states == previous
    assert not controller.has_active_worker
    refiner.refine.assert_not_called()
    clipboard.write_text.assert_not_called()
    recorder.cancel.assert_called_once()


def test_step_and_total_timings_never_log_private_content(pipeline, caplog):
    controller, _, transcriber, refiner, clipboard, _ = pipeline
    transcriber.transcribe.return_value = "PRIVATE_TRANSCRIPT_MARKER"
    refiner.refine.return_value = "PRIVATE_OUTPUT_MARKER"
    controller.settings.profile_path.write_text("PRIVATE_PROFILE_MARKER", encoding="utf-8")
    with caplog.at_level("INFO", logger="voice_to_me.controller"):
        finish_recording(controller)
    assert set(controller.timings) == {"recording", "transcription", "refinement", "clipboard"}
    assert all(value >= 0 for value in controller.timings.values())
    assert "Step transcription completed" in caplog.text
    assert "Dictation completed" in caplog.text
    assert "PRIVATE" not in caplog.text
    assert "Total" in controller.message
    clipboard.write_text.assert_called_once_with("PRIVATE_OUTPUT_MARKER")


def test_long_step_updates_elapsed_status_without_contents(pipeline):
    controller, _, _, refiner, _, _ = pipeline
    controller._heartbeat_interval = 0.01
    started, release, updated = threading.Event(), threading.Event(), threading.Event()
    messages = []

    def observe(state, message):
        messages.append(message)
        if state is AppState.REFINING and "in this step" in message:
            updated.set()

    def wait(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return "Ready to paste."

    controller.set_observer(observe)
    refiner.refine.side_effect = wait
    controller.toggle()
    controller.toggle()
    assert started.wait(3)
    try:
        assert updated.wait(3)
        assert all("olá eh pessoal" not in message for message in messages)
    finally:
        release.set()
    assert controller.wait_for_idle()
    assert not controller._heartbeat.is_alive()


def test_shutdown_is_idempotent_and_all_cooperative_threads_finish(pipeline):
    controller, recorder, _, _, clipboard, states = pipeline
    controller.toggle()
    assert controller.shutdown()
    before = list(states)
    assert controller.shutdown()
    assert controller.wait_for_idle()
    assert not controller._worker.is_alive()
    assert not controller._heartbeat.is_alive()
    recorder.cancel.assert_called_once()
    clipboard.write_text.assert_not_called()
    assert states == before


def test_thread_start_failure_is_visible_and_does_not_leave_a_busy_controller(pipeline, monkeypatch):
    controller, recorder, _, _, clipboard, _ = pipeline
    monkeypatch.setattr(controller, "_new_operation", Mock(side_effect=RuntimeError("no thread")))
    controller.toggle()
    assert controller.state is AppState.ERROR
    assert not controller.is_busy
    recorder.start.assert_not_called()
    clipboard.write_text.assert_not_called()


def test_cold_transcription_progress_separates_loading_from_actual_decoding(pipeline):
    controller, _, transcriber, _, clipboard, states = pipeline
    controller._model_prepared = False

    def transcribe(*args, **kwargs):
        kwargs["progress"]("preparing", "Loading the local speech model.")
        assert controller.state is AppState.PREPARING
        kwargs["progress"]("transcribing", "Decoding speech locally.")
        assert controller.state is AppState.TRANSCRIBING
        return "A simulated dictation."

    transcriber.transcribe.side_effect = transcribe
    finish_recording(controller)
    assert states.index(AppState.PREPARING) < states.index(AppState.TRANSCRIBING)
    assert {"preparation", "transcription"} <= controller.timings.keys()
    assert controller.state is AppState.COPIED
    clipboard.write_text.assert_called_once()


def test_sound_cues_follow_successful_adapter_boundaries(pipeline):
    controller, recorder, transcriber, refiner, clipboard, _ = pipeline
    events = []
    recorder.start.side_effect = lambda: events.append("microphone_started")

    def stop():
        events.append("microphone_stopped")
        return np.ones(16000, dtype=np.float32)

    recorder.stop.side_effect = stop
    transcriber.transcribe.side_effect = lambda *_, **__: events.append("transcribed") or "Hello"
    refiner.refine.side_effect = lambda *_, **__: events.append("refined") or "Hello."
    clipboard.write_text.side_effect = lambda _: events.append("clipboard_written")
    controller.set_observer(lambda state, _: events.append("copied") if state is AppState.COPIED else None)
    controller.set_sound_observer(events.append)
    assert events == []
    finish_recording(controller)
    assert events == [
        "microphone_started", "recording_started", "microphone_stopped", "recording_stopped",
        "transcribed", "refined", "clipboard_written", "copied", "ready",
    ]
    controller.set_sound_observer(events.append)
    assert events[-1] == "ready" and events.count("ready") == 1


def test_microphone_start_failure_has_no_sound_cues(pipeline):
    controller, recorder, _, _, clipboard, _ = pipeline
    cues = []
    controller.set_sound_observer(cues.append)
    recorder.start.side_effect = RuntimeError("Unavailable microphone")
    controller.toggle()
    assert controller.wait_for_idle()
    assert controller.state is AppState.ERROR
    assert cues == []
    recorder.cancel.assert_called_once()
    clipboard.write_text.assert_not_called()


def test_short_recording_still_has_one_stop_cue_without_ready(pipeline):
    controller, recorder, _, _, _, _ = pipeline
    cues = []
    controller.set_sound_observer(cues.append)
    recorder.stop.return_value = np.zeros(100, dtype=np.float32)
    finish_recording(controller)
    assert controller.state is AppState.ERROR
    assert cues == ["recording_started", "recording_stopped"]


@pytest.mark.parametrize("failing_adapter", ["transcriber", "refiner", "clipboard"])
def test_retry_cues_do_not_repeat_recording_boundaries(pipeline, failing_adapter):
    controller, recorder, transcriber, refiner, clipboard, _ = pipeline
    cues = []
    controller.set_sound_observer(cues.append)
    adapter = {
        "transcriber": transcriber.transcribe,
        "refiner": refiner.refine,
        "clipboard": clipboard.write_text,
    }[failing_adapter]
    adapter.side_effect = [RuntimeError("Temporary failure"), adapter.return_value]
    finish_recording(controller)
    assert controller.state is AppState.ERROR
    assert cues == ["recording_started", "recording_stopped"]
    controller.retry()
    assert controller.wait_for_idle()
    assert controller.state is AppState.COPIED
    assert cues == ["recording_started", "recording_stopped", "ready"]
    recorder.start.assert_called_once()
    recorder.stop.assert_called_once()


def test_recording_cancel_plays_stop_after_cleanup_once(pipeline):
    controller, recorder, _, _, clipboard, _ = pipeline
    started = threading.Event()
    events = []

    def observe(cue):
        events.append(cue)
        if cue == "recording_started":
            started.set()

    recorder.cancel.side_effect = lambda: events.append("microphone_cancelled")
    controller.set_sound_observer(observe)
    controller.toggle()
    assert started.wait(3)
    controller.cancel()
    assert controller.wait_for_idle()
    controller.cancel()
    assert events == ["recording_started", "microphone_cancelled", "recording_stopped"]
    recorder.stop.assert_not_called()
    clipboard.write_text.assert_not_called()


def test_failed_recorder_stop_plays_stop_after_successful_cleanup(pipeline):
    controller, recorder, _, _, _, _ = pipeline
    cues = []
    controller.set_sound_observer(cues.append)
    recorder.stop.side_effect = RuntimeError("Stop unavailable")
    finish_recording(controller)
    assert controller.state is AppState.ERROR
    recorder.cancel.assert_called_once()
    assert cues == ["recording_started", "recording_stopped"]


@pytest.mark.parametrize("closing", [False, True])
def test_cancel_or_shutdown_during_start_never_plays_late_start(pipeline, closing):
    controller, recorder, _, _, clipboard, _ = pipeline
    started, release = threading.Event(), threading.Event()
    cues = []

    def start():
        started.set()
        assert release.wait(3)

    controller.set_sound_observer(cues.append)
    recorder.start.side_effect = start
    controller.toggle()
    assert started.wait(3)
    try:
        if closing:
            assert not controller.shutdown(timeout_seconds=0.01)
            controller.set_sound_observer(lambda _: cues.append("late_observer"))
        else:
            controller.cancel()
    finally:
        release.set()
    assert controller.wait_for_idle()
    assert cues == ([] if closing else ["recording_stopped"])
    recorder.cancel.assert_called_once()
    recorder.stop.assert_not_called()
    clipboard.write_text.assert_not_called()


def test_shutdown_during_recording_detaches_stop_cue(pipeline):
    controller, recorder, _, _, clipboard, _ = pipeline
    started = threading.Event()
    cues = []

    def observe(cue):
        cues.append(cue)
        if cue == "recording_started":
            started.set()

    controller.set_sound_observer(observe)
    controller.toggle()
    assert started.wait(3)
    assert controller.shutdown()
    assert cues == ["recording_started"]
    recorder.cancel.assert_called_once()
    clipboard.write_text.assert_not_called()


@pytest.mark.parametrize("closing", [False, True])
def test_cancel_or_shutdown_refinement_suppresses_ready_cue(pipeline, closing):
    controller, _, _, refiner, clipboard, _ = pipeline
    started, release = threading.Event(), threading.Event()
    cues = []

    def refine(*_, **__):
        started.set()
        assert release.wait(3)
        return "Late output."

    controller.set_sound_observer(cues.append)
    refiner.refine.side_effect = refine
    controller.toggle()
    controller.toggle()
    assert started.wait(3)
    try:
        if closing:
            assert not controller.shutdown(timeout_seconds=0.01)
        else:
            controller.cancel()
    finally:
        release.set()
    assert controller.wait_for_idle()
    assert cues == ["recording_started", "recording_stopped"]
    clipboard.write_text.assert_not_called()


def test_sound_observer_failure_never_interrupts_pipeline_or_logs_contents(pipeline, caplog):
    controller, _, _, _, clipboard, _ = pipeline
    cues = []

    def broken(cue):
        cues.append(cue)
        raise RuntimeError("PRIVATE_SOUND_CALLBACK_MARKER")

    controller.set_sound_observer(broken)
    finish_recording(controller)
    assert controller.state is AppState.COPIED
    clipboard.write_text.assert_called_once()
    assert cues == ["recording_started", "recording_stopped", "ready"]
    assert caplog.text.count("Sound observer failed (RuntimeError)") == 3
    assert "PRIVATE_SOUND_CALLBACK_MARKER" not in caplog.text


def test_model_warmup_is_silent_and_sound_observer_can_be_disabled(pipeline):
    controller, _, _, _, _, _ = pipeline
    cues = []
    controller.set_sound_observer(cues.append)
    controller._model_prepared = False
    assert controller.prepare()
    assert controller.wait_for_idle()
    assert controller.state is AppState.READY
    assert cues == []
    controller.set_sound_observer(None)
    finish_recording(controller)
    assert controller.state is AppState.COPIED
    assert cues == []


def test_copied_status_cancel_suppresses_ready_sound(pipeline):
    controller, _, _, _, clipboard, _ = pipeline
    cues = []
    controller.set_sound_observer(cues.append)
    requested = False

    def observe(state, _):
        nonlocal requested
        if state is AppState.COPIED and not requested:
            requested = True
            controller.cancel()

    controller.set_observer(observe)
    finish_recording(controller)
    clipboard.write_text.assert_called_once()
    assert cues == ["recording_started", "recording_stopped"]
