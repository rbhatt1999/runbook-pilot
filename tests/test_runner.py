import pytest

from runbook_pilot.approve import ScriptedApprover
from runbook_pilot.executor import ExecResult
from runbook_pilot.models import Decision, Step, Usage
from runbook_pilot.notebook import load_notebook
from runbook_pilot.runner import resume_run, start_run
from tests.helpers import StubPlanner, write_allow, write_goal


def test_nothing_runs_before_approval(tmp_path, quiet):
    events = []

    class Gate:
        def decide(self, step, *, index, total):
            del index, total
            events.append(("decide", step.command))
            return Decision("approve")

        def edit_notes(self, decisions, lesson):
            events.append(("notes",))
            return decisions, lesson

    def execute(argv, *, timeout, max_chars):
        del timeout, max_chars
        events.append(("exec", argv))
        return ExecResult(0, "ok\n", "", False)

    planner = StubPlanner(
        [Step("one", "echo one", "one"), Step("two", "echo two", "two")]
    )
    start_run(
        write_goal(tmp_path),
        write_allow(tmp_path),
        tmp_path / "runs",
        model="fake",
        provider_name="fake",
        provider=planner,
        approver=Gate(),
        executor=execute,
        console=quiet,
    )
    assert events[0][0] == "decide"
    execs = [event for event in events if event[0] == "exec"]
    assert execs == [("exec", ["echo", "one"]), ("exec", ["echo", "two"])]
    for index, event in enumerate(events):
        if event[0] == "exec":
            assert events[index - 1] == ("decide", " ".join(event[1]))


def test_reject_runs_nothing_and_still_writes_the_index(tmp_path, quiet):
    def execute(argv, *, timeout, max_chars):
        raise AssertionError(f"should not run {argv}")

    planner = StubPlanner([Step("say hi", "echo hi", "hi"), Step("again", "echo again", "again")])
    nb = start_run(
        write_goal(tmp_path),
        write_allow(tmp_path),
        tmp_path / "runs",
        model="fake",
        provider_name="fake",
        provider=planner,
        approver=ScriptedApprover(["r", "r"], quiet),
        executor=execute,
        console=quiet,
    )
    assert all(step.decision == "rejected" for step in nb.steps)
    assert all(step.outcome == "skipped" for step in nb.steps)
    text = nb.path.read_text(encoding="utf-8")
    assert "rejected" in text
    assert "echo hi" in text
    index = (tmp_path / "runs" / "INDEX.md").read_text(encoding="utf-8")
    assert "Echo is enough." in index
    assert "Lesson:" in index


def test_quit_before_approval_runs_nothing(tmp_path, quiet):
    calls = []

    def execute(argv, *, timeout, max_chars):
        calls.append(argv)
        return ExecResult(0, "", "", False)

    nb = start_run(
        write_goal(tmp_path),
        write_allow(tmp_path),
        tmp_path / "runs",
        model="fake",
        provider_name="fake",
        provider=StubPlanner(),
        approver=ScriptedApprover(["q"], quiet),
        executor=execute,
        console=quiet,
    )
    assert calls == []
    assert nb.status == "paused"
    assert nb.steps[0].decision == "pending"
    assert "Paused" in (tmp_path / "runs" / "INDEX.md").read_text(encoding="utf-8")


def test_approved_command_outside_the_allowlist_is_logged_and_not_run(tmp_path, quiet):
    calls = []

    def execute(argv, *, timeout, max_chars):
        calls.append(argv)
        return ExecResult(0, "", "", False)

    planner = StubPlanner([Step("wipe", "rm -rf /tmp/runbook-pilot-blocked", "gone")])
    planner.lesson = "rm -rf stayed blocked."
    nb = start_run(
        write_goal(tmp_path),
        write_allow(tmp_path),
        tmp_path / "runs",
        model="fake",
        provider_name="fake",
        provider=planner,
        approver=ScriptedApprover(["a"], quiet),
        executor=execute,
        console=quiet,
    )
    assert calls == []
    assert nb.steps[0].outcome == "blocked"
    text = nb.path.read_text(encoding="utf-8")
    assert "blocked" in text
    assert "rm -rf" in text
    assert "rm -rf stayed blocked." in (tmp_path / "runs" / "INDEX.md").read_text(encoding="utf-8")


