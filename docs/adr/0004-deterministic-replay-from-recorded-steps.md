# 4. Deterministic replay from recorded steps

**Status:** accepted

## Context
Incident response needs to reproduce a failing run, but live reruns are non-deterministic, may
have side effects, and cost money.

## Decision
Store every run's model responses and tool results as ordered steps. Replay by running the same graph
against a recorded model and recorded tools. A second mode reruns the question live against another
version and diffs the trajectory, answer, cost and latency.

## Consequences
- Replays reproduce failures exactly (tested for tool errors, circuit-open rejections and loop
  stops), call no real tools, and cannot cause side effects.
- Replays are stored as runs linked to the original, marked on every span, use a separate
  circuit-breaker namespace and are excluded from production metrics.
- Limits: a replay uses the stored, already redacted text, and it cannot show what a different model
  or prompt would have done; that is what the rerun mode is for. Runs recorded with content capture
  disabled cannot be replayed.
