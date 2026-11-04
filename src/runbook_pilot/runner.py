from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.table import Table
from rich.text import Text

from runbook_pilot.allowlist import Allowlist, load_allowlist
from runbook_pilot.approve import InteractiveApprover, ScriptedApprover
from runbook_pilot.executor import CommandRefused, ExecResult, run_argv
from runbook_pilot.index import (
    append_lesson,
    read_index_for_prompt,
    render_entry,
    select_prior,
)
from runbook_pilot.llm import Planner, make_provider
from runbook_pilot.models import Decision, Step, Usage
from runbook_pilot.notebook import (
    Notebook,
    display_path,
    load_notebook,
    notebook_path,
    outcome_summary,
)

Executor = Callable[..., ExecResult]


def start_run(
    goal_path: Path,
    allowlist_path: Path,
    runs_dir: Path,
    *,
    model: str,
    provider_name: str,
    provider: Planner | None = None,
    dry_run: bool = False,
    approver: InteractiveApprover | ScriptedApprover | None = None,
    executor: Executor = run_argv,
    console: Console | None = None,
) -> Notebook:
    console = console or Console(highlight=False)
    goal = goal_path.read_text(encoding="utf-8")
    allow = load_allowlist(allowlist_path)
    planner = provider if provider is not None else make_provider(provider_name, model)
    index_text = read_index_for_prompt(runs_dir)
    prior_path, prior_text = select_prior(runs_dir, goal)
    if prior_path is None:
        console.print("No earlier notebook to read.", markup=False, highlight=False)
    else:
        console.print(f"Read earlier run {display_path(prior_path)}", markup=False, highlight=False)

    steps, usage = planner.plan(
        goal=goal,
        index_text=index_text,
        prior_text=prior_text,
        allowlist_text=allow.summary(),
    )
    if not steps:
        raise RuntimeError("the planner returned an empty plan")

    created = datetime.now(timezone.utc).replace(microsecond=0)
    slug = slug_for(goal_path)
    path = notebook_path(runs_dir, slug, created)
    nb = Notebook(
        title=title_from(goal, goal_path),
        slug=slug,
        goal=goal,
        goal_path=str(goal_path),
        model=model,
        provider=provider_name,
        dry_run=dry_run,
        allowlist_path=str(allowlist_path),
        created=created.isoformat(),
        path=path,
        status="in_progress",
        steps=steps,
        plan_input_tokens=usage.input_tokens,
        plan_output_tokens=usage.output_tokens,
        plan_cost_usd=usage.cost_usd,
        prior_notebook=display_path(prior_path) if prior_path else "",
    )
    nb.add_usage(usage)
    nb.save()
    gate = approver or InteractiveApprover(console)
    return drive(nb, allow, planner, gate, executor, console)


def resume_run(
    path: Path,
    *,
    approver: InteractiveApprover | ScriptedApprover | None = None,
    executor: Executor = run_argv,
    dry_run: bool = False,
    console: Console | None = None,
    provider: Planner | None = None,
) -> Notebook:
    console = console or Console(highlight=False)
    nb = load_notebook(path)
    if nb.status == "completed":
        console.print(f"{display_path(path)} is already finished.", markup=False, highlight=False)
        return nb
    if dry_run:
        nb.dry_run = True
    allow = load_allowlist(Path(nb.allowlist_path))
    planner = provider if provider is not None else make_provider(nb.provider, nb.model)
    gate = approver or InteractiveApprover(console)
    return drive(nb, allow, planner, gate, executor, console)


def drive(
    nb: Notebook,
    allow: Allowlist,
    planner: Planner,
    approver: InteractiveApprover | ScriptedApprover,
    executor: Executor,
    console: Console,
) -> Notebook:
    _show_plan(console, nb)
    if nb.dry_run:
        console.print(
            "Dry run: approved commands are checked and logged, not executed.",
            markup=False,
            highlight=False,
        )
    index = 0
    while index < len(nb.steps):
        step = nb.steps[index]
        if step.decision not in {"", "pending"}:
            index += 1
            continue
        try:
            decision = approver.decide(step, index=index + 1, total=len(nb.steps))
        except (KeyboardInterrupt, EOFError):
            decision = Decision("quit")
        if decision.action == "quit":
            nb.status = "paused"
            if not nb.lesson:
                nb.lesson = "Paused. Remaining steps have not run."
            if not nb.decisions:
                nb.decisions = "Stopped early. Resume when you want the rest of the plan."
            nb.save()
            _publish(nb)
            console.print(
                f"Paused. Resume with: runbook-pilot resume {display_path(nb.path)}",
                markup=False,
                highlight=False,
            )
            return nb
        if decision.action == "reject":
            step.decision = "rejected"
            step.outcome = "skipped"
            nb.save()
            _say(console, "rejected", step.command)
            index += 1
            continue
        if decision.action == "edit":
            step.intent = decision.intent or step.intent
            step.command = decision.command or step.command
            step.expected = decision.expected or step.expected
            step.decision = "edited"
        else:
            step.decision = "approved"

        verdict = allow.check(step.command)
        step.mode = verdict.mode
        if not verdict.allowed:
            _block_step(nb, allow, planner, step, verdict.reason, index, console)
            index += 1
            continue
        if nb.dry_run:
            step.outcome = "dry-run"
            step.interpretation = "Dry run: the command was not executed."
            nb.save()
            _say(console, "dry-run", step.command)
            index += 1
            continue
        _execute(nb, planner, step, verdict.argv, allow, executor, console)
        index += 1

    nb.status = "completed"
    summary = _summary(nb)
    try:
        decisions, lesson, usage = planner.wrap_up(summary=summary)
    except Exception as exc:
        decisions, lesson, usage = summary, "Wrap-up failed.", Usage()
        console.print(f"Wrap-up failed: {exc}", markup=False, highlight=False)
    nb.add_usage(usage)
    decisions, lesson = approver.edit_notes(decisions, lesson)
    nb.decisions = decisions
    nb.lesson = lesson
    nb.save()
    _publish(nb)
    _say_total(console, nb)
    return nb


