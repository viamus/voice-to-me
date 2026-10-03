"""Local Whisper transcription with a lazy, resident model."""

from __future__ import annotations

import importlib
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from voice_to_me.config import WhisperSettings
from voice_to_me.cuda_runtime import configure_cuda_dlls
from voice_to_me.performance import BackendDetectionError, resolve_backend

_LOG = logging.getLogger(__name__)
ProgressCallback = Callable[[str, str], None]


class TranscriptionError(RuntimeError):
    """Local transcription could not produce a usable result."""


class TranscriptionCancelled(TranscriptionError):
    """The caller cancelled the current transcription."""


def _progress(callback: ProgressCallback | None, stage: str, message: str) -> None:
    if callback is not None:
        try:
            callback(stage, message)
        except Exception as error:
            # UI failures must not discard speech or leak message/exception contents.
            _LOG.warning("Speech progress callback failed (%s).", type(error).__name__)


def _failure(error: Exception, *, loading: bool) -> TranscriptionError:
    if isinstance(error, BackendDetectionError):
        return TranscriptionError(str(error))
    values = []
    current: BaseException | None = error
    for _ in range(5):
        if current is None:
            break
        values.append(str(current).lower())
        current = current.__cause__ or current.__context__
    detail = " ".join(values)
    if "out of memory" in detail or "cuda_error_out_of_memory" in detail:
        return TranscriptionError(
            "The GPU has insufficient free memory. Close other GPU apps or choose Fast in Settings."
        )
    if any(value in detail for value in ("cublas", "cudnn", "cuda", ".dll", "dll load")):
        return TranscriptionError(
            "The GPU runtime is missing or incompatible. Install the project's GPU support "
            "and restart Voice to Me. CPU fallback was not used."
        )
    if loading:
        return TranscriptionError(
            f"Could not prepare the local speech model ({type(error).__name__}). "
            "Use Download model in Settings and check the speech preset."
        )
    return TranscriptionError(
        f"Could not transcribe audio locally ({type(error).__name__}). Try recording again."
    )


class WhisperTranscriber:
    def __init__(self, settings: WhisperSettings):
        self.settings = settings
        self._model: Any | None = None
        self._lock = threading.Lock()
        self.actual_device: str | None = None
        self.actual_compute_type: str | None = None

    @property
    def backend_description(self) -> str:
        if self._model is None:
            return "Speech model not prepared yet."
        if self.actual_device is None or self.actual_compute_type is None:
            return "Local model ready (backend not reported)."
        label = "GPU (CUDA)" if self.actual_device == "cuda" else "CPU"
        return f"{label} · {self.actual_compute_type}"

    @staticmethod
    def _check_cancel(cancel: threading.Event | None) -> None:
        if cancel is not None and cancel.is_set():
            raise TranscriptionCancelled("Transcription cancelled.")

    def prepare(
        self, cancel: threading.Event | None = None, progress: ProgressCallback | None = None,
    ) -> None:
        self._check_cancel(cancel)
        with self._lock:
            self._check_cancel(cancel)
            self._load_model(cancel, progress)
            self._check_cancel(cancel)

    def _load_model(
        self, cancel: threading.Event | None = None, progress: ProgressCallback | None = None,
    ) -> Any:
        if self._model is not None:
            return self._model
        self._check_cancel(cancel)
        _progress(progress, "preparing", "Preparing the local speech model...")
        if not self.settings.local_files_only:
            raise TranscriptionError("Download model is an explicit Settings action, not a recording step.")
        model_name = self.settings.model
        if self.settings.model_directory:
            model_path = Path(self.settings.model_directory).expanduser()
            if not model_path.is_dir():
                raise TranscriptionError(
                    "The local speech model folder does not exist. Use Download model in Settings."
                )
            model_name = str(model_path.resolve())
        started = time.perf_counter()
        try:
            backend = resolve_backend(self.settings)
            configure_cuda_dlls()
            self._check_cancel(cancel)
            faster_whisper = importlib.import_module("faster_whisper")
            _LOG.info(
                "Preparing speech model: device=%s compute=%s.", backend.device, backend.compute_type,
            )
            model = faster_whisper.WhisperModel(
                model_name,
                device=backend.device,
                compute_type=backend.compute_type,
                local_files_only=True,
            )
            engine = getattr(model, "model", None)
            device = getattr(engine, "device", None)
            compute = getattr(engine, "compute_type", None)
            self.actual_device = device if isinstance(device, str) and device in {"cpu", "cuda"} else None
            self.actual_compute_type = compute if isinstance(compute, str) else None
            if backend.device == "cuda" and self.actual_device == "cpu":
                raise TranscriptionError(
                    "Whisper loaded on CPU after CUDA was selected. Check the GPU runtime; "
                    "CPU fallback was not accepted."
                )
            self._model = model
            _LOG.info(
                "Speech model prepared in %.2fs: actual_device=%s actual_compute=%s.",
                time.perf_counter() - started, self.actual_device or "not reported",
                self.actual_compute_type or "not reported",
            )
            self._check_cancel(cancel)
        except (TranscriptionError, TranscriptionCancelled):
            raise
        except Exception as exc:
            _LOG.warning(
                "Speech model preparation failed after %.2fs (%s).",
                time.perf_counter() - started, type(exc).__name__,
            )
            raise _failure(exc, loading=True) from None
        return self._model

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        cancel: threading.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> str:
        self._check_cancel(cancel)
        if sample_rate != 16000:
            raise TranscriptionError("Local transcription requires mono audio at 16000 Hz.")
        samples = np.asarray(audio, dtype=np.float32)
        if samples.ndim != 1:
            raise TranscriptionError("Local transcription requires a mono audio vector.")
        if not np.all(np.isfinite(samples)):
            raise TranscriptionError("The audio contains invalid samples. Record it again.")
        if samples.size == 0:
            return ""
        with self._lock:
            self._check_cancel(cancel)
            model = self._load_model(cancel, progress)
            self._check_cancel(cancel)
            _progress(progress, "transcribing", f"Transcribing locally · {self.backend_description}")
            started = time.perf_counter()
            try:
                segments, _info = model.transcribe(
                    samples,
                    language=self.settings.language or None,
                    beam_size=self.settings.beam_size,
                    temperature=0,
                    vad_filter=True,
                    condition_on_previous_text=False,
                )
                text: list[str] = []
                try:
                    for segment in segments:
                        self._check_cancel(cancel)
                        value = segment.text.strip()
                        if value:
                            text.append(value)
                finally:
                    # Cancellation must also release a suspended decoding generator.
                    close = getattr(segments, "close", None)
                    if close is not None:
                        close()
                self._check_cancel(cancel)
                _LOG.info(
                    "Local transcription finished in %.2fs for %.2fs of audio: device=%s compute=%s.",
                    time.perf_counter() - started, samples.size / sample_rate,
                    self.actual_device or "not reported", self.actual_compute_type or "not reported",
                )
                return " ".join(text).strip()
            except TranscriptionCancelled:
                raise
            except Exception as exc:
                _LOG.warning(
                    "Local transcription failed after %.2fs (%s).",
                    time.perf_counter() - started, type(exc).__name__,
                )
                raise _failure(exc, loading=False) from None
