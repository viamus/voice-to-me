"""Session history behavior using fake audio, refinement and clipboard adapters."""

from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError, replace
from unittest.mock import Mock

import pytest

from voice_to_me.config import ConfigurationError, load_settings
from voice_to_me.controller import AppController, AppState
from voice_to_me.history import MAX_HISTORY_ENTRIES, HistoryEntry


@pytest.fixture
def controller(tmp_path):
    settings = load_settings(tmp_path / "config.toml")
    recorder = Mock()
    recorder.stop.return_value = [0.0] * 16000
    transcriber = Mock()
    transcriber.transcribe.return_value = "A local synthetic transcription."
    refiner = Mock()
    refiner.refine.return_value = "A refined synthetic result."
    controller = AppController(settings, recorder, transcriber, refiner, Mock())
    controller._model_prepared = True
    yield controller
    controller.shutdown()


def complete(controller):
    controller.toggle()
    controller.toggle()
    assert controller.wait_for_idle()


def test_success_records_exact_final_unicode_text_and_immutable_metadata(controller):
    controller.refiner.refine.return_value = "  Olá, João! 👋\nReunião às 14h — tudo bem?  "
    complete(controller)
    (entry,) = controller.history
    assert isinstance(entry, HistoryEntry)
    assert entry.id == 1 and entry.refined
    assert entry.text == "Olá, João! 👋\nReunião às 14h — tudo bem?"
    assert entry.created_at.tzinfo is not None and entry.created_at.utcoffset() is not None
    assert isinstance(entry.elapsed_seconds, float) and entry.elapsed_seconds >= 0
    assert controller.clipboard.write_text.call_args.args == (entry.text,)
    with pytest.raises(FrozenInstanceError):
        entry.text = "Changed"


def test_local_transcription_entry_marks_refinement_off(controller):
    controller.settings = replace(controller.settings,
                                  codex=replace(controller.settings.codex, enabled=False))
    complete(controller)
    (entry,) = controller.history
    assert entry.text == controller.transcriber.transcribe.return_value
    assert not entry.refined
    controller.refiner.refine.assert_not_called()


def test_history_is_newest_first_and_bounded_to_thirty_results(controller):
    assert MAX_HISTORY_ENTRIES == 30
    for number in range(35):
        controller.refiner.refine.return_value = f"Synthetic result {number}."
        complete(controller)
    assert len(controller.history) == 30
    assert [entry.id for entry in controller.history] == list(range(35, 5, -1))
    assert controller.history[0].text == "Synthetic result 34."
    assert controller.history[-1].text == "Synthetic result 5."
    assert not controller.copy_history(1)


def test_observer_receives_initial_snapshot_success_and_explicit_clear(controller):
    snapshots = []
    controller.set_history_observer(snapshots.append)
    assert snapshots == [()]
    complete(controller)
    assert snapshots == [(), controller.history]
    assert controller.clear_history()
    assert snapshots[-1] == () and controller.history == ()
    assert len(snapshots) == 3


def test_installing_observer_for_existing_history_only_replays_snapshot(controller):
    complete(controller)
    controller.clipboard.write_text.reset_mock()
    snapshots = []
    controller.set_history_observer(snapshots.append)
    controller.set_history_observer(snapshots.append)
    assert snapshots == [controller.history, controller.history]
    assert len(controller.history) == 1
    controller.clipboard.write_text.assert_not_called()


def test_observer_failures_never_fail_pipeline_or_expose_text(controller, caplog):
    secret = "A private callback and dictation marker."
    controller.refiner.refine.return_value = secret
    controller.set_history_observer(Mock(side_effect=RuntimeError(secret)))
    complete(controller)
    assert controller.state is AppState.COPIED
    assert len(controller.history) == 1 and controller.history[0].text == secret
    assert secret not in caplog.text
    assert "History observer failed (RuntimeError)" in caplog.text
    assert controller.clear_history()
    assert controller.history == ()


def test_history_text_is_never_in_status_or_logs(controller, caplog):
    marker = "Unique private final dictation marker 9ca395."
    controller.refiner.refine.return_value = marker
    statuses = []
    controller.set_observer(lambda state, message: statuses.append(message))
    complete(controller)
    controller.copy_history(controller.history[0].id)
    assert marker not in caplog.text
    assert all(marker not in message for message in statuses)


def test_session_history_does_not_write_or_modify_files(controller):
    folder = controller.settings.config_path.parent
    before = {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}
    complete(controller)
    assert controller.history
    after = {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}
    assert after == before


@pytest.mark.parametrize("result", ["", "  ", None, "invalid\x00text"])
def test_invalid_final_result_creates_no_history_entry(controller, result):
    controller.refiner.refine.return_value = result
    snapshots = []
    controller.set_history_observer(snapshots.append)
    complete(controller)
    assert controller.state is AppState.ERROR and controller.history == ()
    assert snapshots == [()]
    controller.clipboard.write_text.assert_not_called()


