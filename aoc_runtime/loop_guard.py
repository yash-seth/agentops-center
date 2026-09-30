"""Loop detection for agent runs.

Flags, in order of severity:
  * repeat      - the same (tool, normalized args) called >= max_repeats times
  * oscillation - an A,B,A,B pattern over the last four calls
  * step_budget - more model steps than the agent's max_steps
  * cost_budget - run cost above the agent's per-run budget
When tripped, the graph stops gracefully and returns a safe fallback answer;
LangGraph's recursion_limit stays as the hard backstop.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

FALLBACK_ANSWER = (
    "I could not complete this request: the agent stopped itself after detecting a loop. "
    "The incident has been recorded for the operations team."
)


def args_hash(args: dict[str, Any]) -> str:
    normalized = json.dumps(args, sort_keys=True, default=str).lower().strip()
    return hashlib.sha1(normalized.encode()).hexdigest()[:12]


@dataclass
class LoopGuard:
    max_steps: int = 6
    cost_budget_usd: float = 0.05
    max_repeats: int = 3
    calls: list[tuple[str, str]] = field(default_factory=list)
    steps: int = 0

    def observe_step(self) -> str | None:
        self.steps += 1
        if self.steps > self.max_steps:
            return "step_budget"
        return None

    def observe_call(self, tool: str, args: dict[str, Any]) -> str | None:
        self.calls.append((tool, args_hash(args)))
        if self.calls.count(self.calls[-1]) >= self.max_repeats:
            return "repeat"
        last4 = self.calls[-4:]
        a, b, c, d = (last4 + [None] * 4)[:4] if len(last4) == 4 else (None,) * 4
        if a is not None and a == c and b == d and a != b:
            return "oscillation"
        return None

    def observe_cost(self, cost_usd: float) -> str | None:
        return "cost_budget" if cost_usd > self.cost_budget_usd else None
