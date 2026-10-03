# Getting started

## Requirements

- Windows and Python **3.12**, including Tcl/Tk.
- A working microphone and a local Whisper model.
- A compatible NVIDIA GPU is optional; CPU transcription also works.
- **Codex CLI installed and signed in is mandatory for text refinement.** For direct local transcription, disable **Refine with Codex CLI** in Settings.

Install and authenticate Codex using the [official guide](https://developers.openai.com/codex/cli/). Voice to Me uses the existing CLI login; it does not ask for or copy your credentials.

## Setup

Run `setup.bat`, then `run.bat`. The launcher opens the app without a console. Use `debug-run.bat` to troubleshoot with a console. Nothing is installed as a service or added to Windows startup.

Setup creates a local `.venv`. With `uv` it uses `uv.lock`; the pip fallback uses the package version ranges. NVIDIA detection also installs project-local cuBLAS/cuDNN dependencies. No other application's Python environment is used.

Before dictating, open Settings and choose a microphone, language and transcription profile. **Download model** retrieves weights explicitly if they are not cached. Model preparation happens before recording and the model stays loaded between messages.

## Settings

| Setting | Purpose |
| --- | --- |
| Recording shortcut | One key/chord or mouse button to start and stop |
| Paste shortcut (Ctrl + V) | A separate manual button to paste into the focused external app |
| Writing style | Tone, punctuation and rewriting instructions for enabled refinement |
| Microphone | Default input or an available device |
| Dictation language | `pt`, `en`, or blank for automatic detection |
| Transcription profile | Fast (`small`), Balanced (`turbo`), Best quality (`large-v3`) |
| Refine with Codex CLI | Enable only when the CLI is installed, compatible and signed in |
| Codex model | Optional; blank uses the CLI default |

Capture buttons recognize keyboard or mouse automatically. Each new capture replaces that action's previous binding. All shortcuts stay paused throughout Settings. **Save & Apply** persists and activates changes; **Back/Cancel** discard the draft.

Paste supports one key, optionally with Ctrl/Alt/Shift/Win, or any standard mouse button. Middle/X1/X2 paste buttons consume their default action to avoid scrolling or navigating. Left/right retain normal clicks and focus, and ignore the app and taskbar controls. A driver must report X1/X2; some mouse utilities remap these buttons.

## Daily use

Press once to record, speak naturally, then press again to stop. The completion chime confirms that text is on the clipboard. Focus your destination text field and activate the separate paste shortcut. Sending remains a manual action in that application.

Busy processing ignores additional recording toggles. **Cancel** discards the pending result; **Try again** reuses retained audio or transcription after a failure. A new recording replaces this retry buffer. Closing the app removes the tray, stops hooks and cancels pending paste work.

## Files and diagnostics

The default settings and writing style live in `%LOCALAPPDATA%\VoiceToMe`. Use the editor rather than opening another application. A custom configuration can be passed with `--config`.

```powershell
# List microphones; does not record.
.\.venv\Scripts\python.exe -m voice_to_me devices

# Check dependencies, enabled Codex readiness and cached model.
.\.venv\Scripts\python.exe -m voice_to_me check

# Preview with simulated adapters, without recording or clipboard changes.
.\.venv\Scripts\python.exe -m voice_to_me demo --auto-close 3
```

Manual TOML edits require restarting the app. See [config.example.toml](../config.example.toml) for the complete configuration schema.
