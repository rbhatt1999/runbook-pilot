from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from runbook_pilot import __version__
from runbook_pilot.approve import InteractiveApprover, ScriptedApprover, scripted_actions
from runbook_pilot.llm import resolve_model, resolve_provider_name
from runbook_pilot.runner import resume_run, start_run

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Plan a run, wait for approval, and run only allow-listed commands.",
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Plan a run, wait for approval, and run only allow-listed commands."""


@app.command()
def run(
    goal: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="Markdown file with the goal."),
    allowlist: Path = typer.Option(
        ...,
        "--allowlist",
        "-a",
        exists=True,
        dir_okay=False,
        readable=True,
        help="YAML file of commands that may run.",
    ),
    runs_dir: Path = typer.Option(Path("runs"), "--runs-dir", help="Directory for notebooks and INDEX.md."),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="OpenAI model. Defaults to $RUNBOOK_PILOT_MODEL or gpt-4.1-mini.",
    ),
    provider: str = typer.Option(
        "auto",
        "--provider",
        help="auto, openai, or fake. auto uses openai when OPENAI_API_KEY is set.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Walk the approval prompts and log commands without running them.",
    ),
    decisions: Optional[str] = typer.Option(
        None,
        "--decisions",
        help="Comma-separated decisions for scripts: a, r, q (approve, reject, quit).",
    ),
) -> None:
    """Read past runs, draft a plan, and execute the steps you approve."""
    console = Console(highlight=False)
    try:
        provider_name, note = resolve_provider_name(provider)
    except ValueError as exc:
        console.print(str(exc), markup=False, highlight=False)
        raise typer.Exit(1)
    if note:
        console.print(note, markup=False, highlight=False)
    chosen = resolve_model(model)
    gate = _approver(decisions, console)
    try:
        start_run(
            goal,
            allowlist,
            runs_dir,
            model=chosen,
            provider_name=provider_name,
            dry_run=dry_run,
            approver=gate,
            console=console,
        )
    except (RuntimeError, ValueError, OSError) as exc:
        console.print(str(exc), markup=False, highlight=False)
        raise typer.Exit(1)


@app.command()
def resume(
    notebook: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="Notebook from an earlier run."),
    decisions: Optional[str] = typer.Option(None, "--decisions", help="Comma-separated a, r, q decisions."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Do not execute the remaining commands."),
) -> None:
    """Continue a run that was paused."""
    console = Console(highlight=False)
    gate = _approver(decisions, console)
    try:
        resume_run(notebook, approver=gate, dry_run=dry_run, console=console)
    except (RuntimeError, ValueError, OSError) as exc:
        console.print(str(exc), markup=False, highlight=False)
        raise typer.Exit(1)


def _approver(decisions: str | None, console: Console) -> InteractiveApprover | ScriptedApprover:
    if decisions is None:
        return InteractiveApprover(console)
    return ScriptedApprover(scripted_actions(decisions), console)
