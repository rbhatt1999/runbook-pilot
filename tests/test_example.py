from pathlib import Path

from runbook_pilot.allowlist import load_allowlist
from runbook_pilot.llm import FakeProvider

ROOT = Path(__file__).resolve().parents[1]


def test_sample_allowlist_matches_the_offline_plan():
    allow = load_allowlist(ROOT / "examples/docker-hygiene/allowlist.yaml")
    goal = (ROOT / "examples/docker-hygiene/goal.md").read_text(encoding="utf-8")
    steps, _usage = FakeProvider().plan(
        goal=goal,
        index_text="",
        prior_text="",
        allowlist_text=allow.summary(),
    )
    verdicts = [allow.check(step.command) for step in steps]
    assert [item.allowed for item in verdicts] == [True, True, True, False]
    assert verdicts[0].mode == "read"
    assert "rm -rf" in verdicts[-1].reason
    prune = allow.check("docker image prune -f")
    assert prune.allowed and prune.mode == "write"
