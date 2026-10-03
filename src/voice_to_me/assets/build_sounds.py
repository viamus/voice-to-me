"""Generate Voice to Me's original, quiet PCM chimes without audio playback."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 44100
VOLUME = 0.16
GAP_SECONDS = 0.012
NOTES = {
    "recording-start.wav": ((523.25, 0.078), (783.99, 0.086)),
    "recording-stop.wav": ((698.46, 0.086), (523.25, 0.078)),
    "result-ready.wav": ((659.25, 0.083), (830.61, 0.083), (987.77, 0.107)),
}


def render_notes(notes: tuple[tuple[float, float], ...]) -> bytes:
    samples: list[int] = []
    for index, (frequency, duration) in enumerate(notes):
        if index:
            samples.extend([0] * round(GAP_SECONDS * SAMPLE_RATE))
        count = round(duration * SAMPLE_RATE)
        attack, release = round(0.010 * SAMPLE_RATE), round(0.018 * SAMPLE_RATE)
        for position in range(count):
            fade_in = min(1.0, position / attack)
            fade_out = min(1.0, (count - 1 - position) / release)
            envelope = math.sin(math.pi * fade_in / 2) ** 2
            envelope *= math.sin(math.pi * fade_out / 2) ** 2
            phase = 2 * math.pi * frequency * position / SAMPLE_RATE
            tone = 0.88 * math.sin(phase) + 0.12 * math.sin(phase * 2)
            samples.append(round(32767 * VOLUME * envelope * tone))
    return struct.pack(f"<{len(samples)}h", *samples)


def build() -> None:
    output = Path(__file__).parent
    for filename, notes in NOTES.items():
        with wave.open(str(output / filename), "wb") as cue:
            cue.setnchannels(1)
            cue.setsampwidth(2)
            cue.setframerate(SAMPLE_RATE)
            cue.writeframes(render_notes(notes))


if __name__ == "__main__":
    build()
