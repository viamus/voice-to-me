"""Short, local sound cues; playback never blocks recording or processing."""

from __future__ import annotations

import importlib
import logging
import threading
from pathlib import Path
from typing import Any

ASSETS = Path(__file__).parent / "assets"


def _load_backend() -> Any:
    try:
        return importlib.import_module("winsound")
    except (ImportError, OSError):
        return None


class SoundCues:
    """Play distinct recording/start, recording/stop, and clipboard-ready chimes."""

    def __init__(self) -> None:
        self._backend = _load_backend()
        self._closed = False
        self._lock = threading.Lock()

    def recording_started(self) -> None:
        self._play("recording-start.wav")

    def recording_stopped(self) -> None:
        self._play("recording-stop.wav")

    def ready(self) -> None:
        self._play("result-ready.wav")

    def _play(self, filename: str) -> None:
        with self._lock:
            if self._closed or self._backend is None:
                return
            path = ASSETS / filename
            try:
                flags = (self._backend.SND_FILENAME | self._backend.SND_ASYNC
                         | self._backend.SND_NODEFAULT)
                self._backend.PlaySound(str(path), flags)
            except (RuntimeError, OSError):
                logging.getLogger(__name__).debug("Sound cue unavailable: %s", filename)

    def close(self) -> None:
        """Stop this process's asynchronous cue and suppress all later playback."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._backend is not None:
                try:
                    self._backend.PlaySound(None, 0)
                except (RuntimeError, OSError):
                    logging.getLogger(__name__).debug("Sound cleanup unavailable")
