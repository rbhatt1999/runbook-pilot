import re
from pathlib import Path

HEADER = """\
# Run index

The pilot reads this file before it writes a plan. Newest runs are at the bottom.
"""

_WORD = re.compile(r"[a-z0-9]{4,}")


def read_index_for_prompt(runs_dir: Path, limit: int = 8000) -> str:
    path = runs_dir / "INDEX.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if len(text) <= limit:
        return text
    return "[earlier entries truncated]\n" + text[-limit:]


def append_lesson(
    runs_dir: Path,
    *,
    created: str,
    slug: str,
    notebook: str,
    outcome: str,
    lesson: str,
) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / "INDEX.md"
    entry = render_entry(
        created=created,
        slug=slug,
        notebook=notebook,
        outcome=outcome,
        lesson=lesson,
    )
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if not existing.strip():
        path.write_text(HEADER.rstrip() + "\n\n" + entry, encoding="utf-8")
        return path
    if not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + "\n" + entry, encoding="utf-8")
    return path


def render_entry(*, created: str, slug: str, notebook: str, outcome: str, lesson: str) -> str:
    date = created[:10]
    one_line = " ".join(lesson.split()) or "No lesson recorded."
    if len(one_line) > 280:
        one_line = one_line[:277].rstrip() + "..."
    return (
        f"### {date} {slug}\n"
        f"- Notebook: `{notebook}`\n"
        f"- Outcome: {outcome}\n"
        f"- Lesson: {one_line}\n"
    )


def select_prior(
    runs_dir: Path,
    goal: str,
    *,
    exclude: Path | None = None,
    limit: int = 6000,
) -> tuple[Path | None, str]:
    if not runs_dir.exists():
        return None, ""
    goal_words = _words(goal)
    excluded = exclude.resolve() if exclude else None
    candidates = []
    for path in runs_dir.glob("*.md"):
        if path.name == "INDEX.md":
            continue
        if excluded and path.resolve() == excluded:
            continue
        candidates.append(path)
    if not candidates:
        return None, ""

    def score(path: Path) -> tuple[int, float]:
        text = path.read_text(encoding="utf-8", errors="replace")
        return (len(goal_words & _words(text[:8000])), path.stat().st_mtime)

    best = max(candidates, key=score)
    text = best.read_text(encoding="utf-8", errors="replace")
    if len(text) > limit:
        text = text[:limit] + "\n\n[truncated]\n"
    return best, text


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))
