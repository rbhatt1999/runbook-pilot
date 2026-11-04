import json
import os
import re
from typing import Any, Protocol

from runbook_pilot.models import Step, Usage
from runbook_pilot.pricing import estimate_cost

MAX_STEPS = 8

# Fixed token counts for the offline planner. Cost stays 0 because nothing
# was billed. They are here so the log path is the same as a real call.
FAKE_PLAN_USAGE = Usage(200, 80, 0.0)
FAKE_ALT_USAGE = Usage(15, 10, 0.0)
FAKE_WRAP_USAGE = Usage(40, 20, 0.0)

PLAN_INSTRUCTIONS = """\
You plan operational runbooks. You do not run commands.
Return at most 8 steps. Each command is one program plus its arguments:
no pipes, redirects, globs, command substitution, or newlines.
Prefer commands from the allowlist. Prefer read-only checks.
Suggest a write only when the goal asks for cleanup, and put it last.
Never plan rm -rf, mkfs, dd, shutdown, or reboot.
If the index records a decision, follow it unless the goal says otherwise.
"""

ALT_INSTRUCTIONS = """\
The previous command was refused. Call propose_command with one safer
alternative that matches the allowlist. Prefer a read-only command.
If nothing in the allowlist would help, still call the tool with the
closest read-only check and say so in the intent.
"""

INTERPRET_INSTRUCTIONS = """\
In one or two sentences, say what this output means for the goal.
Use only the output you were given. Do not propose a new command.
"""

WRAP_INSTRUCTIONS = """\
Write the closing notes for this run from the events below.
decisions: short markdown. What was chosen, what was refused, and what
to do differently next time. Do not invent counts or timings.
lesson: one sentence, no line breaks, for the index the next run will read.
"""

PROPOSE_COMMAND = {
    "type": "function",
    "name": "propose_command",
    "description": "Propose one safer command after the previous one was refused.",
    "strict": True,
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "intent": {"type": "string"},
            "command": {"type": "string"},
            "expected": {"type": "string"},
        },
        "required": ["intent", "command", "expected"],
    },
}

_STEP = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent": {"type": "string"},
        "command": {"type": "string"},
        "expected": {"type": "string"},
    },
    "required": ["intent", "command", "expected"],
}

PLAN_FORMAT = {
    "type": "json_schema",
    "name": "runbook_plan",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"steps": {"type": "array", "items": _STEP}},
        "required": ["steps"],
    },
}

WRAP_FORMAT = {
    "type": "json_schema",
    "name": "runbook_wrap",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decisions": {"type": "string"},
            "lesson": {"type": "string"},
        },
        "required": ["decisions", "lesson"],
    },
}


class Planner(Protocol):
    def plan(
        self,
        *,
        goal: str,
        index_text: str,
        prior_text: str,
        allowlist_text: str,
    ) -> tuple[list[Step], Usage]: ...

    def alternative(
        self,
        *,
        step: Step,
        reason: str,
        allowlist_text: str,
    ) -> tuple[Step | None, Usage]: ...

    def interpret(self, *, step: Step) -> tuple[str, Usage]: ...

    def wrap_up(self, *, summary: str) -> tuple[str, str, Usage]: ...


