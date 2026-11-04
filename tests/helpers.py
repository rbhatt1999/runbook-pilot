from dataclasses import replace
from pathlib import Path

from runbook_pilot.approve import ScriptedApprover
from runbook_pilot.models import Step, Usage

ECHO_ALLOW = """\
timeout_seconds: 5
max_output_chars: 2000
never: []
allow:
  - bin: echo
    args_regex: '^.+$'
    mode: read
"""


def write_allow(directory: Path, text: str = ECHO_ALLOW) -> Path:
    path = directory / "allowlist.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def write_goal(directory: Path, text: str = "Say hello.\n") -> Path:
    path = directory / "goal.md"
    path.write_text(text, encoding="utf-8")
    return path


class StubPlanner:
    def __init__(self, steps: list[Step] | None = None):
        self.steps = steps or [Step("say hi", "echo hi", "hi")]
        self.plans = 0
        self.indexes: list[str] = []
        self.priors: list[str] = []
        self.plan_usage = Usage(100, 50, 0.01)
        self.interpret_usage = Usage(10, 5, 0.002)
        self.wrap_usage = Usage(4, 4, 0.001)
        self.lesson = "Echo is enough."
        self.decisions = "Kept the greeting."
        self.alternative: Step | None = None

    def plan(self, *, goal: str, index_text: str, prior_text: str, allowlist_text: str):
        del goal, allowlist_text
        self.plans += 1
        self.indexes.append(index_text)
        self.priors.append(prior_text)
        return [replace(step) for step in self.steps], self.plan_usage

    def alternative(self, *, step: Step, reason: str, allowlist_text: str):
        del step, reason, allowlist_text
        return self.alternative, Usage()

    def interpret(self, *, step: Step):
        detail = (step.stdout or "").strip()
        return f"saw {detail}", self.interpret_usage

    def wrap_up(self, *, summary: str):
        del summary
        return self.decisions, self.lesson, self.wrap_usage


def gate(actions: list[str], console) -> ScriptedApprover:
    return ScriptedApprover(actions, console)
