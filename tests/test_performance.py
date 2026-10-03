from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from voice_to_me import cuda_runtime, performance
from voice_to_me.config import WhisperSettings
from voice_to_me.performance import BackendDetectionError


@pytest.fixture
def ct2(monkeypatch):
    module = SimpleNamespace(
        get_cuda_device_count=Mock(return_value=1),
        get_supported_compute_types=Mock(return_value={"float32", "float16", "int8_float16"}),
    )
    monkeypatch.setattr(performance, "_load_ctranslate2", lambda: module)
    monkeypatch.setattr(performance, "cuda_runtime_ready", lambda: True)
    return module


@pytest.mark.parametrize("name,model", [
    ("fast", "small"), ("balanced", "turbo"), ("quality", "large-v3"),
    ("Fast", "small"), ("Balanced", "turbo"), ("Best quality", "large-v3"),
])
def test_presets_are_simple_local_and_clear_old_model_directory(name, model):
    current = WhisperSettings(model="custom", model_directory="old-large-folder", language="pt")
    selected = performance.profile_to_settings(name, current)
    assert selected.model == model
    assert selected.model_directory == ""
    assert selected.language == "pt"
    assert selected.device == "auto"
    assert selected.compute_type == "auto"
    assert selected.beam_size == 1
    assert selected.local_files_only


def test_same_model_keeps_its_existing_cache_directory():
    current = WhisperSettings(model="large-v3", model_directory="cached-large-folder")
    assert performance.profile_to_settings("quality", current).model_directory == "cached-large-folder"
    current = replace(current, model="large-v3-turbo", model_directory="cached-turbo-folder")
    assert performance.profile_to_settings("balanced", current).model_directory == "cached-turbo-folder"


@pytest.mark.parametrize("model,profile", [
    ("small", "fast"), ("turbo", "balanced"), ("large-v3-turbo", "balanced"),
    ("large-v3", "quality"), ("custom-local-model", "custom"),
])
def test_current_profile_query(model, profile):
    assert performance.profile_name(WhisperSettings(model=model)) == profile


def test_invalid_profile_is_actionable():
    with pytest.raises(ValueError, match="Fast, Balanced, or Best quality"):
        performance.profile_to_settings("unknown", WhisperSettings())


def test_profile_labels_remain_stable_and_in_usable_order():
    assert tuple(performance.PROFILE_LABELS.items()) == (
        ("fast", "Fast"), ("balanced", "Balanced"), ("quality", "Best quality"),
    )


def test_detected_cuda_prefers_memory_efficient_int8_float16(ct2):
    backend = performance.detect_backend()
    assert backend.device == "cuda"
    assert backend.compute_type == "int8_float16"
    assert backend.cuda_available and backend.runtime_ready
    ct2.get_supported_compute_types.assert_called_once_with("cuda")


def test_cuda_uses_float16_when_int8_float16_is_unavailable(ct2):
    ct2.get_supported_compute_types.return_value = {"float32", "float16"}
    assert performance.detect_backend().compute_type == "float16"


def test_old_cuda_float32_configuration_is_optimized(ct2):
    selected = performance.resolve_backend(WhisperSettings(device="cuda", compute_type="float32"))
    assert selected.device == "cuda"
    assert selected.compute_type == "int8_float16"


def test_supported_explicit_cuda_type_is_preserved(ct2):
    assert performance.resolve_backend(
        WhisperSettings(device="cuda", compute_type="float16")
    ).compute_type == "float16"


def test_unsupported_cuda_type_is_an_error_instead_of_a_hidden_fallback(ct2):
    with pytest.raises(BackendDetectionError, match="not supported"):
        performance.resolve_backend(WhisperSettings(device="cuda", compute_type="int16"))


def test_no_cuda_allows_truthful_auto_cpu_fallback(ct2):
    ct2.get_cuda_device_count.return_value = 0
    backend = performance.resolve_backend(WhisperSettings())
    assert (backend.device, backend.compute_type, backend.cuda_available) == ("cpu", "int8", False)
    assert backend.description == "CPU · int8"
    ct2.get_supported_compute_types.assert_not_called()


