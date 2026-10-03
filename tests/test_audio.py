from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np
import pytest

from voice_to_me import transcriber as transcriber_module
from voice_to_me.config import AudioSettings, WhisperSettings
from voice_to_me.performance import BackendInfo
from voice_to_me.recorder import AudioRecorder, RecordingError
from voice_to_me.transcriber import (
    TranscriptionCancelled,
    TranscriptionError,
    WhisperTranscriber,
)


@pytest.fixture(autouse=True)
def fake_speech_backend(monkeypatch):
    monkeypatch.setattr(
        "voice_to_me.transcriber.resolve_backend",
        lambda settings: BackendInfo("cpu", "int8", False, "CPU · int8", True),
    )
    monkeypatch.setattr("voice_to_me.transcriber.configure_cuda_dlls", lambda: ())


class FakeStream:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.callback = kwargs["callback"]
        self.started = False
        self.stopped = False
        self.closed = False
        self.start_error = False
        self.stop_error = False

    def start(self):
        self.started = True
        if self.start_error:
            raise OSError("device failed")

    def stop(self):
        self.stopped = True
        if self.stop_error:
            raise OSError("stop failed")

    def close(self):
        self.closed = True

    def feed(self, samples, status=None):
        array = np.asarray(samples, dtype=np.float32)
        self.callback(array.reshape(-1, 1), array.size, None, status)


@pytest.fixture
def microphone(monkeypatch):
    streams = []

    def create_stream(**kwargs):
        stream = FakeStream(**kwargs)
        streams.append(stream)
        return stream

    monkeypatch.setattr(
        "voice_to_me.recorder.importlib.import_module",
        lambda _name: SimpleNamespace(InputStream=create_stream),
    )
    return streams


def test_recording_returns_mono_float32_and_closes_device(microphone):
    recorder = AudioRecorder(AudioSettings(device="USB microphone"))
    recorder.start()
    stream = microphone[0]
    stream.feed([0.1, 0.2])
    stream.feed([0.3])

    audio = recorder.stop()

    np.testing.assert_allclose(audio, [0.1, 0.2, 0.3])
    assert audio.dtype == np.float32
    assert audio.ndim == 1
    assert stream.options["device"] == "USB microphone"
    assert stream.options["samplerate"] == 16000
    assert stream.options["channels"] == 1
    assert stream.stopped and stream.closed


def test_recording_buffer_is_bounded_if_timer_is_delayed(microphone):
    recorder = AudioRecorder(AudioSettings(sample_rate=10, max_seconds=1))
    recorder.start()
    stream = microphone[0]
    for _ in range(100):
        stream.feed(np.ones(7))

    assert recorder._frames == 10
    assert sum(chunk.size for chunk in recorder._chunks) == 10
    assert recorder.stop().size == 10


def test_capture_overflow_discards_result_and_closes_device(microphone):
    recorder = AudioRecorder(AudioSettings())
    recorder.start()
    microphone[0].feed([0.2])
    microphone[0].feed([0.3], status="input overflow")

    with pytest.raises(RecordingError, match="capture"):
        recorder.stop()

    assert microphone[0].closed
    assert recorder._chunks == []
    recorder.start()
    microphone[1].feed([0.4])
    np.testing.assert_allclose(recorder.stop(), [0.4])


def test_stream_start_failure_still_closes_device_and_can_retry(monkeypatch):
    streams = []

    def create_stream(**kwargs):
        stream = FakeStream(**kwargs)
        stream.start_error = not streams
        streams.append(stream)
        return stream

    monkeypatch.setattr(
        "voice_to_me.recorder.importlib.import_module",
        lambda _name: SimpleNamespace(InputStream=create_stream),
    )
    recorder = AudioRecorder(AudioSettings())
    with pytest.raises(RecordingError, match="open the microphone"):
        recorder.start()
    assert streams[0].stopped and streams[0].closed

    recorder.start()
    recorder.stop()
    assert streams[1].closed