class FakeProvider:
    """Canned plans for tests and for trying the loop with no API key.

    It only glances at the goal to pick which canned plan to return.
    """

    def plan(
        self,
        *,
        goal: str,
        index_text: str,
        prior_text: str,
        allowlist_text: str,
    ) -> tuple[list[Step], Usage]:
        del index_text, prior_text, allowlist_text
        if "docker" in goal.lower():
            steps = [
                Step(
                    "Show free disk space",
                    "df -h",
                    "a table that includes the root filesystem",
                ),
                Step(
                    "Show how much disk Docker is using",
                    "docker system df",
                    "sizes for images, containers, and volumes",
                ),
                Step(
                    "List containers and their writable size",
                    "docker ps -a --size",
                    "each container and its size",
                ),
                Step(
                    "Delete Docker's data directory",
                    "rm -rf /var/lib/docker",
                    "this should be refused",
                ),
            ]
        else:
            steps = [
                Step("Show free disk space", "df -h", "a filesystem table"),
                Step("Show the current user", "whoami", "a user name"),
                Step(
                    "Remove a temp path",
                    "rm -rf /tmp/runbook-pilot-blocked",
                    "this should be refused",
                ),
            ]
        return steps, FAKE_PLAN_USAGE

    def alternative(
        self,
        *,
        step: Step,
        reason: str,
        allowlist_text: str,
    ) -> tuple[Step | None, Usage]:
        del reason, allowlist_text
        if step.command.startswith("df"):
            return None, Usage()
        return (
            Step(
                "Fall back to a read-only disk check",
                "df -h",
                "a table that includes the root filesystem",
            ),
            FAKE_ALT_USAGE,
        )

    def interpret(self, *, step: Step) -> tuple[str, Usage]:
        if step.exit_code == 0:
            first = (step.stdout or "").strip().splitlines()
            detail = first[0][:160] if first else "no stdout"
            text = f"Finished cleanly. First line: {detail}"
        else:
            text = f"Exited {step.exit_code}."
        return text, Usage()

    def wrap_up(self, *, summary: str) -> tuple[str, str, Usage]:
        first = summary.strip().splitlines()[0] if summary.strip() else "Nothing ran."
        decisions = (
            "Written by the offline planner, so this is only a count. "
            "With the API on, this is the section I would edit before it is saved.\n\n"
            f"{first}\n\n"
            "Anything that deletes data still needs a person."
        )
        return decisions, f"Offline run ({first})."[:240], FAKE_WRAP_USAGE


class OpenAIProvider:
    def __init__(self, model: str, client: Any = None):
        self.model = model
        if client is None:
            if not os.environ.get("OPENAI_API_KEY"):
                raise RuntimeError(
                    "OPENAI_API_KEY is not set. Use --provider fake to run offline."
                )
            from openai import OpenAI

            client = OpenAI(timeout=90)
        self.client = client

    def plan(
        self,
        *,
        goal: str,
        index_text: str,
        prior_text: str,
        allowlist_text: str,
    ) -> tuple[list[Step], Usage]:
        response = self._create(
            instructions=PLAN_INSTRUCTIONS,
            input=_context(goal, index_text, prior_text, allowlist_text),
            text={"format": PLAN_FORMAT},
            max_output_tokens=2000,
        )
        payload = _parse_json(getattr(response, "output_text", "") or "")
        return steps_from_plan(payload), _usage(response, self.model)

    def alternative(
        self,
        *,
        step: Step,
        reason: str,
        allowlist_text: str,
    ) -> tuple[Step | None, Usage]:
        prompt = (
            f"Blocked command: {step.command}\n"
            f"Reason: {reason}\n"
            f"Intent was: {step.intent}\n\n"
            f"Allowlist:\n{allowlist_text}"
        )
        response = self._create(
            instructions=ALT_INSTRUCTIONS,
            input=prompt,
            tools=[PROPOSE_COMMAND],
            tool_choice={"type": "function", "name": "propose_command"},
            max_output_tokens=400,
        )
        found = steps_from_tool_calls(response)
        return (found[0] if found else None), _usage(response, self.model)

    def interpret(self, *, step: Step) -> tuple[str, Usage]:
        prompt = (
            f"Goal step: {step.intent}\n"
            f"Command: {step.command}\n"
            f"Expected: {step.expected}\n"
            f"Exit: {step.exit_code}\n"
            f"stdout:\n{step.stdout[:3000]}\n"
            f"stderr:\n{step.stderr[:1000]}"
        )
        response = self._create(
            instructions=INTERPRET_INSTRUCTIONS,
            input=prompt,
            max_output_tokens=300,
        )
        text = (getattr(response, "output_text", "") or "").strip()
        if not text:
            text = "No interpretation returned."
        return text, _usage(response, self.model)

    def wrap_up(self, *, summary: str) -> tuple[str, str, Usage]:
        response = self._create(
            instructions=WRAP_INSTRUCTIONS,
            input=summary,
            text={"format": WRAP_FORMAT},
            max_output_tokens=800,
        )
        payload = _parse_json(getattr(response, "output_text", "") or "")
        decisions = str(payload.get("decisions", "")).strip() or summary
        lesson = " ".join(str(payload.get("lesson", "")).split()) or "Run finished."
        return decisions, lesson[:280], _usage(response, self.model)

    def _create(self, **kwargs: Any) -> Any:
        try:
            # store=False: command output should not be kept on OpenAI's side.
            return self.client.responses.create(model=self.model, store=False, **kwargs)
        except Exception as exc:
            raise RuntimeError(f"OpenAI request failed: {exc}") from exc


