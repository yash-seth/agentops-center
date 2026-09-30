"""List-price-equivalent cost. Free tiers cost $0 in reality, but showback/chargeback uses the
price you *would* pay, so we always compute cost from a price table (USD per 1M tokens)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input_per_m: float
    output_per_m: float


# Verify against provider pricing pages before quoting numbers; see docs/COSTS.md.
PRICE_TABLE: dict[str, Price] = {
    "gemini-2.5-flash": Price(0.30, 2.50),
    "llama-3.3-70b-versatile": Price(0.59, 0.79),
    "qwen2.5:3b": Price(0.0, 0.0),  # local
    "fake-llm": Price(0.10, 0.40),  # deterministic, non-zero so cost paths are exercised in tests
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price = PRICE_TABLE.get(model)
    if price is None:
        return 0.0
    return (input_tokens * price.input_per_m + output_tokens * price.output_per_m) / 1_000_000
