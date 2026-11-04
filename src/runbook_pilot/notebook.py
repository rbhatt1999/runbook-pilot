import base64
import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

import yaml

from runbook_pilot.models import Step, Usage, format_cost, format_usage

STATE_PREFIX = "runbook-pilot-state:"


@dataclass
class Notebook:
    title: str
    slug: str
    goal: str
    goal_path: str
    model: str
    provider: str
    dry_run: bool
    allowlist_path: str
    created: str
    path: Path
    status: str = "planned"
    steps: list[Step] = field(default_factory=list)
    decisions: str = ""
    lesson: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = 0.0
    plan_input_tokens: int = 0
    plan_output_tokens: int = 0
    plan_cost_usd: float | None = 0.0
    prior_notebook: str = ""
    last_published_status: str = ""

    def add_usage(self, usage: Usage) -> None:
        total = Usage(self.input_tokens, self.output_tokens, self.cost_usd).add(usage)
        self.input_tokens = total.input_tokens
        self.output_tokens = total.output_tokens
        self.cost_usd = total.cost_usd

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(render_notebook(self), encoding="utf-8")


def render_notebook(nb: Notebook) -> str:
    front = {
        "goal": nb.title,
        "status": nb.status,
        "model": nb.model,
        "provider": nb.provider,
        "dry_run": nb.dry_run,
        "created": nb.created,
        "input_tokens": nb.input_tokens,
        "output_tokens": nb.output_tokens,
        "cost_usd": nb.cost_usd,
        "allowlist": nb.allowlist_path,
    }
    front_yaml = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    payload = base64.b64encode(json.dumps(to_state(nb), ensure_ascii=False).encode()).decode()
    return (
        f"---\n{front_yaml}\n---\n\n"
        f"{render_body(nb)}\n\n"
        f"<!-- The line below is how `resume` reloads this run. The notes above are the log. -->\n"
        f"{STATE_PREFIX}{payload}\n"
    )


