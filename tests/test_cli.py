import re
from pathlib import Path

from typer.testing import CliRunner

from runbook_pilot.cli import app
from runbook_pilot.notebook import load_notebook

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()
# On GitHub Actions, Typer forces a color terminal and Rich styles each
# piece of a long option separately, so "--dry-run" is not one substring.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def test_help_lists_the_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    help_text = _plain(result.stdout)
    assert "run" in help_text
    assert "resume" in help_text
    version = runner.invoke(app, ["--version"])
    assert version.exit_code == 0
    assert "0.1.0" in _plain(version.stdout)
    run_help = runner.invoke(app, ["run", "--help"])
    assert run_help.exit_code == 0
    run_text = _plain(run_help.stdout)
    assert "--dry-run" in run_text
    assert "--allowlist" in run_text
    assert "--provider" in run_text


def test_dry_run_with_the_fake_provider(tmp_path):
    runs = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "run",
            str(ROOT / "examples/docker-hygiene/goal.md"),
            "--allowlist",
            str(ROOT / "examples/docker-hygiene/allowlist.yaml"),
            "--runs-dir",
            str(runs),
            "--provider",
            "fake",
            "--dry-run",
            "--decisions",
            "a,a,a,a,r",
        ],
    )
    assert result.exit_code == 0, result.stdout
    notebooks = list(runs.glob("*.md"))
    notebooks = [path for path in notebooks if path.name != "INDEX.md"]
    assert len(notebooks) == 1
    nb = load_notebook(notebooks[0])
    assert nb.dry_run
    assert nb.steps[0].outcome == "dry-run"
    blocked = [step for step in nb.steps if step.outcome == "blocked"]
    assert blocked and blocked[0].command.startswith("rm -rf")
    assert any(step.decision == "rejected" for step in nb.steps)
    index = (runs / "INDEX.md").read_text(encoding="utf-8")
    assert "Lesson:" in index
    assert "blocked" in index
