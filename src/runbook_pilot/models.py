from dataclasses import dataclass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    # None means the model is not in the local price table.
    cost_usd: float | None = 0.0

    def add(self, other: "Usage") -> "Usage":
        if self.cost_usd is None or other.cost_usd is None:
            cost = None
        else:
            cost = self.cost_usd + other.cost_usd
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            cost,
        )


@dataclass
class Step:
    intent: str
    command: str
    expected: str
    decision: str = "pending"
    mode: str = ""
    outcome: str = ""
    block_reason: str = ""
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    interpretation: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = 0.0
    alternative: str = ""
    from_alternative: bool = False


@dataclass
class Decision:
    action: str
    intent: str = ""
    command: str = ""
    expected: str = ""


def format_cost(cost: float | None) -> str:
    if cost is None:
        return "unknown"
    return f"${cost:.6f}"


def format_usage(input_tokens: int, output_tokens: int, cost: float | None) -> str:
    return f"{input_tokens} in / {output_tokens} out, est. {format_cost(cost)}"
