"""Register GPU DLLs from this Python environment without external installation paths."""

from __future__ import annotations

import os
import sysconfig
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_DLL_HANDLES: list[Any] = []
_REGISTERED: set[str] = set()


def _library_directories() -> tuple[Path, ...]:
    roots = {Path(sysconfig.get_path(name)) for name in ("purelib", "platlib")}
    return tuple(sorted({
        path.resolve() for root in roots for path in (root / "nvidia").glob("*/bin")
        if path.is_dir()
    }))


def configure_cuda_dlls() -> tuple[str, ...]:
    """Keep add_dll_directory handles alive and support native LoadLibrary lookups."""
    if os.name != "nt":
        return ()
    with _LOCK:
        directories = _library_directories()
        for directory in directories:
            identity = str(directory).casefold()
            if identity not in _REGISTERED:
                _DLL_HANDLES.append(os.add_dll_directory(str(directory)))
                _REGISTERED.add(identity)
        current = os.environ.get("PATH", "")
        existing = {part.casefold() for part in current.split(os.pathsep)}
        new = [str(directory) for directory in directories if str(directory).casefold() not in existing]
        if new:
            os.environ["PATH"] = os.pathsep.join([*new, current])
        return tuple(str(directory) for directory in directories)


def cuda_runtime_ready() -> bool:
    """Check required Windows CUDA 12/cuDNN 9 filenames without loading a model."""
    if os.name != "nt":
        # Unix dynamic loader paths cannot be inferred from wheel directories.
        # The actual native constructor/decode remains the authoritative check.
        return True
    roots = {Path(sysconfig.get_path(name)) for name in ("purelib", "platlib")}
    directories = [*_library_directories(), *(root / "ctranslate2" for root in roots)]
    directories.extend(Path(part) for part in os.environ.get("PATH", "").split(os.pathsep) if part)
    required = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll")
    return all(any((directory / name).is_file() for directory in directories) for name in required)