def make_provider(name: str, model: str) -> FakeProvider | OpenAIProvider:
    if name == "fake":
        return FakeProvider()
    if name == "openai":
        return OpenAIProvider(model)
    raise ValueError(f"unknown provider {name!r}")


def resolve_model(model: str | None) -> str:
    return model or os.environ.get("RUNBOOK_PILOT_MODEL") or "gpt-4.1-mini"


def resolve_provider_name(requested: str) -> tuple[str, str]:
    name = requested.lower().strip()
    if name == "auto":
        if os.environ.get("OPENAI_API_KEY"):
            return "openai", ""
        return "fake", "OPENAI_API_KEY is not set, so this run uses the offline planner."
    if name in {"fake", "openai"}:
        return name, ""
    raise ValueError("provider must be auto, openai, or fake")


def steps_from_plan(payload: dict) -> list[Step]:
    steps = []
    for item in payload.get("steps") or []:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command", "")).strip()
        intent = str(item.get("intent", "")).strip()
        expected = str(item.get("expected", "")).strip()
        if not command or not intent:
            continue
        steps.append(Step(intent=intent, command=command, expected=expected or "see the output"))
    return steps[:MAX_STEPS]


def steps_from_tool_calls(response: Any) -> list[Step]:
    steps = []
    for item in getattr(response, "output", None) or []:
        if isinstance(item, dict):
            kind = item.get("type")
            name = item.get("name")
            raw = item.get("arguments")
        else:
            kind = getattr(item, "type", None)
            name = getattr(item, "name", None)
            raw = getattr(item, "arguments", None)
        if kind != "function_call" or name != "propose_command" or not raw:
            continue
        data = json.loads(raw)
        command = str(data.get("command", "")).strip()
        intent = str(data.get("intent", "")).strip()
        if not command or not intent:
            continue
        steps.append(
            Step(
                intent=intent,
                command=command,
                expected=str(data.get("expected", "")).strip() or "see the output",
            )
        )
    return steps


def _usage(response: Any, model: str) -> Usage:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    incoming = _token(usage, "input_tokens", "prompt_tokens")
    outgoing = _token(usage, "output_tokens", "completion_tokens")
    return Usage(incoming, outgoing, estimate_cost(model, incoming, outgoing))


def _token(usage: Any, *names: str) -> int:
    if usage is None:
        return 0
    for name in names:
        if isinstance(usage, dict) and usage.get(name) is not None:
            return int(usage[name])
        value = getattr(usage, name, None)
        if value is not None:
            return int(value)
    return 0


def _parse_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise RuntimeError("model returned JSON that was not an object")
    return data


def _context(goal: str, index_text: str, prior_text: str, allowlist_text: str) -> str:
    parts = [
        "Goal:\n" + goal.strip(),
        "Allowlist:\n" + (allowlist_text.strip() or "(none)"),
        "Earlier runs (INDEX.md):\n" + (index_text.strip() or "(no earlier runs)"),
        "Most relevant previous notebook:\n" + (prior_text.strip() or "(none)"),
    ]
    return "\n\n".join(parts)
