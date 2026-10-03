"""Simple speech presets and truthful, local backend selection."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, replace
from typing import Any

from .config import WhisperSettings
from .cuda_runtime import configure_cuda_dlls, cuda_runtime_ready

PROFILE_LABELS = {"fast": "Fast", "balanced": "Balanced", "quality": "Best quality"}
_MODELS = {"fast": "small", "balanced": "turbo", "quality": "large-v3"}
_CUDA_PREFERENCE = ("int8_float16", "float16", "int8", "float32")


class BackendDetectionError(RuntimeError):
    """Local GPU detection or runtime setup needs attention."""


@dataclass(frozen=True)
class BackendInfo:
    device: str
    compute_type: str
    cuda_available: bool
    description: str
    runtime_ready: bool | None = None
    supported_compute_types: frozenset[str] = frozenset()


def _canonical_model(model: str) -> str:
    value = model.strip().lower()
    return "turbo" if value == "large-v3-turbo" else value


def profile_name(settings: WhisperSettings) -> str:
    model = _canonical_model(settings.model)
    return next((name for name, value in _MODELS.items() if value == model), "custom")


def profile_to_settings(name: str, current: WhisperSettings) -> WhisperSettings:
    aliases = {label.lower(): key for key, label in PROFILE_LABELS.items()}
    key = aliases.get(name.strip().lower(), name.strip().lower())
    if key not in _MODELS:
        raise ValueError("Choose Fast, Balanced, or Best quality.")
    model = _MODELS[key]
    directory = current.model_directory if _canonical_model(current.model) == model else ""
    return replace(
        current, model=model, model_directory=directory, device="auto", compute_type="auto",
        beam_size=1, local_files_only=True,
    )


def _load_ctranslate2() -> Any:
    configure_cuda_dlls()
    return importlib.import_module("ctranslate2")


def detect_backend(module: Any | None = None) -> BackendInfo:
    """Detect CUDA capabilities without loading or downloading a speech model."""
    try:
        ct2 = module if module is not None else _load_ctranslate2()
        count = ct2.get_cuda_device_count()
    except Exception:
        raise BackendDetectionError(
            "Could not inspect the speech backend. Repair the local speech dependencies."
        ) from None
    if count <= 0:
        return BackendInfo("cpu", "int8", False, "CPU · int8", runtime_ready=True)
    try:
        supported = frozenset(ct2.get_supported_compute_types("cuda"))
    except Exception:
        raise BackendDetectionError(
            "A CUDA GPU was detected, but its runtime could not initialize. "
            "Install GPU support and restart Voice to Me."
        ) from None
    compute = next((value for value in _CUDA_PREFERENCE if value in supported), None)
    if compute is None:
        raise BackendDetectionError("The CUDA GPU does not support a usable speech compute type.")
    ready = cuda_runtime_ready()
    description = (f"GPU detected (CUDA) · {compute}" if ready
                   else "GPU detected (CUDA); install GPU runtime support.")
    return BackendInfo("cuda", compute, True, description, ready, supported)


def resolve_backend(settings: WhisperSettings) -> BackendInfo:
    """Optimize legacy CUDA float32; never hide a failed CUDA runtime behind CPU."""
    if settings.device == "cpu":
        compute = "int8" if settings.compute_type in {"auto", "default"} else settings.compute_type
        return BackendInfo("cpu", compute, False, f"CPU · {compute}", runtime_ready=True)
    detected = detect_backend()
    if not detected.cuda_available:
        if settings.device == "cuda":
            raise BackendDetectionError(
                "CUDA was selected, but no CUDA GPU is available. Use an automatic speech preset "
                "or install GPU support."
            )
        return detected
    if not detected.runtime_ready:
        raise BackendDetectionError(
            "A CUDA GPU was detected, but CUDA/cuDNN libraries are missing. "
            "Install the project's GPU support and restart Voice to Me."
        )
    requested = settings.compute_type
    compute = detected.compute_type
    if requested not in {"auto", "default", "float32"}:
        if requested not in detected.supported_compute_types:
            raise BackendDetectionError("The selected speech compute type is not supported by CUDA.")
        compute = requested
    return replace(detected, compute_type=compute, description=f"GPU (CUDA) · {compute}")


def backend_status(settings: WhisperSettings | None = None) -> str:
    try:
        if settings is not None and settings.device == "cpu":
            return resolve_backend(settings).description
        return detect_backend().description
    except BackendDetectionError as error:
        return str(error)