def test_edit_runs_the_revised_command(tmp_path, quiet):
    calls = []

    def execute(argv, *, timeout, max_chars):
        calls.append(argv)
        return ExecResult(0, "hi\n", "", False)

    class EditGate(ScriptedApprover):
        def decide(self, step, *, index=1, total=1):
            del step, index, total
            return Decision("edit", intent="Say hi", command="echo hi", expected="hi")

    planner = StubPlanner([Step("wipe", "rm -rf /tmp/x", "no")])
    nb = start_run(
        write_goal(tmp_path),
        write_allow(tmp_path),
        tmp_path / "runs",
        model="fake",
        provider_name="fake",
        provider=planner,
        approver=EditGate([], quiet),
        executor=execute,
        console=quiet,
    )
    assert calls == [["echo", "hi"]]
    assert nb.steps[0].decision == "edited"
    assert nb.steps[0].outcome == "ran"


def test_dry_run_does_not_execute(tmp_path, quiet):
    def execute(argv, *, timeout, max_chars):
        raise AssertionError(argv)

    nb = start_run(
        write_goal(tmp_path),
        write_allow(tmp_path),
        tmp_path / "runs",
        model="fake",
        provider_name="fake",
        provider=StubPlanner(),
        dry_run=True,
        approver=ScriptedApprover(["a"], quiet),
        executor=execute,
        console=quiet,
    )
    assert nb.steps[0].outcome == "dry-run"
    assert "not executed" in nb.steps[0].interpretation


def test_log_records_output_decision_and_token_cost(tmp_path, quiet):
    def execute(argv, *, timeout, max_chars):
        del argv, timeout, max_chars
        return ExecResult(0, "hi\n", "", False)

    nb = start_run(
        write_goal(tmp_path),
        write_allow(tmp_path),
        tmp_path / "runs",
        model="gpt-4.1-mini",
        provider_name="openai",
        provider=StubPlanner(),
        approver=ScriptedApprover(["a"], quiet),
        executor=execute,
        console=quiet,
    )
    text = nb.path.read_text(encoding="utf-8")
    assert "echo hi" in text
    assert "hi" in text
    assert "Decision: approved" in text
    assert "Exit: 0" in text
    assert "saw hi" in text
    assert "est. $" in text
    loaded = load_notebook(nb.path)
    assert loaded.input_tokens == 114
    assert loaded.output_tokens == 59
    assert loaded.cost_usd == pytest.approx(0.013)
    assert loaded.steps[0].cost_usd == pytest.approx(0.002)
    assert loaded.steps[0].input_tokens == 10
    assert "Echo is enough." in (tmp_path / "runs" / "INDEX.md").read_text(encoding="utf-8")


def test_second_run_reads_the_index(tmp_path, quiet):
    calls = []

    def execute(argv, *, timeout, max_chars):
        calls.append(argv)
        return ExecResult(0, "hi\n", "", False)

    planner = StubPlanner()
    runs = tmp_path / "runs"
    start_run(
        write_goal(tmp_path, "First goal.\n"),
        write_allow(tmp_path),
        runs,
        model="fake",
        provider_name="fake",
        provider=planner,
        approver=ScriptedApprover(["a"], quiet),
        executor=execute,
        console=quiet,
    )
    start_run(
        write_goal(tmp_path, "Second goal about the same host.\n"),
        write_allow(tmp_path),
        runs,
        model="fake",
        provider_name="fake",
        provider=planner,
        approver=ScriptedApprover(["a"], quiet),
        executor=execute,
        console=quiet,
    )
    assert planner.plans == 2
    assert planner.indexes[0] == ""
    assert "Echo is enough." in planner.indexes[1]
    assert "Echo is enough." in planner.priors[1]


def test_resume_continues_without_replanning(tmp_path, quiet):
    calls = []

    def execute(argv, *, timeout, max_chars):
        calls.append(list(argv))
        return ExecResult(0, "ok\n", "", False)

    planner = StubPlanner(
        [Step("one", "echo one", "one"), Step("two", "echo two", "two")]
    )
    runs = tmp_path / "runs"
    nb = start_run(
        write_goal(tmp_path),
        write_allow(tmp_path),
        runs,
        model="fake",
        provider_name="fake",
        provider=planner,
        approver=ScriptedApprover(["a", "q"], quiet),
        executor=execute,
        console=quiet,
    )
    assert calls == [["echo", "one"]]
    assert nb.status == "paused"
    resumed = resume_run(
        nb.path,
        approver=ScriptedApprover(["a"], quiet),
        executor=execute,
        provider=planner,
        console=quiet,
    )
    assert planner.plans == 1
    assert calls == [["echo", "one"], ["echo", "two"]]
    assert resumed.status == "completed"
    index = (runs / "INDEX.md").read_text(encoding="utf-8")
    assert "paused" in index
    assert "completed" in index


def test_usage_import_is_real():
    # Guards against the stub drifting away from the cost fields the log uses.
    assert Usage(1, 1, 0.0).add(Usage(1, 1, None)).cost_usd is None
