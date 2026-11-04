import json
from types import SimpleNamespace

import pytest

from runbook_pilot.llm import (
    FakeProvider,
    OpenAIProvider,
    resolve_provider_name,
    steps_from_plan,
)
from runbook_pilot.models import Step
from runbook_pilot.pricing import estimate_cost


def test_fake_docker_plan_ends_with_a_refused_delete():
    steps, usage = FakeProvider().plan(
        goal="Check docker disk and logs",
        index_text="",
        prior_text="",
        allowlist_text="",
    )
    assert [step.command for step in steps[:3]] == [
        "df -h",
        "docker system df",
        "docker ps -a --size",
    ]
    assert steps[-1].command.startswith("rm -rf")
    assert usage.cost_usd == 0.0
    assert usage.input_tokens > 0


def test_price_table_uses_the_longest_model_name():
    mini = estimate_cost("gpt-4.1-mini-2025-04-14", 1_000_000, 0)
    full = estimate_cost("gpt-4.1", 1_000_000, 0)
    assert mini == pytest.approx(0.40)
    assert full == pytest.approx(2.00)
    assert estimate_cost("gpt-5-mini", 10, 4) == pytest.approx((10 * 0.25 + 4 * 2.00) / 1_000_000)
    assert estimate_cost("some-local-model", 10, 4) is None


def test_openai_provider_requires_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        OpenAIProvider("gpt-4.1-mini")


def test_auto_uses_fake_without_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    name, note = resolve_provider_name("auto")
    assert name == "fake"
    assert "offline" in note
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value")
    assert resolve_provider_name("auto")[0] == "openai"


class _Responses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("tools"):
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        type="function_call",
                        name="propose_command",
                        arguments=json.dumps(
                            {"intent": "safer", "command": "df -h", "expected": "a table"}
                        ),
                    )
                ],
                output_text="",
                usage=SimpleNamespace(input_tokens=3, output_tokens=2),
            )
        name = kwargs.get("text", {}).get("format", {}).get("name")
        if name == "runbook_plan":
            payload = json.dumps(
                {"steps": [{"intent": "disk", "command": "df -h", "expected": "a table"}]}
            )
        elif name == "runbook_wrap":
            payload = json.dumps({"decisions": "Kept it read-only.", "lesson": "Start with df."})
        else:
            payload = "Root has room."
        return SimpleNamespace(
            output=[],
            output_text=payload,
            usage=SimpleNamespace(input_tokens=10, output_tokens=4),
        )


def test_openai_provider_parses_plan_tool_and_cost():
    client = SimpleNamespace(responses=_Responses())
    provider = OpenAIProvider("gpt-4.1-mini", client=client)
    steps, usage = provider.plan(goal="disk", index_text="", prior_text="", allowlist_text="df")
    assert steps[0].command == "df -h"
    assert usage.input_tokens == 10
    assert usage.cost_usd == pytest.approx((10 * 0.40 + 4 * 1.60) / 1_000_000)
    plan_call = client.responses.calls[0]
    assert plan_call["store"] is False
    assert plan_call["text"]["format"]["name"] == "runbook_plan"
    assert "tools" not in plan_call

    alt, alt_usage = provider.alternative(
        step=Step("wipe", "rm -rf /", "no"),
        reason="never",
        allowlist_text="df",
    )
    assert alt is not None and alt.command == "df -h"
    assert alt_usage.input_tokens == 3
    tool_call = client.responses.calls[1]
    assert tool_call["tools"][0]["name"] == "propose_command"
    assert tool_call["tool_choice"]["name"] == "propose_command"
    assert tool_call["store"] is False

    text, _ = provider.interpret(
        step=Step("disk", "df -h", "table", exit_code=0, stdout="ok\n", outcome="ran")
    )
    assert text == "Root has room."
    decisions, lesson, _ = provider.wrap_up(summary="1 ran")
    assert lesson == "Start with df."
    assert "read-only" in decisions


def test_plan_parser_drops_blanks_and_caps_the_list():
    payload = {"steps": [{"intent": "", "command": "true", "expected": "x"}]}
    payload["steps"].extend(
        {"intent": f"step {i}", "command": f"echo {i}", "expected": "n"} for i in range(10)
    )
    steps = steps_from_plan(payload)
    assert len(steps) == 8
    assert steps[0].command == "echo 0"
