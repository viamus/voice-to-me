"""Read-only readiness diagnostics with fake subprocesses and no login or network."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from voice_to_me import cli, codex_status, performance
from voice_to_me.codex_status import CodexStatus, check_codex_readiness
from voice_to_me.config import CodexSettings, load_settings
from voice_to_me.refiner import RefinementError


@pytest.fixture
def local_cli(tmp_path, monkeypatch):
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"never executed")
    monkeypatch.setattr(codex_status, "resolve_codex_executable", lambda _: executable)
    calls = SimpleNamespace(
        commands=[], help_result=SimpleNamespace(
            returncode=0, stdout=" ".join(codex_status._REQUIRED_FLAGS).encode(),
        ), login_result=SimpleNamespace(returncode=0, stdout=b"private account/token details"),
    )

    def run(command, **kwargs):
        calls.commands.append((command, kwargs))
        result = calls.help_result if command[1:] == ["exec", "--help"] else calls.login_result
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(codex_status.subprocess, "run", run)
    return calls


def test_ready_check_only_runs_hidden_help_and_local_login_status(local_cli, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private-key")
    monkeypatch.setenv("CODEX_THREAD_ID", "private-context")
    monkeypatch.setenv("CODEX_HOME", "C:/existing-auth")
    monkeypatch.setenv("HTTP_PROXY", "private-proxy")
    status = check_codex_readiness(CodexSettings(), timeout=2)
    assert status.ready and status.installed and status.compatible and status.authenticated
    assert "private" not in status.message
    assert [command[1:] for command, _ in local_cli.commands] == [
        ["exec", "--help"], ["login", "status"],
    ]
    for _, options in local_cli.commands:
        assert options["shell"] is False
        assert options["stdin"] == subprocess.DEVNULL
        assert options["stderr"] == subprocess.DEVNULL
        assert options["timeout"] == 2
        assert options["creationflags"] == codex_status._creation_flags()
        assert options["env"]["CODEX_HOME"] == "C:/existing-auth"
        assert "OPENAI_API_KEY" not in options["env"]
        assert "CODEX_THREAD_ID" not in options["env"]
        assert "HTTP_PROXY" not in options["env"]
        assert not Path(options["cwd"]).exists()
    assert local_cli.commands[0][1]["stdout"] == subprocess.PIPE
    assert local_cli.commands[1][1]["stdout"] == subprocess.DEVNULL


def test_missing_cli_is_actionable_without_starting_subprocess(local_cli, monkeypatch):
    resolver = Mock(side_effect=RefinementError("private path and exception details"))
    monkeypatch.setattr(codex_status, "resolve_codex_executable", resolver)
    status = check_codex_readiness(CodexSettings())
    assert not status.ready and not status.installed
    assert "Install" in status.message and "local transcription" in status.message
    assert "private" not in status.message
    assert local_cli.commands == []


@pytest.mark.parametrize("help_result", [
    SimpleNamespace(returncode=0, stdout=b"old CLI"),
    SimpleNamespace(returncode=2, stdout=" ".join(codex_status._REQUIRED_FLAGS).encode()),
])
def test_unsupported_cli_does_not_probe_authentication(local_cli, help_result):
    local_cli.help_result = help_result
    status = check_codex_readiness(CodexSettings())
    assert status.installed and not status.compatible and not status.authenticated
    assert "update" in status.message
    assert len(local_cli.commands) == 1


@pytest.mark.parametrize("error", [OSError("private native error"), subprocess.TimeoutExpired("codex", 3)])
def test_help_failure_has_safe_status_and_short_timeout(local_cli, error):
    local_cli.help_result = error
    status = check_codex_readiness(CodexSettings())
    assert status.installed and not status.ready and not status.compatible
    assert "private" not in status.message
    assert len(local_cli.commands) == 1
    assert local_cli.commands[0][1]["timeout"] == 3


def test_missing_sign_in_is_actionable_and_does_not_start_login(local_cli):
    local_cli.login_result = SimpleNamespace(returncode=1, stdout=b"private account details")
    status = check_codex_readiness(CodexSettings())
    assert status.installed and status.compatible and not status.authenticated
    assert "codex login" in status.message and "private" not in status.message
    assert local_cli.commands[-1][0][1:] == ["login", "status"]


@pytest.mark.parametrize("error", [OSError("private native error"), subprocess.TimeoutExpired("codex", 3)])
def test_authentication_check_failure_does_not_expose_raw_details(local_cli, error):
    local_cli.login_result = error
    status = check_codex_readiness(CodexSettings())
    assert status.installed and status.compatible and not status.ready
    assert "could not be checked" in status.message and "private" not in status.message
    assert len(local_cli.commands) == 2


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_status_timeout_is_rejected(local_cli, timeout):
    with pytest.raises(ValueError, match="positive"):
        check_codex_readiness(CodexSettings(), timeout=timeout)
    assert local_cli.commands == []


def mock_check_dependencies(monkeypatch):
    monkeypatch.setattr(cli, "importlib", SimpleNamespace(import_module=lambda _: Mock()))
    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(Tk=lambda: Mock()))
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", SimpleNamespace(download_model=Mock()))
    monkeypatch.setattr(performance, "resolve_backend", lambda _: SimpleNamespace(
        device="cpu", compute_type="int8",
    ))


def test_cli_check_skips_codex_entirely_in_local_mode(tmp_path, monkeypatch, capsys):
    mock_check_dependencies(monkeypatch)
    checker = Mock(side_effect=AssertionError("Codex must not be checked"))
    monkeypatch.setattr(codex_status, "check_codex_readiness", checker)
    settings = load_settings(tmp_path / "config.toml")
    settings = replace(settings, codex=replace(settings.codex, enabled=False))
    assert cli._check(settings) == 0
    assert "Codex CLI/login are not required" in capsys.readouterr().out
    checker.assert_not_called()


@pytest.mark.parametrize("ready", [True, False])
def test_cli_check_requires_ready_codex_only_when_enabled(tmp_path, monkeypatch, capsys, ready):
    mock_check_dependencies(monkeypatch)
    checker = Mock(return_value=CodexStatus(True, True, ready, "Sign in to enable refinement."))
    monkeypatch.setattr(codex_status, "check_codex_readiness", checker)
    settings = load_settings(tmp_path / "config.toml")
    assert cli._check(settings) == (0 if ready else 1)
    checker.assert_called_once_with(settings.codex)
    text = capsys.readouterr().out
    assert ("signed in" if ready else "Sign in") in text
