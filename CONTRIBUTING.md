# Contributing to Voice to Me

Bug reports, accessibility improvements, documentation fixes and focused code changes are welcome. Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Report a problem or suggest an improvement

Check [troubleshooting](docs/troubleshooting.md) and existing [issues](https://github.com/viamus/voice-to-me/issues) first. Use the [bug or feature request form](https://github.com/viamus/voice-to-me/issues/new/choose) to explain the problem and the expected behavior.

Reproduce dictation problems with a synthetic message such as "Please move our meeting to ten." Remove private conversations, writing styles, credentials and identifying paths from logs or screenshots. Report vulnerabilities privately using [SECURITY.md](SECURITY.md).

## Set up development

Use Windows, Python 3.12 with Tcl/Tk, Git and `uv`. Fork the repository, clone your fork and create a branch for your change. In PowerShell, from the repository root:

```powershell
uv sync --locked --extra dev --python 3.12
.\.venv\Scripts\python.exe -m voice_to_me demo
```

The demo uses simulated adapters. It does not record audio, use the system clipboard, activate global shortcuts or request a Codex refinement.

Code is in `src/voice_to_me`, tests in `tests`, and user documentation in `docs`. The app interface and documentation are in English.

## Make and validate a change

Keep changes focused and explain their user-visible effect. Preserve keyboard navigation, mouse controls and one-handed use. Keep diagnostics free of dictated text, writing styles and credentials.

For code changes, run:

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m pytest -q
```

Add or update meaningful tests when behavior changes. The suite uses simulated devices and adapters. A passing test suite does not establish that a physical microphone, GPU, global shortcut or destination application works correctly.

For packaging changes, also run:

```powershell
uv build --wheel
.\.venv\Scripts\python.exe scripts/check_wheel.py
```

For documentation-only changes, check formatting, links and any commands you changed. You do not need to record audio or invoke Codex to validate documentation. See [VALIDATION.md](VALIDATION.md) for existing evidence and manual test boundaries.

## Open a pull request

Target `main` and use the PR template to describe what changed, why, and the checks you ran. Distinguish simulated checks from any manual Windows testing. Use synthetic content in examples and screenshots. GitHub Actions runs lint, tests and wheel checks on Windows with Python 3.12.

Contributions are accepted under the project's [MIT license](LICENSE).
