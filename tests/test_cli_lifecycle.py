"""CLI exit boundaries; no GUI, global inputs, audio or clipboard."""
import subprocess
import sys
from unittest.mock import Mock

import pytest

from voice_to_me import cli


@pytest.mark.parametrize("user_quit,pending,expected_exit", [
    (False, False, False), (False, True, False), (True, False, False), (True, True, True),
])
def test_forced_exit_only_after_user_quit_and_native_work_remains(
    monkeypatch, user_quit, pending, expected_exit,
):
    runtime = Mock(has_active_worker=pending)
    exit_process = Mock()
    monkeypatch.setattr(cli.os, "_exit", exit_process)
    cli._shutdown_app(runtime, user_quit=user_quit)
    runtime.shutdown.assert_called_once_with()
    assert exit_process.call_count == int(expected_exit)
    if expected_exit:
        exit_process.assert_called_once_with(0)


def test_actual_child_process_exits_instead_of_retaining_a_blocked_daemon_worker():
    code = """
from threading import Thread, Event
from voice_to_me.cli import _shutdown_app
class Runtime:
    worker = Thread(target=Event().wait, daemon=True)
    def shutdown(self): pass
    @property
    def has_active_worker(self): return self.worker.is_alive()
runtime = Runtime()
runtime.worker.start()
_shutdown_app(runtime, user_quit=True)
raise SystemExit(99)
"""
    result = subprocess.run([sys.executable, "-c", code], timeout=5, capture_output=True)
    assert result.returncode == 0