def test_stream_constructor_failure_clears_recording_state(monkeypatch):
    def fail(**kwargs):
        raise OSError("no microphone")

    monkeypatch.setattr(
        "voice_to_me.recorder.importlib.import_module",
        lambda _name: SimpleNamespace(InputStream=fail),
    )
    recorder = AudioRecorder(AudioSettings())
    with pytest.raises(RecordingError):
        recorder.start()
    assert recorder._stream is None
    assert not recorder._accepting
    recorder.cancel()


def test_stop_failure_always_closes_device(microphone):
    recorder = AudioRecorder(AudioSettings())
    recorder.start()
    microphone[0].stop_error = True
    with pytest.raises(RecordingError, match="stop"):
        recorder.stop()
    assert microphone[0].closed
    assert recorder._stream is None


def test_cancel_discards_audio_and_is_idempotent_even_when_stop_fails(microphone):
    recorder = AudioRecorder(AudioSettings())
    recorder.start()
    microphone[0].feed([0.8])
    microphone[0].stop_error = True
    recorder.cancel()
    recorder.cancel()
    assert microphone[0].closed
    assert recorder._chunks == []
    assert recorder._stream is None


def test_start_twice_does_not_replace_open_stream(microphone):
    recorder = AudioRecorder(AudioSettings())
    recorder.start()
    with pytest.raises(RecordingError, match="already"):
        recorder.start()
    assert len(microphone) == 1
    recorder.cancel()


def test_callbacks_after_stop_are_ignored(microphone):
    recorder = AudioRecorder(AudioSettings())
    recorder.start()
    assert recorder.stop().size == 0
    microphone[0].feed([0.8])
    assert recorder._frames == 0
    assert recorder._chunks == []


def test_invalid_callback_shape_fails_the_recording(microphone):
    recorder = AudioRecorder(AudioSettings())
    recorder.start()
    microphone[0].callback(np.zeros((2, 2)), 2, None, None)
    with pytest.raises(RecordingError, match="capture"):
        recorder.stop()
    assert microphone[0].closed


@pytest.fixture
def whisper(monkeypatch):
    instances = []

    class Model:
        def __init__(self, name, **kwargs):
            self.name = name
            self.options = kwargs
            self.calls = []
            self.model = SimpleNamespace(device=kwargs["device"], compute_type=kwargs["compute_type"])
            self.segments = [SimpleNamespace(text=" Olá. "), SimpleNamespace(text=" Tudo bem? ")]
            instances.append(self)

        def transcribe(self, audio, **kwargs):
            self.calls.append((audio, kwargs))
            return iter(self.segments), SimpleNamespace(language="pt")

    monkeypatch.setattr(
        "voice_to_me.transcriber.importlib.import_module",
        lambda _name: SimpleNamespace(WhisperModel=Model),
    )
    return instances


def test_whisper_is_lazy_and_model_is_reused(whisper):
    transcriber = WhisperTranscriber(WhisperSettings())
    assert whisper == []
    assert transcriber.transcribe(np.ones(16000)) == "Olá. Tudo bem?"
    assert transcriber.transcribe(np.ones(16000)) == "Olá. Tudo bem?"
    assert len(whisper) == 1
    assert whisper[0].options == {
        "device": "cpu", "compute_type": "int8", "local_files_only": True,
    }
    assert whisper[0].calls[0][1] == {
        "language": "pt", "beam_size": 1, "temperature": 0, "vad_filter": True,
        "condition_on_previous_text": False,
    }
    assert whisper[0].calls[0][0].dtype == np.float32


def test_whisper_accepts_explicit_model_directory(whisper, tmp_path):
    transcriber = WhisperTranscriber(WhisperSettings(model_directory=str(tmp_path)))
    transcriber.transcribe(np.ones(16000))
    assert whisper[0].name == str(tmp_path.resolve())


def test_whisper_missing_model_directory_is_actionable_and_does_not_load(whisper, tmp_path):
    transcriber = WhisperTranscriber(
        WhisperSettings(model_directory=str(tmp_path / "missing"))
    )
    with pytest.raises(TranscriptionError, match="Download model"):
        transcriber.transcribe(np.ones(16000))
    assert whisper == []


