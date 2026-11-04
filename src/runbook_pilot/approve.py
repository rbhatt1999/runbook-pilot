import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from runbook_pilot.models import Decision, Step

PROMPT = "[a]pprove  [e]dit  [r]eject  [q]uit"
_BULLET = re.compile(r"^[-*]\s*(Intent|Command|Expected)\s*:\s*(.*)$", re.IGNORECASE)


def parse_step_markdown(text: str, fallback: Step) -> tuple[str, str, str]:
    intent, command, expected = fallback.intent, fallback.command, fallback.expected
    for line in text.splitlines():
        match = _BULLET.match(line.strip())
        if not match:
            continue
        key = match.group(1).lower()
        value = match.group(2).strip()
        if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
            value = value[1:-1]
        if not value:
            continue
        if key == "intent":
            intent = value
        elif key == "command":
            command = value
        elif key == "expected":
            expected = value
    return intent, command, expected


def parse_notes(text: str, fallback_decisions: str, fallback_lesson: str) -> tuple[str, str]:
    match = re.search(r"^##\s+Lesson\s*$", text, re.MULTILINE)
    if not match:
        body = text.strip() or fallback_decisions
        return body, fallback_lesson
    decisions = text[: match.start()].strip()
    decisions = re.sub(r"^##\s+Decisions and next time\s*", "", decisions).strip()
    lesson = " ".join(text[match.end() :].split())
    return decisions or fallback_decisions, lesson or fallback_lesson


def render_notes(decisions: str, lesson: str) -> str:
    return (
        "## Decisions and next time\n\n"
        f"{decisions.strip()}\n\n"
        "## Lesson\n\n"
        f"{lesson.strip()}\n"
    )


class ScriptedApprover:
    def __init__(self, actions: list[str], console: Console | None = None):
        self.actions = list(actions)
        self.console = console or Console(highlight=False)

    def decide(self, step: Step, *, index: int = 1, total: int = 1) -> Decision:
        _show_step(self.console, step, index, total)
        self.console.print(PROMPT, markup=False, highlight=False)
        if not self.actions:
            self.console.print("> q", markup=False, highlight=False)
            return Decision("quit")
        action = self.actions.pop(0)
        self.console.print(f"> {action}", markup=False, highlight=False)
        if action in {"a", "approve"}:
            return Decision("approve")
        if action in {"r", "reject"}:
            return Decision("reject")
        if action in {"q", "quit"}:
            return Decision("quit")
        if action in {"e", "edit"}:
            raise ValueError("edit needs an interactive terminal; use a, r, or q in --decisions")
        raise ValueError(f"unknown decision {action!r}; use a, r, or q")

    def edit_notes(self, decisions: str, lesson: str) -> tuple[str, str]:
        return decisions, lesson


class InteractiveApprover:
    def __init__(self, console: Console | None = None, editor: str | None = None):
        self.console = console or Console(highlight=False)
        self.editor = editor

    def decide(self, step: Step, *, index: int = 1, total: int = 1) -> Decision:
        _show_step(self.console, step, index, total)
        while True:
            try:
                choice = input(PROMPT + ": ").strip().lower()
            except EOFError:
                return Decision("quit")
            if choice in {"a", "approve"}:
                return Decision("approve")
            if choice in {"r", "reject"}:
                return Decision("reject")
            if choice in {"q", "quit"}:
                return Decision("quit")
            if choice in {"e", "edit"}:
                intent, command, expected = edit_step(step, self.editor)
                if (intent, command, expected) == (step.intent, step.command, step.expected):
                    return Decision("approve")
                self.console.print(f"command is now: {command}", markup=False, highlight=False)
                return Decision("edit", intent=intent, command=command, expected=expected)
            self.console.print("Enter a, e, r, or q.", markup=False, highlight=False)

    def edit_notes(self, decisions: str, lesson: str) -> tuple[str, str]:
        self.console.print(Panel(Text(decisions), title="Decisions and next time"))
        self.console.print("Lesson: " + lesson, markup=False, highlight=False)
        try:
            choice = input("[a]ccept  [e]dit: ").strip().lower()
        except EOFError:
            return decisions, lesson
        if choice not in {"e", "edit"}:
            return decisions, lesson
        updated = edit_text(render_notes(decisions, lesson), self.editor)
        return parse_notes(updated, decisions, lesson)


def edit_step(step: Step, editor: str | None = None) -> tuple[str, str, str]:
    template = (
        f"- Intent: {step.intent}\n"
        f"- Command: `{step.command}`\n"
        f"- Expected: {step.expected}\n"
    )
    text = edit_text(template, editor)
    return parse_step_markdown(text, step)


def edit_text(contents: str, editor: str | None = None) -> str:
    command = editor or os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(contents)
        path = Path(handle.name)
    try:
        subprocess.run([*shlex.split(command), str(path)], check=False)
        return path.read_text(encoding="utf-8")
    finally:
        path.unlink(missing_ok=True)


def scripted_actions(spec: str) -> list[str]:
    return [part.strip().lower() for part in spec.split(",") if part.strip()]


def _show_step(console: Console, step: Step, index: int, total: int) -> None:
    body = (
        f"Step {index}/{total}\n"
        f"{step.intent}\n"
        f"$ {step.command}\n"
        f"expected: {step.expected}"
    )
    console.print(Panel(Text(body), title="Approve this step?", highlight=False))
