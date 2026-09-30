"""Tool resilience: per-call timeout, retry with backoff for transient errors, circuit breaker.

The graph records every attempt as its own span and metric sample, so retries are visible in
traces and the tool failure rate reflects reality rather than hiding behind successful retries.
"""

from __future__ import annotations

import contextvars
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResilienceConfig:
    timeout_s: float = 10.0
    max_retries: int = 2
    backoff_s: float = 0.2
    breaker_threshold: int = 5  # consecutive failures before the breaker opens
    breaker_cooldown_s: float = 30.0
    breaker_scope: str = ""  # replays use their own namespace


class CircuitOpenError(RuntimeError):
    """Raised without calling the tool while its breaker is open."""


def is_transient(exc: BaseException) -> bool:
    """Timeouts and connection problems are worth retrying; bad arguments are not."""
    return isinstance(exc, TimeoutError | ConnectionError)


class CircuitBreaker:
    def __init__(self, threshold: int, cooldown_s: float):
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self.failures = 0
        self.opened_at: float | None = None
        self._lock = threading.Lock()

    def before_call(self) -> None:
        with self._lock:
            if self.opened_at is None:
                return
            if time.monotonic() - self.opened_at >= self.cooldown_s:
                self.opened_at = None  # half-open: let one call through
                self.failures = self.threshold - 1
                return
            raise CircuitOpenError("circuit open: tool disabled after repeated failures")

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()

    @property
    def is_open(self) -> bool:
        return self.opened_at is not None


_breakers: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="aoc-tool")


def breaker_for(tool: str, cfg: ResilienceConfig) -> CircuitBreaker:
    key = cfg.breaker_scope + tool
    with _registry_lock:
        if key not in _breakers:
            _breakers[key] = CircuitBreaker(cfg.breaker_threshold, cfg.breaker_cooldown_s)
        return _breakers[key]


def reset_breakers() -> None:
    with _registry_lock:
        _breakers.clear()


def run_with_timeout(fn: Callable[[], Any], timeout_s: float) -> Any:
    """Run fn in a worker thread with the caller's context; raise TimeoutError past the limit.

    A timed-out call keeps running in its thread (Python cannot kill it); the agent moves on.
    """
    ctx = contextvars.copy_context()
    future = _executor.submit(ctx.run, fn)
    try:
        return future.result(timeout=timeout_s)
    except FutureTimeout as exc:
        raise TimeoutError(f"tool call exceeded {timeout_s}s") from exc
