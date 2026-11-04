import io
import sys
from pathlib import Path

from rich.console import Console

from runbook_pilot.approve import (
    ScriptedApprover,
    edit_step,
    parse_notes,
    parse_step_markdown,
)
from runbook_pilot.models import Step


def test_parse_step_markdown_strips_backticks():
    fallback = Step("old", "true", "old expected")
    text = "- Intent: Show space\n- Command: `df -h`\n- Expected: a table\n"
    intent, command, expected = parse_step_markdown(text, fallback)
    assert (intent, command, expected) == ("Show space", "df -h", "a table")


def test_parse_notes_splits_the_lesson():
    text = "## Decisions and next time\n\nLeave the images.\n\n## Lesson\n\nStart with df.\n"
    decisions, lesson = parse_notes(text, "fallback", "fallback lesson")
    assert decisions == "Leave the images."
    assert lesson == "Start with df."


def test_editor_round_trip(tmp_path: Path):
    script = tmp_path / "edit.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "Path(sys.argv[1]).write_text("
        "'- Intent: Say hi\\n- Command: `echo hi`\\n- Expected: hi\\n'"
        ")\n",
        encoding="utf-8",
    )
    step = Step("old", "rm -rf /tmp/x", "no")
    intent, command, expected = edit_step(step, editor=f"{sys.executable} {script}")
    assert (intent, command, expected) == ("Say hi", "echo hi", "hi")


def test_scripted_reject_and_quit():
    gate = ScriptedApprover(["r", "q"], Console(file=io.StringIO(), highlight=False))
    assert gate.decide(Step("a", "echo a", "a")).action == "reject"
    assert gate.decide(Step("b", "echo b", "b")).action == "quit"
    assert gate.decide(Step("c", "echo c", "c")).action == "quit"
