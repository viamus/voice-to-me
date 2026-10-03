# Troubleshooting

| Symptom | Action |
| --- | --- |
| Codex is not configured | Install and sign in to Codex CLI, or disable **Refine with Codex CLI** and save Settings for local transcription |
| Codex is incompatible | Update the CLI using its official installation method; Voice to Me needs isolated `exec` and structured-output options |
| Refinement times out | Check CLI login and network access; retry, or disable refinement |
| Microphone cannot open | Check Windows microphone permissions and select the correct input in Settings |
| Model is missing | Choose a transcription profile and click **Download model** |
| Large model feels slow | Use Balanced/Fast and check GPU acceleration status; initial preparation has a separate loading step |
| CUDA is unavailable | Rerun setup with NVIDIA drivers available, or use CPU; the project needs its own GPU dependencies |
| Paste does nothing | Focus an external text field, release the shortcut/modifiers and check the binding; elevated apps may reject normal-process input |
| Mouse X1/X2 is not recognized | Check whether the mouse driver exposes standard extra buttons or remaps them |
| Windows notification is not visible | Check notification settings/Do Not Disturb; status and sounds remain available in the app |
| Tk cannot initialize | Ensure Python 3.12 includes Tcl/Tk and its runtime files are readable |
| App appears already running | Check the tray; choose **Quit** before starting another instance |

Use `debug-run.bat` or the `check` command for local diagnostics. Logs are under the settings directory and exclude dictated text. When reporting a problem, include the app version, transcription profile and error/status; share a synthetic reproduction rather than a private conversation.