def test_whisper_loading_failure_does_not_cache_bad_model(monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append(kwargs)
        raise OSError("model missing")

    monkeypatch.setattr(
        "voice_to_me.transcriber.importlib.import_module",
        lambda _name: SimpleNamespace(WhisperModel=fail),
    )
    transcriber = WhisperTranscriber(WhisperSettings())
    for _ in range(2):
        with pytest.raises(TranscriptionError, match="Download model"):
            transcriber.transcribe(np.ones(16000))
    assert len(calls) == 2
    assert all(call["local_files_only"] for call in calls)


@pytest.mark.parametrize(
    ("audio", "rate"),
    [(np.ones(10), 48000), (np.ones((2, 3)), 16000), (np.array([np.nan]), 16000)],
)
def test_invalid_whisper_audio_is_rejected_before_loading(whisper, audio, rate):
    transcriber = WhisperTranscriber(WhisperSettings())
    with pytest.raises(TranscriptionError):
        transcriber.transcribe(audio, sample_rate=rate)
    assert whisper == []


def test_empty_audio_does_not_load_model(whisper):
    assert WhisperTranscriber(WhisperSettings()).transcribe(np.array([])) == ""
    assert whisper == []


def test_cancel_before_transcription_does_not_load_model(whisper):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(TranscriptionCancelled):
        WhisperTranscriber(WhisperSettings()).transcribe(np.ones(16000), cancel=cancel)
    assert whisper == []


def test_cancel_between_segments_releases_decoder(monkeypatch):
    cancel = threading.Event()
    closed = []

    def segments():
        try:
            yield SimpleNamespace(text="first")
            cancel.set()
            yield SimpleNamespace(text="should never be returned")
        finally:
            closed.append(True)

    model = SimpleNamespace(transcribe=lambda *_args, **_kwargs: (segments(), None))
    monkeypatch.setattr(
        "voice_to_me.transcriber.importlib.import_module",
        lambda _name: SimpleNamespace(WhisperModel=lambda *_args, **_kwargs: model),
    )
    with pytest.raises(TranscriptionCancelled):
        WhisperTranscriber(WhisperSettings()).transcribe(np.ones(16000), cancel=cancel)
    assert closed == [True]


def test_decoder_failure_is_reported_without_partial_text(monkeypatch):
    def segments():
        yield SimpleNamespace(text="partial text")
        raise RuntimeError("decoder failure")

    model = SimpleNamespace(transcribe=lambda *_args, **_kwargs: (segments(), None))
    monkeypatch.setattr(
        "voice_to_me.transcriber.importlib.import_module",
        lambda _name: SimpleNamespace(WhisperModel=lambda *_args, **_kwargs: model),
    )
    with pytest.raises(TranscriptionError, match="transcribe"):
        WhisperTranscriber(WhisperSettings()).transcribe(np.ones(16000))


def test_prepare_loads_once_and_reuses_model_without_repeating_preparing_stage(whisper):
    transcriber = WhisperTranscriber(WhisperSettings())
    events = []
    assert transcriber.backend_description == "Speech model not prepared yet."
    transcriber.prepare(progress=lambda stage, message: events.append((stage, message)))
    assert len(whisper) == 1
    assert [stage for stage, _ in events] == ["preparing"]
    assert transcriber.backend_description == "CPU · int8"
    events.clear()
    transcriber.prepare(progress=lambda stage, message: events.append((stage, message)))
    assert events == []
    assert transcriber.transcribe(np.ones(16000), progress=lambda s, m: events.append((s, m)))
    assert [stage for stage, _ in events] == ["transcribing"]
    assert len(whisper) == 1


def test_cold_transcription_reports_preparing_then_actual_transcribing(whisper):
    events = []
    transcriber = WhisperTranscriber(WhisperSettings())
    assert transcriber.transcribe(np.ones(16000), progress=lambda s, m: events.append((s, m)))
    assert [stage for stage, _ in events] == ["preparing", "transcribing"]
    assert "CPU · int8" in events[-1][1]


def test_prepare_cancelled_from_progress_never_loads_a_model(whisper):
    cancel = threading.Event()
    with pytest.raises(TranscriptionCancelled):
        WhisperTranscriber(WhisperSettings()).prepare(
            cancel, progress=lambda _stage, _message: cancel.set(),
        )
    assert whisper == []


def test_progress_failure_does_not_break_transcription_or_leak_content(whisper, caplog):
    def fail(*_):
        raise RuntimeError("private message that must not leak")

    assert WhisperTranscriber(WhisperSettings()).transcribe(np.ones(16000), progress=fail)
    assert "RuntimeError" in caplog.text
    assert "private message" not in caplog.text


def test_gpu_uses_resolved_compute_and_reports_actual_loaded_backend(whisper, monkeypatch):
    monkeypatch.setattr(
        transcriber_module, "resolve_backend",
        lambda _: BackendInfo("cuda", "int8_float16", True, "GPU (CUDA) · int8_float16", True),
    )
    transcriber = WhisperTranscriber(WhisperSettings(device="cuda", compute_type="float32"))
    transcriber.prepare()
    assert whisper[0].options["device"] == "cuda"
    assert whisper[0].options["compute_type"] == "int8_float16"
    assert transcriber.actual_device == "cuda"
    assert transcriber.actual_compute_type == "int8_float16"
    assert transcriber.backend_description == "GPU (CUDA) · int8_float16"


def test_cpu_fallback_after_gpu_selection_is_rejected(monkeypatch):
    monkeypatch.setattr(
        "voice_to_me.transcriber.resolve_backend",
        lambda _: BackendInfo("cuda", "int8_float16", True, "GPU (CUDA) · int8_float16", True),
    )
    native = SimpleNamespace(model=SimpleNamespace(device="cpu", compute_type="int8"))
    monkeypatch.setattr(
        "voice_to_me.transcriber.importlib.import_module",
        lambda _: SimpleNamespace(WhisperModel=lambda *args, **kwargs: native),
    )
    transcriber = WhisperTranscriber(WhisperSettings())
    with pytest.raises(TranscriptionError, match="CPU fallback was not accepted"):
        transcriber.prepare()
    assert transcriber._model is None


@pytest.mark.parametrize("message,expected", [
    ("Library cublas64_12.dll is not found", "GPU runtime"),
    ("Could not load cudnn64_9.dll", "GPU runtime"),
    ("CUDA failed with CUDA_ERROR_OUT_OF_MEMORY", "insufficient free memory"),
])
def test_gpu_native_failure_is_actionable_without_a_silent_cpu_retry(monkeypatch, message, expected):
    calls = []

    def fail(*args, **kwargs):
        calls.append(kwargs)
        raise RuntimeError(message)

    monkeypatch.setattr(
        "voice_to_me.transcriber.importlib.import_module",
        lambda _: SimpleNamespace(WhisperModel=fail),
    )
    with pytest.raises(TranscriptionError, match=expected):
        WhisperTranscriber(WhisperSettings()).prepare()
    assert len(calls) == 1


def test_decode_cuda_dll_failure_is_reported_as_a_gpu_runtime_problem(whisper):
    transcriber = WhisperTranscriber(WhisperSettings())
    transcriber.prepare()

    def fail(*_args, **_kwargs):
        raise RuntimeError("cublas64_12.dll was not found")

    whisper[0].transcribe = fail
    with pytest.raises(TranscriptionError, match="GPU runtime"):
        transcriber.transcribe(np.ones(16000))


def test_model_download_is_never_allowed_as_a_preparation_side_effect(whisper):
    with pytest.raises(TranscriptionError, match="explicit Settings action"):
        WhisperTranscriber(WhisperSettings(local_files_only=False)).prepare()
    assert whisper == []


def test_timing_logs_contain_backend_and_duration_but_never_text(whisper, caplog):
    caplog.set_level("INFO", logger="voice_to_me.transcriber")
    transcriber = WhisperTranscriber(WhisperSettings())
    transcriber.transcribe(np.ones(16000))
    assert "prepared in" in caplog.text
    assert "actual_device=cpu actual_compute=int8" in caplog.text
    assert "for 1.00s of audio" in caplog.text
    assert "Olá" not in caplog.text
    assert "Tudo bem" not in caplog.text
