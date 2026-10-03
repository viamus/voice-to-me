from __future__ import annotations

import struct
import wave
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from voice_to_me import sounds


@pytest.fixture
def backend(monkeypatch):
    mocked = SimpleNamespace(SND_FILENAME=0x20000, SND_ASYNC=1, SND_NODEFAULT=2,
                             PlaySound=Mock())
    monkeypatch.setattr(sounds, "_load_backend", lambda: mocked)
    return mocked


def test_three_distinct_file_cues_are_asynchronous_without_default_fallback(backend):
    cues = sounds.SoundCues()
    cues.recording_started()
    cues.recording_stopped()
    cues.ready()

    expected_flags = backend.SND_FILENAME | backend.SND_ASYNC | backend.SND_NODEFAULT
    assert backend.PlaySound.call_args_list == [
        call(str(sounds.ASSETS / "recording-start.wav"), expected_flags),
        call(str(sounds.ASSETS / "recording-stop.wav"), expected_flags),
        call(str(sounds.ASSETS / "result-ready.wav"), expected_flags),
    ]


def test_close_stops_playback_once_and_suppresses_late_events(backend):
    cues = sounds.SoundCues()
    cues.ready()
    cues.close()
    cues.close()
    cues.recording_started()
    cues.recording_stopped()
    cues.ready()

    assert backend.PlaySound.call_count == 2
    assert backend.PlaySound.call_args == call(None, 0)


@pytest.mark.parametrize("error", [RuntimeError("Audio unavailable"), OSError("No output")])
def test_audio_failure_does_not_interrupt_lifecycle(backend, error):
    backend.PlaySound.side_effect = error
    cues = sounds.SoundCues()
    cues.recording_started()
    cues.recording_stopped()
    cues.ready()
    cues.close()
    cues.ready()
    assert backend.PlaySound.call_count == 4


def test_unavailable_backend_is_a_noop(monkeypatch):
    monkeypatch.setattr(sounds, "_load_backend", lambda: None)
    cues = sounds.SoundCues()
    cues.recording_started()
    cues.recording_stopped()
    cues.ready()
    cues.close()


@pytest.mark.parametrize("error", [ImportError("Non-Windows"), OSError("Unavailable")])
def test_backend_import_failure_is_safe(monkeypatch, error):
    importer = Mock(side_effect=error)
    monkeypatch.setattr(sounds.importlib, "import_module", importer)
    assert sounds._load_backend() is None
    importer.assert_called_once_with("winsound")


def test_packaged_chimes_are_short_gentle_pcm_with_silent_edges():
    payloads = []
    for filename in ("recording-start.wav", "recording-stop.wav", "result-ready.wav"):
        with wave.open(str(sounds.ASSETS / filename), "rb") as cue:
            assert cue.getnchannels() == 1
            assert cue.getsampwidth() == 2
            assert cue.getframerate() == 44100
            assert cue.getcomptype() == "NONE"
            assert 0.1 <= cue.getnframes() / cue.getframerate() <= 0.35
            payload = cue.readframes(cue.getnframes())
        samples = struct.unpack(f"<{len(payload) // 2}h", payload)
        assert samples[0] == samples[-1] == 0
        assert 1000 < max(abs(sample) for sample in samples) < 7000
        payloads.append(payload)
    assert len(set(payloads)) == 3
