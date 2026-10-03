"""Read-only local Codex installation, CLI compatibility and sign-in checks."""

from __future__ import annotations

import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import CodexSettings
from .refiner import (
    _REQUIRED_FLAGS,
    _creation_flags,
    _minimal_environment,
    resolve_codex_executable,
)


@dataclass(frozen=True)
class CodexStatus:
    installed: bool
    compatible: bool
    authenticated: bool
    message: str

    @property
    def ready(self) -> bool:
        return self.installed and self.compatible and self.authenticated


def check_codex_readiness(settings: CodexSettings, *, timeout: float = 3.0) -> CodexStatus:
    """Check the native CLI without prompts, dictated text, login or refinement.

    Call this outside the GUI thread. The only subprocesses are ``exec --help``
    and ``login status`` in an empty temporary directory. Neither command starts
    a login flow. Authentication is represented only by its successful exit
    code; account details, tokens, logs and raw CLI output are never returned.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("The Codex status timeout must be positive")
    try:
        executable = resolve_codex_executable(settings.executable)
    except (RuntimeError, OSError, ValueError):
        return CodexStatus(False, False, False,
                           "Codex CLI was not found. Install and sign in to Codex CLI to enable "
                           "refinement, or keep it off for local transcription.")
    try:
        with tempfile.TemporaryDirectory(prefix="voice-to-me-codex-check-") as temporary:
            directory = Path(temporary)
            options = {
                "cwd": directory,
                "env": _minimal_environment(executable, directory),
                "stdin": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "timeout": timeout,
                "check": False,
                "creationflags": _creation_flags(),
                "shell": False,
            }
            try:
                result = subprocess.run([str(executable), "exec", "--help"],
                                        stdout=subprocess.PIPE, **options)
            except subprocess.TimeoutExpired:
                return CodexStatus(True, False, False,
                                   "Codex CLI did not respond. Try again, or keep refinement off.")
            except OSError:
                return CodexStatus(True, False, False,
                                   "Codex CLI could not start. Reinstall it to enable refinement.")
            help_text = result.stdout.decode("utf-8", errors="replace")
            if result.returncode or any(flag not in help_text for flag in _REQUIRED_FLAGS):
                return CodexStatus(True, False, False,
                                   "Codex CLI needs an update before refinement can be enabled.")
            try:
                # Discard stdout as well: API-key logins may display key/account
                # details. The status command's exit code is the only data needed.
                signed_in = subprocess.run([str(executable), "login", "status"],
                                           stdout=subprocess.DEVNULL, **options)
            except (OSError, subprocess.TimeoutExpired):
                return CodexStatus(True, True, False,
                                   "Codex sign-in status could not be checked. Try again, or "
                                   "keep refinement off for local transcription.")
            if signed_in.returncode:
                return CodexStatus(True, True, False,
                                   "Codex CLI is installed but not signed in. Sign in with "
                                   "codex login, then reopen Settings to enable refinement.")
            return CodexStatus(True, True, True,
                               "Codex CLI is installed, compatible and signed in. Refinement "
                               "is available; transcription also works with it turned off.")
    except OSError:
        return CodexStatus(True, False, False,
                           "Codex status could not be checked. Review temporary-folder access "
                           "or keep refinement off for local transcription.")
