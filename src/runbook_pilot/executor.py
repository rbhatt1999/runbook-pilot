import os
import subprocess
from dataclasses import dataclass

from runbook_pilot.allowlist import is_rm_rf


class CommandRefused(RuntimeError):
    pass


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool


def run_argv(argv: list[str], *, timeout: int, max_chars: int) -> ExecResult:
    if is_rm_rf(argv):
        raise CommandRefused("rm -rf is never run")
    try:
        # shell=False so the allowlist's argv is what actually runs.
        completed = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _as_text(exc.stdout)
        stderr = _as_text(exc.stderr)
        stderr = (stderr + f"\nTimed out after {timeout}s.").strip()
        stdout, stderr, truncated = _clip_pair(stdout, stderr, max_chars)
        return ExecResult(124, _scrub(stdout), _scrub(stderr), truncated)
    stdout, stderr, truncated = _clip_pair(completed.stdout or "", completed.stderr or "", max_chars)
    return ExecResult(completed.returncode, _scrub(stdout), _scrub(stderr), truncated)


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _clip_pair(stdout: str, stderr: str, max_chars: int) -> tuple[str, str, bool]:
    stdout, cut_out = _clip(stdout, max_chars)
    stderr, cut_err = _clip(stderr, max_chars)
    return stdout, stderr, cut_out or cut_err


def _clip(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    note = f"\n[truncated to {max_chars} chars]"
    keep = max(0, max_chars - len(note))
    return text[:keep] + note, True


def _scrub(text: str) -> str:
    key = os.environ.get("OPENAI_API_KEY", "")
    # Ignore tiny values so a placeholder key doesn't punch holes in the log.
    if len(key) < 8 or not text:
        return text
    return text.replace(key, "[redacted]")
