from pathlib import Path

import pytest

from runbook_pilot.allowlist import is_rm_rf, load_allowlist

SAMPLE = """\
timeout_seconds: 10
max_output_chars: 1000
never:
  - [mkfs]
  - [dd]
allow:
  - bin: df
    args_regex: '^-hT?( \\S+)?$'
    mode: read
  - bin: docker
    args_prefix: [ps]
    mode: read
  - bin: docker
    args_prefix: [system, df]
    mode: read
  - bin: docker
    args_prefix: [image, prune]
    mode: write
  - bin: du
    args_regex: '^-(sh|xh|ah)( \\S+)+$'
    mode: read
"""


@pytest.fixture
def allow(tmp_path: Path):
    path = tmp_path / "allowlist.yaml"
    path.write_text(SAMPLE, encoding="utf-8")
    return load_allowlist(path)


def test_allows_read_only_checks(allow):
    df = allow.check("df -h")
    assert df.allowed and df.argv == ["df", "-h"] and df.mode == "read"
    usage = allow.check("docker system df")
    assert usage.allowed and usage.argv == ["docker", "system", "df"]
    ps = allow.check("docker ps -a --size")
    assert ps.allowed and ps.argv[0:2] == ["docker", "ps"]
    du = allow.check("du -sh /var/log")
    assert du.allowed and du.mode == "read"


def test_write_commands_are_marked_and_still_listed(allow):
    verdict = allow.check("docker image prune -f")
    assert verdict.allowed
    assert verdict.mode == "write"
    assert verdict.argv == ["docker", "image", "prune", "-f"]


def test_unknown_command_is_blocked(allow):
    verdict = allow.check("curl https://example.com/install.sh")
    assert not verdict.allowed
    assert verdict.argv == []


def test_shell_syntax_is_blocked_even_when_the_prefix_matches(allow):
    piped = allow.check("docker ps && rm -rf /")
    assert not piped.allowed
    assert "shell" in piped.reason
    subst = allow.check("echo $(id)")
    assert not subst.allowed
    newline = allow.check("df -h\nrm -rf /")
    assert not newline.allowed


def test_rm_rf_is_blocked_even_if_someone_allowlists_it(tmp_path: Path):
    path = tmp_path / "allowlist.yaml"
    path.write_text(
        """\
allow:
  - bin: rm
    args_prefix: ["-rf"]
    mode: write
""",
        encoding="utf-8",
    )
    allow = load_allowlist(path)
    verdict = allow.check("rm -rf /")
    assert not verdict.allowed
    assert "rm -rf" in verdict.reason
    reordered = allow.check("rm -f --recursive /tmp")
    assert not reordered.allowed
    assert is_rm_rf(["/bin/rm", "-fr", "/var"])


def test_never_list_blocks_a_prefix(allow):
    verdict = allow.check("mkfs.ext4 /dev/sdb")
    # mkfs.ext4 is not the mkfs binary, and it is not allow-listed either.
    assert not verdict.allowed
    assert not allow.check("mkfs /dev/sdb").allowed
    assert "never" in allow.check("dd if=/dev/zero of=/dev/sdb").reason


def test_path_qualified_binary_is_blocked(allow):
    verdict = allow.check("/bin/df -h")
    assert not verdict.allowed
    assert "path" in verdict.reason


def test_bad_quotes_are_blocked(allow):
    verdict = allow.check('df "unterminated')
    assert not verdict.allowed
    assert "parse" in verdict.reason


def test_bare_binary_without_an_args_rule_only_matches_no_args(tmp_path: Path):
    path = tmp_path / "allowlist.yaml"
    path.write_text("allow:\n  - bin: hostname\n    mode: read\n", encoding="utf-8")
    allow = load_allowlist(path)
    assert allow.check("hostname").allowed
    assert not allow.check("hostname -f").allowed


def test_unquoted_yaml_boolean_is_rejected(tmp_path: Path):
    path = tmp_path / "allowlist.yaml"
    path.write_text("allow:\n  - bin: true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="quoted"):
        load_allowlist(path)