def test_explicit_cuda_without_a_cuda_device_fails(ct2):
    ct2.get_cuda_device_count.return_value = 0
    with pytest.raises(BackendDetectionError, match="no CUDA GPU"):
        performance.resolve_backend(WhisperSettings(device="cuda"))


def test_missing_dlls_are_distinguished_from_gpu_hardware_detection(ct2, monkeypatch):
    monkeypatch.setattr(performance, "cuda_runtime_ready", lambda: False)
    detected = performance.detect_backend()
    assert detected.cuda_available
    assert detected.runtime_ready is False
    assert "install GPU runtime support" in detected.description
    with pytest.raises(BackendDetectionError, match="libraries are missing"):
        performance.resolve_backend(WhisperSettings())


def test_cuda_capability_failure_never_claims_cpu_fallback(ct2):
    ct2.get_supported_compute_types.side_effect = RuntimeError("private native detail")
    with pytest.raises(BackendDetectionError, match="CUDA GPU was detected") as error:
        performance.resolve_backend(WhisperSettings())
    assert "private native detail" not in str(error.value)


def test_backend_status_is_a_safe_readonly_label_even_when_detection_fails(ct2):
    ct2.get_cuda_device_count.side_effect = OSError("private installation path")
    text = performance.backend_status()
    assert "Repair the local speech dependencies" in text
    assert "private installation path" not in text


def test_explicit_cpu_does_not_probe_cuda_or_claim_a_gpu(ct2):
    backend = performance.resolve_backend(WhisperSettings(device="cpu", compute_type="auto"))
    assert backend.description == "CPU · int8"
    ct2.get_cuda_device_count.assert_not_called()


def test_project_dll_directories_are_registered_before_importing_ctranslate2(monkeypatch):
    calls = []
    monkeypatch.setattr(performance, "configure_cuda_dlls", lambda: calls.append("dlls"))
    monkeypatch.setattr(
        performance.importlib, "import_module", lambda name: calls.append(name) or SimpleNamespace(),
    )
    performance._load_ctranslate2()
    assert calls == ["dlls", "ctranslate2"]


@pytest.mark.skipif(os.name != "nt", reason="Windows DLL directory registration")
def test_project_gpu_dll_registration_keeps_handles_alive_and_is_idempotent(tmp_path, monkeypatch):
    directories = [tmp_path / "nvidia/cublas/bin", tmp_path / "nvidia/cudnn/bin"]
    for directory in directories:
        directory.mkdir(parents=True)
    handles = []

    def add_directory(path):
        handle = Mock(path=path)
        handles.append(handle)
        return handle

    register = Mock(side_effect=add_directory)
    monkeypatch.setattr(cuda_runtime.sysconfig, "get_path", lambda _: str(tmp_path))
    monkeypatch.setattr(cuda_runtime.os, "add_dll_directory", register)
    monkeypatch.setattr(cuda_runtime, "_DLL_HANDLES", [])
    monkeypatch.setattr(cuda_runtime, "_REGISTERED", set())
    monkeypatch.setenv("PATH", "original-path")
    first = cuda_runtime.configure_cuda_dlls()
    second = cuda_runtime.configure_cuda_dlls()
    assert first == second == tuple(str(directory.resolve()) for directory in directories)
    assert register.call_count == 2
    assert cuda_runtime._DLL_HANDLES == handles
    assert all(not handle.close.called for handle in handles)
    assert os.environ["PATH"].split(os.pathsep) == [*first, "original-path"]
    assert not cuda_runtime.cuda_runtime_ready()
    for name in ("cublas64_12.dll", "cublasLt64_12.dll"):
        (directories[0] / name).write_bytes(b"placeholder")
    (directories[1] / "cudnn64_9.dll").write_bytes(b"placeholder")
    assert cuda_runtime.cuda_runtime_ready()