def slug_for(goal_path: Path) -> str:
    raw = goal_path.parent.name if goal_path.name == "goal.md" else goal_path.stem
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in raw).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return (slug or "run")[:48]


def title_from(goal: str, goal_path: Path) -> str:
    for line in goal.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or goal_path.stem
    return goal_path.stem


def _block_step(
    nb: Notebook,
    allow: Allowlist,
    planner: Planner,
    step: Step,
    reason: str,
    index: int,
    console: Console,
) -> None:
    step.outcome = "blocked"
    step.block_reason = reason
    _say(console, "blocked", f"{step.command} ({reason})")
    usage = Usage()
    alt = None
    if not step.from_alternative:
        try:
            alt, usage = planner.alternative(
                step=step,
                reason=reason,
                allowlist_text=allow.summary(),
            )
        except Exception as exc:
            alt = None
            console.print(f"Could not get an alternative: {exc}", markup=False, highlight=False)
    step.input_tokens += usage.input_tokens
    step.output_tokens += usage.output_tokens
    step.cost_usd = _add_cost(step.cost_usd, usage.cost_usd)
    if alt is not None and alt.command.strip() and alt.command.strip() != step.command:
        step.alternative = alt.command
        nb.steps.insert(
            index + 1,
            Step(
                intent=alt.intent,
                command=alt.command,
                expected=alt.expected,
                from_alternative=True,
            ),
        )
    nb.add_usage(usage)
    nb.save()


def _execute(
    nb: Notebook,
    planner: Planner,
    step: Step,
    argv: list[str],
    allow: Allowlist,
    executor: Executor,
    console: Console,
) -> None:
    step.outcome = "running"
    nb.save()
    try:
        result = executor(argv, timeout=allow.timeout_seconds, max_chars=allow.max_output_chars)
    except CommandRefused as exc:
        step.outcome = "blocked"
        step.block_reason = str(exc)
        nb.save()
        _say(console, "blocked", f"{step.command} ({exc})")
        return
    except Exception as exc:
        step.outcome = "error"
        step.interpretation = f"Failed before the command finished: {exc}"
        nb.save()
        _say(console, "error", str(exc))
        return
    step.exit_code = result.exit_code
    step.stdout = result.stdout
    step.stderr = result.stderr
    step.truncated = result.truncated
    step.outcome = "ran" if result.exit_code == 0 else "error"
    try:
        text, usage = planner.interpret(step=step)
    except Exception as exc:
        text, usage = f"Interpretation failed: {exc}", Usage()
    step.interpretation = text
    step.input_tokens += usage.input_tokens
    step.output_tokens += usage.output_tokens
    step.cost_usd = _add_cost(step.cost_usd, usage.cost_usd)
    nb.add_usage(usage)
    nb.save()
    _say(console, "ran", f"{step.command} (exit {result.exit_code})")


def _publish(nb: Notebook) -> None:
    if nb.last_published_status == nb.status:
        return
    append_lesson(
        nb.path.parent,
        created=nb.created,
        slug=nb.slug,
        notebook=display_path(nb.path),
        outcome=outcome_summary(nb),
        lesson=nb.lesson,
    )
    nb.last_published_status = nb.status
    nb.save()


def _summary(nb: Notebook) -> str:
    lines = [outcome_summary(nb)]
    if nb.prior_notebook:
        lines.append(f"Prior notebook read: {nb.prior_notebook}")
    else:
        lines.append("No prior notebook.")
    lines.append(
        f"Tokens: {nb.input_tokens} in / {nb.output_tokens} out, est. cost {nb.cost_usd}."
    )
    for index, step in enumerate(nb.steps, start=1):
        lines.append(
            f"{index}. decision={step.decision} outcome={step.outcome or 'n/a'} command={step.command}"
        )
        if step.block_reason:
            lines.append(f"   blocked: {step.block_reason}")
        if step.interpretation:
            lines.append(f"   note: {step.interpretation}")
    return "\n".join(lines)


def _show_plan(console: Console, nb: Notebook) -> None:
    table = Table(title=Text(nb.title), show_lines=False)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Intent")
    table.add_column("Command")
    for index, step in enumerate(nb.steps, start=1):
        table.add_row(str(index), Text(step.intent), Text(step.command))
    console.print(table)
    shown = display_path(nb.path)
    console.print(f"Notebook: {shown}", markup=False, highlight=False)


def _say(console: Console, kind: str, detail: str) -> None:
    styles = {
        "ran": "green",
        "dry-run": "yellow",
        "blocked": "red",
        "rejected": "yellow",
        "error": "red",
    }
    line = Text()
    line.append(kind, style=styles.get(kind, "white"))
    line.append(" ")
    line.append(detail)
    console.print(line)


def _say_total(console: Console, nb: Notebook) -> None:
    cost = "unknown" if nb.cost_usd is None else f"${nb.cost_usd:.6f}"
    console.print(
        f"Saved {display_path(nb.path)}. Tokens: {nb.input_tokens} in / {nb.output_tokens} out. Estimated cost: {cost}",
        markup=False,
        highlight=False,
    )


def _add_cost(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left + right
