"""FinOps views over run history: showback by tenant/agent/model/day and model what-if.

Costs are list-price equivalents from aoc_runtime.cost (free tiers cost nothing in reality, but
showback charges what the usage *would* cost). Replays are excluded from showback because they are
operational analysis, not tenant usage; their overhead is reported separately.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from aoc_runtime.cost import PRICE_TABLE, cost_usd

from .db import Run

GROUPS = ("tenant", "agent", "model", "day", "version")
CSV_COLUMNS = (
    "tenant", "agent", "model", "runs", "input_tokens", "output_tokens", "list_price_cost_usd"
)


class FinOpsError(Exception):
    pass


def _window(s: Session, days: int, replays: bool = False) -> list[Run]:
    since = datetime.now(UTC) - timedelta(days=days)
    q = select(Run).where(Run.started_at >= since)
    q = q.where(Run.replay_of.is_not(None) if replays else Run.replay_of.is_(None))
    return list(s.scalars(q))


def _key(run: Run, group_by: str) -> str:
    if group_by == "day":
        return run.started_at.date().isoformat()
    return {
        "tenant": run.tenant, "agent": run.agent, "model": run.model or "unknown",
        "version": f"{run.agent} {run.version}",
    }[group_by]


def summary(s: Session, group_by: str = "tenant", days: int = 30) -> dict:
    if group_by not in GROUPS:
        raise FinOpsError(f"group_by must be one of {GROUPS}")
    rows: dict[str, dict] = {}
    for run in _window(s, days):
        row = rows.setdefault(
            _key(run, group_by),
            {"key": _key(run, group_by), "runs": 0, "input_tokens": 0, "output_tokens": 0,
             "cost_usd": 0.0},
        )
        row["runs"] += 1
        row["input_tokens"] += run.input_tokens
        row["output_tokens"] += run.output_tokens
        row["cost_usd"] += run.cost_usd
    total = sum(r["cost_usd"] for r in rows.values())
    out = sorted(rows.values(), key=lambda r: r["key"] if group_by == "day" else -r["cost_usd"])
    for r in out:
        r["avg_cost_per_run_usd"] = r["cost_usd"] / r["runs"]
        r["share"] = r["cost_usd"] / total if total else 0.0
    replay_cost = sum(r.cost_usd for r in _window(s, days, replays=True))
    return {
        "group_by": group_by, "days": days, "total_cost_usd": total,
        "replay_overhead_usd": replay_cost, "rows": out,
    }


def showback_csv(s: Session, days: int = 30) -> str:
    rows: dict[tuple, dict] = {}
    for run in _window(s, days):
        key = (run.tenant, run.agent, run.model or "unknown")
        r = rows.setdefault(
            key,
            dict(zip(CSV_COLUMNS[:3], key, strict=True))
            | {"runs": 0, "input_tokens": 0, "output_tokens": 0, "list_price_cost_usd": 0.0},
        )
        r["runs"] += 1
        r["input_tokens"] += run.input_tokens
        r["output_tokens"] += run.output_tokens
        r["list_price_cost_usd"] += run.cost_usd
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for key in sorted(rows):
        row = rows[key]
        row["list_price_cost_usd"] = f"{row['list_price_cost_usd']:.6f}"
        writer.writerow(row)
    return buf.getvalue()


def whatif(s: Session, model: str, days: int = 30) -> dict:
    """Recompute the window's cost as if every run had used ``model`` (same token counts)."""
    if model not in PRICE_TABLE:
        raise FinOpsError(f"unknown model {model!r}; known: {sorted(PRICE_TABLE)}")
    by_agent: dict[str, dict] = {}
    for run in _window(s, days):
        row = by_agent.setdefault(run.agent, {"agent": run.agent, "current": 0.0, "alt": 0.0})
        row["current"] += run.cost_usd
        row["alt"] += cost_usd(model, run.input_tokens, run.output_tokens)
    rows = sorted(by_agent.values(), key=lambda r: r["agent"])
    current = sum(r["current"] for r in rows)
    alt = sum(r["alt"] for r in rows)
    for r in rows:
        r["savings_usd"] = r["current"] - r["alt"]
    return {
        "model": model, "days": days, "current_cost_usd": current, "alt_cost_usd": alt,
        "savings_usd": current - alt,
        "savings_pct": (current - alt) / current if current else 0.0,
        "by_agent": rows,
        "caveat": "Cost only: this does not measure answer quality on the cheaper model.",
    }


def price_table() -> list[dict]:
    return [
        {"model": m, "input_per_m_usd": p.input_per_m, "output_per_m_usd": p.output_per_m}
        for m, p in sorted(PRICE_TABLE.items())
    ]
