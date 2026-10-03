"""Bounded local microphone capture; importing this module does not open a device."""

from __future__ import annotations

import importlib
import threading
from typing import Any

import numpy as np

from voice_to_me.config import AudioSettings


class RecordingError(RuntimeError):
    """A capture failed and its audio must not be submitted for transcription."""


class AudioRecorder:
    """Capture mono float32 audio with an explicit start/stop lifecycle.

    The caller controls the maximum-duration timer. The buffer also enforces that
    limit, so a delayed timer can never grow a recording without bounds.
    """

    def __init__(self, settings: AudioSettings):
        if settings.sample_rate <= 0 or settings.max_seconds <= 0:
            raise ValueError("The audio sample rate and maximum duration must be positive.")
        self.settings = settings
        self._max_frames = max(1, int(settings.sample_rate * settings.max_seconds))
        self._lifecycle_lock = threading.RLock()
        self._buffer_lock = threading.Lock()
        self._stream: Any | None = None
        self._chunks: list[np.ndarray] = []
        self._frames = 0
        self._accepting = False
        self._capture_error: RecordingError | None = None

    def _callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        del frames, time_info
        with self._buffer_lock:
            if not self._accepting or self._capture_error is not None:
                return
            if status:
                self._capture_error = RecordingError(
                    f"Microphone capture failed ({status}). Record your message again."
                )
                return
            remaining = self._max_frames - self._frames
            if remaining <= 0:
                return
            try:
                samples = np.asarray(indata, dtype=np.float32)
                if samples.ndim != 2 or samples.shape[1] != 1:
                    raise ValueError("Audio input must be mono.")
                chunk = samples[:remaining, 0].copy()
                if chunk.size:
                    self._chunks.append(chunk)
                    self._frames += chunk.size
            except Exception as exc:  # noqa: BLE001 - exceptions cannot leave the audio callback
                self._capture_error = RecordingError("Could not capture audio.")
                self._capture_error.__cause__ = exc

    @staticmethod
    def _release_stream(stream: Any) -> Exception | None:
        error: Exception | None = None
        try:
            stream.stop()
        except Exception as exc:  # noqa: BLE001 - closing the device remains mandatory
            error = exc
        finally:
            try:
                stream.close()
            except Exception as exc:  # noqa: BLE001 - preserve the earlier failure after cleanup
                error = error or exc
        return error

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._stream is not None:
                raise RecordingError("A recording is already in progress.")
            with self._buffer_lock:
                self._chunks = []
                self._frames = 0
                self._capture_error = None
                self._accepting = True
            stream = None
            try:
                sounddevice = importlib.import_module("sounddevice")
                stream = sounddevice.InputStream(
                    samplerate=self.settings.sample_rate,
                    channels=1,
                    dtype="float32",
                    device=self.settings.device,
                    callback=self._callback,
                )
                self._stream = stream
                stream.start()
            except BaseException as exc:
                self._stream = None
                with self._buffer_lock:
                    self._accepting = False
                    self._chunks = []
                    self._frames = 0
                if stream is not None:
                    self._release_stream(stream)
                if not isinstance(exc, Exception):
                    raise
                raise RecordingError(
                    "Could not open the microphone. Check the device and the Windows "
                    "microphone permission."
                ) from exc

    def stop(self) -> np.ndarray:
        with self._lifecycle_lock:
            stream = self._stream
            if stream is None:
                raise RecordingError("There is no recording in progress.")
            self._stream = None
            with self._buffer_lock:
                self._accepting = False
            release_error = self._release_stream(stream)
            with self._buffer_lock:
                chunks = self._chunks
                capture_error = self._capture_error
                self._chunks = []
                self._frames = 0
                self._capture_error = None
            if capture_error is not None:
                raise capture_error
            if release_error is not None:
                raise RecordingError("Could not stop microphone capture.") from (
                    release_error
                )
            if not chunks:
                return np.empty(0, dtype=np.float32)
            return np.concatenate(chunks).astype(np.float32, copy=False)

    def cancel(self) -> None:
        """Discard capture and always attempt device closure, including after failures."""
        with self._lifecycle_lock:
            stream = self._stream
            self._stream = None
            with self._buffer_lock:
                self._accepting = False
                self._chunks = []
                self._frames = 0
                self._capture_error = None
            if stream is not None:
                self._release_stream(stream)
