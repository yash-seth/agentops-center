"""Fault injection ("chaos") so incidents can be produced on demand for demos and tests.

Faults are process-global and can be set from code (``set_faults``), from the AOC_CHAOS env var
(JSON), or later from a registry flag. Every injected fault is visible in traces because it runs
inside the tool span.
"""

from __future__ import annotations

import contextvars
import json
import os
import random
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass


class InjectedFault(ConnectionError):
    """Injected transient failure (a ConnectionError, so retries treat it like a real outage)."""


@dataclass(frozen=True)
class Fault:
    tool: str  # tool name, or "*" for every tool
    error_rate: float = 0.0
    latency_s: float = 0.0
    force_loop: bool = False  # model keeps calling this tool with the same arguments


_faults: list[Fault] = []
_rng = random.Random()


def set_faults(faults: list[Fault], seed: int | None = None) -> None:
    global _faults
    _faults = list(faults)
    if seed is not None:
        _rng.seed(seed)


def clear_faults() -> None:
    set_faults([])


def load_from_env() -> None:
    raw = os.environ.get("AOC_CHAOS", "").strip()
    if raw:
        set_faults([Fault(**item) for item in json.loads(raw)])


_suspended: contextvars.ContextVar[bool] = contextvars.ContextVar("aoc_faults_off", default=False)


@contextmanager
def suspended() -> Iterator[None]:
    """Disable injected faults for the current context (used by deterministic replay)."""
    token = _suspended.set(True)
    try:
        yield
    finally:
        _suspended.reset(token)


def _matching(tool: str) -> list[Fault]:
    if _suspended.get():
        return []
    return [f for f in _faults if f.tool in (tool, "*")]


def apply_tool_faults(tool: str) -> None:
    """Called inside the tool span, before the tool body runs."""
    for f in _matching(tool):
        if f.latency_s:
            time.sleep(f.latency_s)
        if f.error_rate and _rng.random() < f.error_rate:
            raise InjectedFault(f"injected failure in tool {tool!r}")


def forced_loop_tool() -> str | None:
    if _suspended.get():
        return None
    return next((f.tool for f in _faults if f.force_loop), None)