def test_failed_clipboard_write_adds_no_entry_and_retry_adds_exactly_one(controller):
    controller.clipboard.write_text.side_effect = [RuntimeError("Clipboard busy"), None]
    snapshots = []
    controller.set_history_observer(snapshots.append)
    complete(controller)
    assert controller.history == () and snapshots == [()]
    controller.retry()
    assert controller.wait_for_idle()
    assert controller.state is AppState.COPIED and len(controller.history) == 1
    assert len(snapshots) == 2
    controller.retry()  # Retry outside ERROR cannot duplicate a completed entry.
    assert len(controller.history) == 1


def test_cancelled_refinement_creates_no_history_entry(controller):
    entered, release = threading.Event(), threading.Event()

    def refine(*_, **__):
        entered.set()
        assert release.wait(2)
        return "A cancelled result."

    controller.refiner.refine.side_effect = refine
    controller.toggle()
    controller.toggle()
    assert entered.wait(2)
    controller.cancel()
    release.set()
    assert controller.wait_for_idle()
    assert controller.history == ()
    controller.clipboard.write_text.assert_not_called()


def test_cancel_or_close_inside_clipboard_adapter_does_not_commit_history(controller):
    controller.clipboard.write_text.side_effect = lambda _: controller.cancel()
    complete(controller)
    assert controller.history == ()
    assert controller.state is AppState.READY


def test_manual_recopy_only_writes_clipboard_and_does_not_notify_or_play_sounds(controller):
    complete(controller)
    entry = controller.history[0]
    snapshot = controller.history
    state, message, timings = controller.state, controller.message, controller.timings
    sounds, statuses, history_snapshots = [], [], []
    controller.set_sound_observer(sounds.append)
    controller.set_observer(lambda *args: statuses.append(args))
    controller.set_history_observer(history_snapshots.append)
    statuses.clear()
    controller.clipboard.write_text.reset_mock()
    adapters = controller.recorder, controller.transcriber, controller.refiner
    calls = [adapter.mock_calls.copy() for adapter in adapters]
    assert controller.copy_history(entry.id)
    controller.clipboard.write_text.assert_called_once_with(entry.text)
    assert controller.history == snapshot
    assert (controller.state, controller.message, controller.timings) == (state, message, timings)
    assert not sounds and not statuses
    assert history_snapshots == [snapshot]
    assert [adapter.mock_calls for adapter in adapters] == calls


def test_recopy_failure_is_safe_and_retains_entry_state_and_clipboard_retry(controller, caplog):
    complete(controller)
    snapshot, state, message = controller.history, controller.state, controller.message
    controller.clipboard.write_text.side_effect = RuntimeError("Private text should never leak")
    with pytest.raises(ConfigurationError, match="could not be copied") as error:
        controller.copy_history(snapshot[0].id)
    assert "Private" not in str(error.value) and "Private" not in caplog.text
    assert (controller.history, controller.state, controller.message) == (snapshot, state, message)
    controller.clipboard.write_text.side_effect = None
    assert controller.copy_history(snapshot[0].id)


@pytest.mark.parametrize("entry_id", [0, -1, 999, "1", True, None])
def test_stale_or_invalid_entry_id_does_not_modify_clipboard(controller, entry_id):
    complete(controller)
    controller.clipboard.write_text.reset_mock()
    assert not controller.copy_history(entry_id)
    controller.clipboard.write_text.assert_not_called()


def test_history_actions_are_blocked_while_recording(controller):
    complete(controller)
    snapshot = controller.history
    controller.clipboard.write_text.reset_mock()
    controller.toggle()
    try:
        assert controller.is_busy
        assert not controller.copy_history(snapshot[0].id)
        assert not controller.clear_history()
        assert controller.history == snapshot
        controller.clipboard.write_text.assert_not_called()
    finally:
        controller.cancel()
        assert controller.wait_for_idle()


def test_clear_only_forgets_history_and_ids_are_not_reused(controller):
    complete(controller)
    previous_id = controller.history[0].id
    controller.clipboard.write_text.reset_mock()
    assert controller.clear_history() and controller.history == ()
    controller.clipboard.write_text.assert_not_called()
    assert not controller.copy_history(previous_id)
    complete(controller)
    assert controller.history[0].id > previous_id
    assert not controller.copy_history(previous_id)


def test_shutdown_forgets_history_detaches_observer_and_blocks_actions(controller):
    complete(controller)
    entry_id = controller.history[0].id
    observer = Mock()
    controller.set_history_observer(observer)
    observer.reset_mock()
    controller.clipboard.write_text.reset_mock()
    assert controller.shutdown()
    assert controller.history == ()
    assert controller._history_observer is None
    assert not controller.copy_history(entry_id)
    assert not controller.clear_history()
    controller.set_history_observer(observer)
    observer.assert_not_called()
    controller.clipboard.write_text.assert_not_called()
