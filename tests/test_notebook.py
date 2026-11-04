import pytest

from runbook_pilot.models import Step, Usage
from runbook_pilot.notebook import Notebook, load_notebook


def test_round_trip_keeps_awkward_output(tmp_path):
    path = tmp_path / "run.md"
    nb = Notebook(
        title="Disk check",
        slug="disk-check",
        goal="Look at the disk.",
        goal_path="goal.md",
        model="fake",
        provider="fake",
        dry_run=False,
        allowlist_path="allowlist.yaml",
        created="2026-11-04T18:00:00+00:00",
        path=path,
        status="completed",
        steps=[
            Step(
                intent="Show space",
                command="df -h",
                expected="a table",
                decision="approved",
                mode="read",
                outcome="ran",
                exit_code=0,
                stdout="Filesystem\n---\n/dev/root\n",
                interpretation="Root is fine.",
                input_tokens=10,
                output_tokens=4,
                cost_usd=0.002,
            )
        ],
        decisions="Leave the images.",
        lesson="Start with df.",
        input_tokens=110,
        output_tokens=40,
        cost_usd=0.013,
    )
    nb.save()
    text = path.read_text(encoding="utf-8")
    assert "cost_usd:" in text
    assert "Filesystem" in text
    assert "Root is fine." in text
    assert "Decision: approved" in text
    assert "Exit: 0" in text
    loaded = load_notebook(path)
    assert loaded.steps[0].stdout == "Filesystem\n---\n/dev/root\n"
    assert loaded.cost_usd == pytest.approx(0.013)
    assert loaded.lesson == "Start with df."
    assert loaded.status == "completed"


def test_unknown_cost_stays_unknown(tmp_path):
    path = tmp_path / "run.md"
    nb = Notebook(
        title="t",
        slug="t",
        goal="g",
        goal_path="g.md",
        model="local",
        provider="openai",
        dry_run=True,
        allowlist_path="a.yaml",
        created="2026-11-04T00:00:00+00:00",
        path=path,
    )
    nb.add_usage(Usage(3, 1, None))
    nb.save()
    loaded = load_notebook(path)
    assert loaded.cost_usd is None
    assert "unknown" in path.read_text(encoding="utf-8")
