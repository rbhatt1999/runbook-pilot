import subprocess
import sys
from pathlib import Path

import pytest

from runbook_pilot.executor import CommandRefused, run_argv


def test_captures_stdout_without_a_shell():
    result = run_argv(["echo", "$(id)"], timeout=5, max_chars=1000)
    assert result.exit_code == 0
    assert result.stdout.strip() == "$(id)"


def test_source_does_not_enable_a_shell():
    source = Path(run_argv.__code__.co_filename).read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "shell=False" in source


def test_truncates_long_output():
    code = "print('x' * 5000)"
    result = run_argv([sys.executable, "-c", code], timeout=5, max_chars=200)
    assert result.truncated
    assert len(result.stdout) <= 200
    assert "truncated" in result.stdout


def test_times_out():
    code = "import time; time.sleep(5)"
    result = run_argv([sys.executable, "-c", code], timeout=1, max_chars=1000)
    assert result.exit_code == 124
    assert "Timed out" in result.stderr


def test_redacts_the_api_key(monkeypatch):
    secret = "sk-test-secret-value"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    code = "import os; print(os.environ['OPENAI_API_KEY'])"
    result = run_argv([sys.executable, "-c", code], timeout=5, max_chars=2000)
    assert secret not in result.stdout
    assert "[redacted]" in result.stdout


def test_refuses_rm_rf_before_subprocess(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("subprocess was called")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(CommandRefused):
        run_argv(["rm", "-rf", "/"], timeout=1, max_chars=100)
    with pytest.raises(CommandRefused):
        run_argv(["/bin/rm", "-fr", "/tmp"], timeout=1, max_chars=100)
