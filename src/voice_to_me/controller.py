"""Máquina de estados e pipeline; nenhuma dependência da interface ou de um hook."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from enum import StrEnum

from .config import AppSettings, ConfigurationError, read_profile


class AppState(StrEnum):
    READY = "ready"
    PREPARING = "preparing"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    REFINING = "refining"
    COPIED = "copied"
    ERROR = "error"


class AppController:
    def __init__(self, settings: AppSettings, recorder, transcriber, refiner, clipboard):
        self.settings = settings
        self.recorder, self.transcriber = recorder, transcriber
        self.refiner, self.clipboard = refiner, clipboard
        self.state = AppState.READY
        self.message = "Ready to listen. Press once to record and again to finish."
        self._lock = threading.RLock()
        self._observer: Callable[[AppState, str], None] = lambda *_: None
        self._sound_observer: Callable[[str], None] | None = None
        self._worker: threading.Thread | None = None
        self._heartbeat: threading.Thread | None = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_interval = 1.0
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._closed = False
        self._input_suspended = False
        self._audio = None
        self._transcript = ""
        self._model_prepared = False
        self._task_starting = False
        self._operation_started = 0.0
        self._step_started = 0.0
        self._step_name = ""
        self._status_base = self.message
        self._timings: dict[str, float] = {}

    def set_observer(self, observer: Callable[[AppState, str], None]) -> None:
        with self._lock:
            if self._closed:
                return
            self._observer = observer
            observer(self.state, self.message)

    def set_sound_observer(self, observer: Callable[[str], None] | None) -> None:
        """Observe completed recording boundaries without replaying the current state."""
        with self._lock:
            if not self._closed:
                self._sound_observer = observer

    def _emit_sound(self, cue: str) -> None:
        with self._lock:
            if self._closed or (cue == "ready" and self._cancel.is_set()):
                return
            if self._sound_observer is not None:
                try:
                    self._sound_observer(cue)
                except Exception as exc:
                    # Sound playback is optional; do not expose callback contents.
                    logging.getLogger(__name__).warning("Sound observer failed (%s)", type(exc).__name__)

    def _publish(self, state: AppState, message: str) -> None:
        with self._lock:
            if self._closed:
                return
            self.state, self.message = state, message
            self._observer(state, message)

    def _finish_step(self) -> None:
        with self._lock:
            if self._step_name:
                elapsed = max(0.0, time.monotonic() - self._step_started)
                self._timings[self._step_name] = self._timings.get(self._step_name, 0) + elapsed
                if not self._closed:
                    logging.getLogger(__name__).info("Step %s completed in %.2f s",
                                                     self._step_name, elapsed)
                self._step_name = ""

    def _begin_step(self, name: str, state: AppState, message: str) -> None:
        with self._lock:
            if self._cancelled():
                return
            if name != self._step_name:
                self._finish_step()
                self._step_name, self._step_started = name, time.monotonic()
            self._status_base = message
            self._publish(state, message)

    def _progress(self, stage: str, message: str) -> None:
        with self._lock:
            if self._cancelled():
                return
            if stage == "preparing":
                self._begin_step("preparation", AppState.PREPARING, message)
            elif stage == "transcribing":
                self._model_prepared = True
                self._begin_step("transcription", AppState.TRANSCRIBING, message)

    def _heartbeat_loop(self, stop: threading.Event) -> None:
        while not stop.wait(self._heartbeat_interval):
            with self._lock:
                if self._cancelled():
                    return
                if self._step_name and self.state in (
                    AppState.PREPARING, AppState.TRANSCRIBING, AppState.REFINING,
                ):
                    step = max(0.0, time.monotonic() - self._step_started)
                    total = max(0.0, time.monotonic() - self._operation_started)
                    self._publish(self.state,
                                  f"{self._status_base} {step:.0f}s in this step · {total:.0f}s total.")

    def _new_operation(self) -> None:
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._heartbeat_stop = threading.Event()
        self._operation_started = time.monotonic()
        self._timings = {}
        self._step_name = ""
        self._heartbeat = threading.Thread(target=self._heartbeat_loop,
                                           args=(self._heartbeat_stop,),
                                           name="voice-to-me-progress", daemon=True)
        self._heartbeat.start()

    def prepare(self) -> bool:
        """Warm a local model in the background before enabling the first dictation."""
        with self._lock:
            if self._closed or self.is_busy or self._model_prepared:
                return False
            self._task_starting = True
            try:
                self._new_operation()
                self._begin_step("preparation", AppState.PREPARING,
                                 "Preparing local Whisper. Recording will be available when ready.")
                self._worker = threading.Thread(target=self._run_prepare,
                                                name="voice-to-me-model-preparation", daemon=True)
                self._worker.start()
            except RuntimeError:
                self._heartbeat_stop.set()
                self._finish_step()
                self._publish(AppState.ERROR, "Model preparation could not start. Restart the app.")
                return False
            finally:
                self._task_starting = False
            return True

    def _run_prepare(self) -> None:
        try:
            self.transcriber.prepare(cancel=self._cancel, progress=self._progress)
            with self._lock:
                if self._cancelled():
                    return
                self._model_prepared = True
                self._finish_step()
                backend = getattr(self.transcriber, "backend_description", "")
                if not isinstance(backend, str) or not backend:
                    backend = f"{self.settings.whisper.device}/{self.settings.whisper.compute_type}"
                elapsed = max(0.0, time.monotonic() - self._operation_started)
                logging.getLogger(__name__).info("Whisper ready: model=%s backend=%s",
                                                 self.settings.whisper.model, backend)
                self._publish(AppState.READY,
                              f"Local Whisper is ready ({backend}; prepared in {elapsed:.1f}s). "
                              "Press once to record and again to finish.")
        except Exception as exc:
            if not self._cancelled():
                logging.getLogger(__name__).error("Model preparation failed (%s)", type(exc).__name__)
                self._finish_step()
                message = str(exc) if isinstance(exc, RuntimeError) else (
                    "The local model could not be prepared. Review Settings and try again."
                )
                self._publish(AppState.ERROR, message)
        finally:
            self._heartbeat_stop.set()
            self._finish_step()
            if self._cancelled() and not self._closed:
                self._publish(AppState.READY, "Cancelled. Your clipboard was preserved.")

    def toggle(self) -> None:
        with self._lock:
            if self._closed or self._input_suspended:
                return
            if self.state is AppState.RECORDING:
                self._stop.set()
                return
            if self.is_busy:
                return
            self._audio, self._transcript = None, ""
            self._start(record=True)

    def retry(self) -> None:
        with self._lock:
            if self._closed or self._input_suspended or self.state is not AppState.ERROR:
                return
            if self.is_busy:
                return
            if self._audio is None and not self._transcript:
                if self._model_prepared:
                    self._start(record=True)
                else:
                    self.prepare()
            else:
                self._start(record=False)

    def _start(self, record: bool) -> None:
        self._task_starting = True
        try:
            self._new_operation()
            refining = bool(self._transcript) and self.settings.codex.enabled
            state = AppState.RECORDING if record else (
                AppState.REFINING if refining else AppState.TRANSCRIBING
            )
            step = "recording" if record else (
                "refinement" if refining else ("clipboard" if self._transcript else "transcription")
            )
            self._begin_step(step, state, "Listening. Press again to finish." if record
                             else "Retrying with the content kept in memory.")
            self._worker = threading.Thread(target=self._run, args=(record,),
                                            name="voice-to-me-pipeline", daemon=True)
            self._worker.start()
        except RuntimeError:
            self._heartbeat_stop.set()
            self._finish_step()
            self._publish(AppState.ERROR, "The dictation worker could not start. Restart the app.")
        finally:
            self._task_starting = False

    def cancel(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._worker and self._worker.is_alive():
                self._cancel.set()
                self._stop.set()
                self._heartbeat_stop.set()
                self._publish(self.state, "Cancelling. Waiting for the current step to finish.")
            else:
                self._audio, self._transcript = None, ""
                self._publish(AppState.READY, "Cancelled. Ready for a new recording.")

    def _cancelled(self) -> bool:
        return self._cancel.is_set() or self._closed

    def _run(self, record: bool) -> None:
        capture_started = False
        try:
            if record:
                if self._cancelled():
                    return
                self.recorder.start()
                capture_started = True
                with self._lock:
                    if self._cancelled():
                        return
                    self._emit_sound("recording_started")
                self._stop.wait(self.settings.audio.max_seconds)
                if self._cancelled():
                    return
                audio = self.recorder.stop()
                capture_started = False
                self._emit_sound("recording_stopped")
                self._finish_step()
                if len(audio) / self.settings.audio.sample_rate < self.settings.audio.minimum_seconds:
                    raise ConfigurationError("Recording too short. Please record a longer message.")
                self._audio = audio
            if self._cancelled():
                return
            if not self._transcript:
                if self._model_prepared:
                    self._begin_step("transcription", AppState.TRANSCRIBING,
                                     "Transcribing locally on your computer…")
                else:
                    self._begin_step("preparation", AppState.PREPARING,
                                     "Preparing the local model before transcription…")
                text = self.transcriber.transcribe(self._audio,
                                                  sample_rate=self.settings.audio.sample_rate,
                                                  cancel=self._cancel, progress=self._progress)
                if self._cancelled():
                    return
                self._model_prepared = True
                self._finish_step()
                if not isinstance(text, str) or not text.strip():
                    raise ConfigurationError("No speech detected. Try a new recording.")
                self._transcript = text.strip()
            if self.settings.codex.enabled:
                profile = read_profile(self.settings.profile_path)
                elapsed = self._timings.get("transcription")
                prefix = f"Transcribed in {elapsed:.1f}s. " if elapsed is not None else ""
                self._begin_step("refinement", AppState.REFINING,
                                 prefix + "Refining your message with Codex…")
                final = self.refiner.refine(self._transcript, profile, cancel=self._cancel)
            else:
                final = self._transcript
            if not isinstance(final, str) or not final.strip() or "\x00" in final:
                source = "Refinement" if self.settings.codex.enabled else "Transcription"
                raise ConfigurationError(f"{source} did not produce valid text. Please retry.")
            with self._lock:
                if self._cancelled():
                    return
                self._finish_step()
                self._step_name, self._step_started = "clipboard", time.monotonic()
                self.clipboard.write_text(final.strip())
                self._finish_step()
                self._audio, self._transcript = None, ""
                elapsed = max(0.0, time.monotonic() - self._operation_started)
                logging.getLogger(__name__).info("Dictation completed in %.2f s", elapsed)
                self._publish(AppState.COPIED,
                              f"Text copied. Total {elapsed:.1f}s. Paste in Teams or anywhere with Ctrl+V.")
                self._emit_sound("ready")
        except Exception as exc:
            if not self._cancelled():
                # Never log audio, the prompt, transcript, output or subprocess stderr.
                logging.getLogger(__name__).error("Pipeline failed (%s)", type(exc).__name__)
                message = str(exc) if isinstance(exc, RuntimeError) else (
                    "The operation failed. Review Settings and try again."
                )
                self._publish(AppState.ERROR, message)
        finally:
            self._heartbeat_stop.set()
            self._finish_step()
            try:
                if record:
                    self.recorder.cancel()
                    if capture_started:
                        capture_started = False
                        self._emit_sound("recording_stopped")
            except Exception:
                if not self._closed:
                    logging.getLogger(__name__).error("Recorder cleanup failed")
            if self._cancelled():
                self._audio, self._transcript = None, ""
                if not self._closed:
                    self._publish(AppState.READY, "Cancelled. Your clipboard was preserved.")

    @property
    def is_busy(self) -> bool:
        with self._lock:
            return self._task_starting or bool(self._worker and self._worker.is_alive())

    @property
    def timings(self) -> dict[str, float]:
        with self._lock:
            return dict(self._timings)

    @property
    def has_active_worker(self) -> bool:
        """Report remaining native work after bounded shutdown, without waiting."""
        with self._lock:
            return bool(self._worker and self._worker.is_alive())

    def suspend_input(self) -> None:
        with self._lock:
            if self.is_busy:
                raise ConfigurationError("Finish or cancel the current recording before changing settings.")
            self._input_suspended = True

    def resume_input(self) -> None:
        with self._lock:
            if not self._closed:
                self._input_suspended = False

    def apply_settings(self, settings: AppSettings, recorder, transcriber, refiner) -> None:
        with self._lock:
            if self._closed:
                raise ConfigurationError("The app is closing. Settings cannot be changed.")
            if self.is_busy:
                raise ConfigurationError("Finish or cancel the current recording before saving settings.")
            if transcriber is not self.transcriber:
                self._model_prepared = False
            self.settings = settings
            self.recorder, self.transcriber, self.refiner = recorder, transcriber, refiner

    def shutdown(self, timeout_seconds: float = 0.5) -> bool:
        """Detach callbacks immediately and wait a bounded time for daemon workers.

        Native Whisper calls cannot stop mid-call. Daemon workers allow normal
        application-process exit after the GUI and native input hooks are closed.
        False means native work was still finishing at the shutdown deadline.
        """
        with self._lock:
            if self._closed:
                return not bool(self._worker and self._worker.is_alive())
            self._closed = True
            self._observer = lambda *_: None
            self._sound_observer = None
            self._cancel.set()
            self._stop.set()
            self._heartbeat_stop.set()
            self._audio, self._transcript = None, ""
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        for worker in (self._heartbeat, self._worker):
            if worker and worker is not threading.current_thread():
                try:
                    worker.join(timeout=max(0.0, deadline - time.monotonic()))
                except RuntimeError:
                    pass  # Thread.start() might have failed before creating a thread.
        return not bool(self._worker and self._worker.is_alive())

    def wait_for_idle(self, timeout: float = 5) -> bool:
        """Useful for deterministic adapter tests and orderly shutdown."""
        deadline = time.monotonic() + timeout
        for worker in (self._worker, self._heartbeat):
            if worker and worker is not threading.current_thread():
                try:
                    worker.join(timeout=max(0.0, deadline - time.monotonic()))
                except RuntimeError:
                    pass
        return not self._worker or not self._worker.is_alive()
