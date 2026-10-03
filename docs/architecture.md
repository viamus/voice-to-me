# How Voice to Me works

The pipeline is:

```text
Microphone → local Whisper → optional Codex CLI → Windows clipboard
                                                  ↓
                                   your manual paste shortcut
```

The controller owns recording, preparation, transcription, refinement, copy, cancellation and retries. Device adapters keep native APIs out of the interface. Tk runs on its main thread; background callbacks queue status and sound events. Settings is a page in the existing window.

## Local transcription

Audio is mono 16 kHz float32 in a bounded memory buffer. Faster-whisper uses a cached converted model. Compatible NVIDIA hardware uses automatic CUDA precision, preferring `int8_float16` then `float16`; CPU uses an appropriate local backend. Short decoding search and a resident model reduce latency. Explicit CUDA failures report an error instead of silently claiming GPU use.

Preparation, transcription and refinement have distinct status/timing information. Logs contain backend details, elapsed times and error types, not recordings or dictated text.

## Codex refinement

When enabled, **Codex CLI is a required dependency**. Voice to Me resolves a native executable and invokes `codex exec` with UTF-8 stdin. The transcription and writing style are content to edit, not commands to execute.

The invocation is read-only and ephemeral, runs from a temporary directory, ignores user configuration/rules, disables tools/plugins/hooks, and requires structured output with a valid nonempty `text` field. Only that field can reach the clipboard. Temporary output is removed afterward. Existing CLI authentication is reused; the app does not print credentials or forward API keys/MCP settings from its environment.

The Settings readiness check runs bounded local `exec --help` and `login status` commands. It does not send a transcription or start a login flow. Missing or unconfigured Codex makes the refinement option unavailable; saving it disabled selects local transcription directly. Local mode skips the CLI and style-loading step entirely.

## Clipboard and shortcuts

Only completed valid text replaces the clipboard. Silence, cancellation and errors before replacement leave it untouched. Windows clipboard replacement is not transactional: restoration after a native failure is best-effort and only prior Unicode text is preserved, not rich text or images.

The optional paste macro is independent of completion. A manual release triggers one tagged Ctrl-down/V-down/V-up/Ctrl-up sequence in the same external foreground window. Focus changes, held modifiers, repeated input and shutdown cancel or reject pending actions. Its tags prevent the generated Ctrl+V from activating either app shortcut. It never presses Enter or accesses clipboard contents.

## Lifecycle

Completed messages enter a bounded in-memory session history only after a successful clipboard write. Immutable snapshots reach the interface through its event queue. Explicit recopy uses the same clipboard adapter and never starts recording, refinement, paste or completion sounds. Copy/clear are blocked during active processing; shutdown detaches history observers and clears the list. History text is excluded from status, tray tooltips, notifications and logs.

Recording is a toggle, not a held key. Settings suspends both input actions. One pipeline worker runs at a time.

The window's close button and Minimize hide the window when a visible tray icon is available. If the tray is unavailable or still starting, ordinary taskbar minimization keeps the app reachable. These actions preserve the process, recording, processing, configured shortcuts and in-memory history. The tray's Open Voice to Me command restores the same window.

An open Settings editor is closed first, discarding unsaved changes and resuming shortcuts. If capture or shortcut cleanup fails, the window stays visible so the user can retry.

Explicit Quit stops sound, removes the tray notification/icon, cancels paste, closes hooks, clears session history and performs bounded cleanup. If a native speech call is still running, the CLI ends its own process to release resources.

Whisper native inference is cooperatively cancelled while the app stays open. No Jarvis runtime hook, file watcher, automatic paste or automatic send is used.
