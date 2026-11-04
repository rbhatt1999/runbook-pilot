import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

DEFAULT_TIMEOUT = 20
DEFAULT_MAX_OUTPUT = 4000

# Checked on the raw command string. shlex does not treat these as syntax,
# and we never hand the string to a shell, but they still mean the command
# is not the single argv the operator thinks they approved.
_SHELL = re.compile(r"(\|\||&&|[|&;<>`]|\$\(|\$\{)")


@dataclass
class Rule:
    binary: str
    args_prefix: list[str] | None
    args_regex: str | None
    pattern: re.Pattern[str] | None
    mode: str


@dataclass
class Verdict:
    allowed: bool
    argv: list[str]
    mode: str
    reason: str


@dataclass
class Allowlist:
    timeout_seconds: int = DEFAULT_TIMEOUT
    max_output_chars: int = DEFAULT_MAX_OUTPUT
    never: list[list[str]] = field(default_factory=list)
    allow: list[Rule] = field(default_factory=list)
    path: str = ""

    def summary(self) -> str:
        lines = [
            f"timeout_seconds: {self.timeout_seconds}",
            f"max_output_chars: {self.max_output_chars}",
            "never:",
        ]
        if not self.never:
            lines.append("  (none listed)")
        for pattern in self.never:
            lines.append("  - " + " ".join(pattern))
        lines.append("allow:")
        if not self.allow:
            lines.append("  (nothing)")
        for rule in self.allow:
            if rule.args_prefix is not None and rule.args_regex is not None:
                detail = f"prefix={' '.join(rule.args_prefix)} regex={rule.args_regex}"
            elif rule.args_prefix is not None:
                shown = " ".join(rule.args_prefix) or "(any arguments)"
                detail = f"prefix={shown}"
            elif rule.args_regex is not None:
                detail = f"args_regex={rule.args_regex}"
            else:
                detail = "no arguments"
            lines.append(f"  - {rule.binary} [{rule.mode}] {detail}")
        lines.append("rm -rf is never run, even if a rule would allow it.")
        lines.append("No pipes, redirects, globs, or command substitution.")
        return "\n".join(lines)

    def check(self, command: str) -> Verdict:
        if "\n" in command or "\r" in command:
            return _block("commands must be a single line")
        if _SHELL.search(command):
            return _block("shell syntax is not allowed; commands run without a shell")
        try:
            argv = shlex.split(command, posix=True)
        except ValueError as exc:
            return _block(f"could not parse the command: {exc}")
        if not argv:
            return _block("empty command")
        if is_rm_rf(argv):
            return _block("rm -rf is never allowed")
        for pattern in self.never:
            if pattern and argv[: len(pattern)] == pattern:
                return _block("command is on the never list")
        if "/" in argv[0] or argv[0] in {".", ".."}:
            return _block("use the program name on PATH, not a path")
        args = argv[1:]
        for rule in self.allow:
            if argv[0] != rule.binary:
                continue
            if _rule_matches(rule, args):
                return Verdict(True, argv, rule.mode, "allowed")
        return _block(f"`{command}` does not match the allowlist")


def is_rm_rf(argv: list[str]) -> bool:
    """Recursive force-delete. The allowlist cannot turn this back on."""
    if not argv or PurePosixPath(argv[0]).name != "rm":
        return False
    recursive = False
    force = False
    for arg in argv[1:]:
        if arg == "--":
            break
        if arg == "--recursive":
            recursive = True
            continue
        if arg == "--force":
            force = True
            continue
        if arg.startswith("-") and not arg.startswith("--"):
            flags = arg[1:]
            if "r" in flags or "R" in flags:
                recursive = True
            if "f" in flags:
                force = True
    return recursive and force


def load_allowlist(path: Path) -> Allowlist:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} should be a YAML mapping")
    timeout = int(raw.get("timeout_seconds", DEFAULT_TIMEOUT))
    max_output = int(raw.get("max_output_chars", DEFAULT_MAX_OUTPUT))
    if timeout <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_output < 0:
        raise ValueError("max_output_chars cannot be negative")

    never: list[list[str]] = []
    for item in raw.get("never") or []:
        if not isinstance(item, list) or not item or not all(isinstance(part, str) for part in item):
            raise ValueError(f"never entries must be lists of strings, got {item!r}")
        never.append(list(item))

    allow: list[Rule] = []
    for item in raw.get("allow") or []:
        if not isinstance(item, dict) or not isinstance(item.get("bin"), str) or not item["bin"]:
            raise ValueError(
                f"allow entries need a bin as a quoted string, got {item!r}. "
                "YAML turns words like true, yes, and no into booleans."
            )
        mode = str(item.get("mode", "read"))
        if mode not in {"read", "write"}:
            raise ValueError(f"mode for {item['bin']} must be read or write")
        prefix = item.get("args_prefix")
        if prefix is not None:
            if not isinstance(prefix, list) or not all(isinstance(part, str) for part in prefix):
                raise ValueError(f"args_prefix for {item['bin']} must be a list of strings")
            prefix = list(prefix)
        regex = item.get("args_regex")
        pattern = None
        if regex is not None:
            regex = str(regex)
            try:
                pattern = re.compile(regex)
            except re.error as exc:
                raise ValueError(f"bad args_regex for {item['bin']}: {exc}") from exc
        allow.append(
            Rule(
                binary=str(item["bin"]),
                args_prefix=prefix,
                args_regex=regex,
                pattern=pattern,
                mode=mode,
            )
        )
    return Allowlist(
        timeout_seconds=timeout,
        max_output_chars=max_output,
        never=never,
        allow=allow,
        path=str(path),
    )


def _rule_matches(rule: Rule, args: list[str]) -> bool:
    if rule.args_prefix is None and rule.pattern is None:
        return args == []
    if rule.args_prefix is not None and args[: len(rule.args_prefix)] != rule.args_prefix:
        return False
    if rule.pattern is not None and rule.pattern.fullmatch(" ".join(args)) is None:
        return False
    return True


def _block(reason: str) -> Verdict:
    return Verdict(False, [], "", reason)
