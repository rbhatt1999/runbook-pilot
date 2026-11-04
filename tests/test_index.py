from runbook_pilot.index import append_lesson, read_index_for_prompt, render_entry, select_prior


def test_append_keeps_older_entries(tmp_path):
    append_lesson(
        tmp_path,
        created="2026-11-03T00:00:00+00:00",
        slug="first",
        notebook="runs/first.md",
        outcome="completed, 1 ran, 0 dry-run, 0 edited, 0 rejected, 0 blocked",
        lesson="Check inodes.",
    )
    append_lesson(
        tmp_path,
        created="2026-11-04T00:00:00+00:00",
        slug="second",
        notebook="runs/second.md",
        outcome="completed, 0 ran, 0 dry-run, 0 edited, 1 rejected, 0 blocked",
        lesson="Do not prune tagged images.",
    )
    text = (tmp_path / "INDEX.md").read_text(encoding="utf-8")
    assert text.count("# Run index") == 1
    assert "Check inodes." in text
    assert "Do not prune tagged images." in text
    assert text.index("first") < text.index("second")
    entry = render_entry(
        created="2026-11-04T00:00:00+00:00",
        slug="second",
        notebook="runs/second.md",
        outcome="completed",
        lesson="line one\nline two",
    )
    assert "line one line two" in entry
    assert entry.count("\n- ") == 3


def test_prompt_index_keeps_the_tail(tmp_path):
    path = tmp_path / "INDEX.md"
    path.write_text(("old\n" * 100) + "RECENT LESSON\n", encoding="utf-8")
    text = read_index_for_prompt(tmp_path, limit=50)
    assert "RECENT LESSON" in text
    assert text.startswith("[earlier entries truncated]")


def test_select_prior_prefers_the_overlapping_notebook(tmp_path):
    (tmp_path / "old.md").write_text(
        "inode exhaustion was the real problem on the mail volume\n",
        encoding="utf-8",
    )
    (tmp_path / "new.md").write_text("unrelated notes about breakfast\n", encoding="utf-8")
    # Make new.md newer so a tie would pick it. Overlap should still win.
    path, text = select_prior(tmp_path, "the mail volume is full of inodes again")
    assert path is not None
    assert path.name == "old.md"
    assert "inode" in text
