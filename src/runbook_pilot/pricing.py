# Standard short-context rates, USD per 1M tokens.
# Not batch, not long-context, and cached input is priced as normal input,
# so the figure can run a bit high. Checked against
# https://developers.openai.com/api/docs/pricing on 2026-11-04.
# Longer snapshot names (gpt-4.1-mini-2025-04-14) use the longest matching row.
_PRICES: tuple[tuple[str, tuple[float, float]], ...] = (
    ("gpt-4.1-mini", (0.40, 1.60)),
    ("gpt-4.1", (2.00, 8.00)),
    ("gpt-4o-mini", (0.15, 0.60)),
    ("gpt-4o", (2.50, 10.00)),
    ("gpt-5-mini", (0.25, 2.00)),
    ("gpt-5-nano", (0.05, 0.40)),
    ("gpt-5", (1.25, 10.00)),
)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    matches = [
        (name, rate)
        for name, rate in _PRICES
        if model == name or model.startswith(name + "-")
    ]
    if not matches:
        return None
    _name, (price_in, price_out) = max(matches, key=lambda item: len(item[0]))
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000
