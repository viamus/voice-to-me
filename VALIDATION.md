# Validation

Validated on Windows with Python **3.12.12** on **2026-10-03**.

## Automated checks

| Check | Result |
| --- | --- |
| `python -m pytest -q` | **568 passed** |
| `python -m ruff check src tests scripts` | Passed |
| `uv build --wheel --offline` | Passed |
| `python scripts/check_wheel.py` | Required modules, defaults, icons and three sound files present |

The tests cover pipeline transitions, cancellation, retries, optional Codex refinement, local CLI readiness, Unicode clipboard failure recovery, recording and paste shortcut isolation, embedded Settings and rollback, sound event ordering, and bounded shutdown. They use simulated audio, hooks, clipboard and process adapters rather than recording or pasting into another application.

Prior native Windows checks also initialized the real Tk window, edited and saved Settings in that same window, invoked tray notification APIs, and verified Pythonw shutdown with a blocked preparation worker. The desktop was locked, so visible notification delivery and physical focus behavior were not assessed.

GitHub Actions runs locked dependencies, lint, tests and wheel checks on a Windows runner. Each run reports its result separately from this local validation record.

## Local GPU measurement

On **2026-10-02**, the same 11.578-second synthetic Portuguese sentence and cached Large V3 model were tested on an RTX 4060 with 8 GB VRAM:

| Configuration | First transcription | Warm transcription |
| --- | ---: | ---: |
| Previous CUDA float32, beam 5 | 22.942 s | 24.652 s |
| Optimized CUDA int8_float16, beam 1 | 1.852 s | 1.150 s |

The production adapter was checked separately: 9.959 seconds to prepare the model, 2.689 seconds for the first transcription and 1.199 seconds on reuse. It retained the loaded model between requests. These measurements exclude Codex refinement and use synthetic speech, not a physical microphone or a recognition-accuracy benchmark. Actual times depend on the model, recording and machine.

The earlier retained app processes were identified and closed. Current shutdown removes the tray, closes input hooks and waits briefly for workers; the CLI exits its own process if a native inference call is still running.

## Manual checks still needed

- Record a real sentence with the selected microphone and check recognition quality.
- Try recording and paste shortcuts with the actual keyboard, mouse and driver mappings in the destination application.
- Confirm that start, stop and ready cues are audible at the preferred Windows volume.
- Check visible Windows notifications with the machine's notification settings.
- Exercise the complete spoken-message-to-refinement-to-clipboard workflow with the configured Codex account.

Native model loading/inference cannot be interrupted immediately mid-call. Cancel suppresses late results; Quit closes the application and releases its resources. Clipboard replacement attempts to restore prior Unicode text after a native write failure, but Windows offers no atomic transaction and rich-text/image formats are not snapshotted.

UI previews in the README are locally generated design illustrations, not screenshots of a hardware test. No microphone, physical input injection, speaker playback, actual clipboard replacement or remote Codex refinement was used for the 2026-10-03 automated checks.
