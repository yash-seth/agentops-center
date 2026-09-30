# Reliability benchmarks

Reproduce: `uv run python scripts/benchmark_resilience.py` (seeded, offline, a few seconds). The
numbers below are asserted by `tests/test_benchmarks.py`, so they cannot silently drift.

These measure the reliability mechanisms against *injected* faults. They are not a claim about any
production system.

## Retries
Tool-call success rate when a fraction of calls fail transiently (2000 calls per cell; up to 2 retries
with backoff disabled for speed).

| injected failure rate | success, no retries | success, 2 retries | theory (1 - p^3) |
|---|---|---|---|
| 0% | 100.0% | 100.0% | 100.0% |
| 25% | 76.1% | 98.5% | 98.4% |
| 50% | 51.5% | 88.1% | 87.5% |
| 75% | 25.0% | 56.9% | 57.8% |

Measured results track the theory, which is a useful check that retries are independent and not
silently masking anything. Retries help with transient faults only; a hard outage needs the breaker.

## Circuit breaker
A total outage of one tool, 200 agent tool calls, 2 retries per call, breaker opens after 5
consecutive failures.

| | calls that reached the failing dependency |
|---|---|
| without breaker | 600 |
| with breaker | 5 |
| avoided | 99.2% |

Without a breaker every call hammers the failing dependency three times, which is how a partial
outage becomes a full one. The agent still gets a fast, explicit `circuit_open` result to work with.

## Loop guard
Forced loops (the model keeps requesting the same tool call) stop every time, after two tool
executions; the third identical call is blocked before it runs and the run returns a safe fallback.

| agent | looping tool | stopped | reason | tool executions before stop |
|---|---|---|---|---|
| hr-policy-bot | rag_search | yes | repeat | 2 |
| supply-chain-assistant | rag_search | yes | repeat | 2 |
| supply-chain-assistant | inventory_sql | yes | repeat | 2 |

## Retrieval and evals
See the chunking comparison in the README and `evals/baseline.fake.json`. Those use hash embeddings
and a scripted model, so they show the method and guard against regressions; they are not absolute
quality numbers.