def render_body(nb: Notebook) -> str:
    lines = [
        f"# {nb.title}",
        "",
        f"Status: {nb.status}",
        f"Provider: {nb.provider}",
        f"Model: {nb.model}",
        f"Dry run: {'yes' if nb.dry_run else 'no'}",
        "Planning: " + format_usage(nb.plan_input_tokens, nb.plan_output_tokens, nb.plan_cost_usd),
        "Total: " + format_usage(nb.input_tokens, nb.output_tokens, nb.cost_usd),
        f"Allowlist: `{nb.allowlist_path}`",
        f"Goal file: `{nb.goal_path}`",
    ]
    if nb.prior_notebook:
        lines.append(f"Prior notebook: `{nb.prior_notebook}`")
    lines.extend(["", "## Goal", "", _goal_body(nb.goal), "", "## Steps", ""])
    if not nb.steps:
        lines.append("No steps.")
    for index, step in enumerate(nb.steps, start=1):
        lines.extend(_render_step(index, step))
    dead = _dead_ends(nb.steps)
    if dead:
        lines.extend(["## Dead ends", "", *dead, ""])
    if nb.decisions.strip():
        lines.extend(["## Decisions and next time", "", nb.decisions.strip(), ""])
    if nb.lesson.strip():
        lines.extend(["## Lesson", "", nb.lesson.strip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def load_notebook(path: Path) -> Notebook:
    text = path.read_text(encoding="utf-8")
    encoded = ""
    for line in reversed(text.splitlines()):
        if line.startswith(STATE_PREFIX):
            encoded = line[len(STATE_PREFIX) :]
            break
    if not encoded:
        raise ValueError(f"{path} has no resume state")
    try:
        data = json.loads(base64.b64decode(encoded))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path} has unreadable resume state") from exc
    return notebook_from_state(data, path)


def to_state(nb: Notebook) -> dict:
    state = {
        "title": nb.title,
        "slug": nb.slug,
        "goal": nb.goal,
        "goal_path": nb.goal_path,
        "model": nb.model,
        "provider": nb.provider,
        "dry_run": nb.dry_run,
        "allowlist_path": nb.allowlist_path,
        "created": nb.created,
        "status": nb.status,
        "steps": [asdict(step) for step in nb.steps],
        "decisions": nb.decisions,
        "lesson": nb.lesson,
        "input_tokens": nb.input_tokens,
        "output_tokens": nb.output_tokens,
        "cost_usd": nb.cost_usd,
        "plan_input_tokens": nb.plan_input_tokens,
        "plan_output_tokens": nb.plan_output_tokens,
        "plan_cost_usd": nb.plan_cost_usd,
        "prior_notebook": nb.prior_notebook,
        "last_published_status": nb.last_published_status,
    }
    return state


def notebook_from_state(data: dict, path: Path) -> Notebook:
    steps = [step_from_dict(item) for item in data.get("steps") or []]
    known = {item.name for item in fields(Notebook)}
    kwargs = {key: value for key, value in data.items() if key in known and key != "steps"}
    kwargs["steps"] = steps
    kwargs["path"] = path
    return Notebook(**kwargs)


def step_from_dict(data: dict) -> Step:
    known = {item.name for item in fields(Step)}
    return Step(**{key: value for key, value in data.items() if key in known})


def notebook_path(runs_dir: Path, slug: str, created: datetime) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y-%m-%d")
    candidate = runs_dir / f"{stamp}-{slug}.md"
    number = 2
    while candidate.exists():
        candidate = runs_dir / f"{stamp}-{slug}-{number}.md"
        number += 1
    return candidate


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def outcome_summary(nb: Notebook) -> str:
    ran = sum(step.outcome == "ran" for step in nb.steps)
    dry = sum(step.outcome == "dry-run" for step in nb.steps)
    edited = sum(step.decision == "edited" for step in nb.steps)
    rejected = sum(step.decision == "rejected" for step in nb.steps)
    blocked = sum(step.outcome == "blocked" for step in nb.steps)
    return (
        f"{nb.status}, {ran} ran, {dry} dry-run, {edited} edited, "
        f"{rejected} rejected, {blocked} blocked"
    )


def _render_step(index: int, step: Step) -> list[str]:
    lines = [
        f"### {index}. {step.intent}",
        "",
        f"- Intent: {step.intent}",
        f"- Command: {_fmt_cmd(step.command)}",
        f"- Expected: {step.expected}",
        f"- Decision: {step.decision}",
    ]
    if step.mode:
        lines.append(f"- Allowlist: {step.mode}")
    if step.outcome == "blocked":
        lines.append(f"- Result: blocked, {step.block_reason}")
    elif step.outcome:
        lines.append(f"- Result: {step.outcome}")
    if step.alternative:
        lines.append(f"- Suggested instead: {_fmt_cmd(step.alternative)}")
    if step.exit_code is not None:
        lines.append(f"- Exit: {step.exit_code}")
    if step.stdout or step.stderr:
        lines.append(f"- Output truncated: {'yes' if step.truncated else 'no'}")
    if step.input_tokens or step.output_tokens or step.cost_usd:
        lines.append("- Step tokens: " + format_usage(step.input_tokens, step.output_tokens, step.cost_usd))
    lines.append("")
    if step.stdout:
        lines.extend(["stdout:", "", _indent(step.stdout), ""])
    if step.stderr:
        lines.extend(["stderr:", "", _indent(step.stderr), ""])
    if step.interpretation:
        lines.extend([f"Interpretation: {step.interpretation}", ""])
    return lines


def _goal_body(goal: str) -> str:
    rows = goal.strip().splitlines()
    if rows and rows[0].startswith("# "):
        rows = rows[1:]
    return "\n".join(rows).strip() or goal.strip()


def _dead_ends(steps: list[Step]) -> list[str]:
    lines = []
    for index, step in enumerate(steps, start=1):
        if step.outcome == "blocked":
            lines.append(f"- Step {index} `{step.command}` was blocked: {step.block_reason}")
        elif step.decision == "rejected":
            lines.append(f"- Step {index} `{step.command}` was rejected before it ran.")
    return lines


def _fmt_cmd(command: str) -> str:
    if "`" in command or "\n" in command:
        return "\n\n" + _indent(command) + "\n"
    return f"`{command}`"


def _indent(text: str) -> str:
    rows = text.splitlines() or [""]
    return "\n".join("    " + row for row in rows)
