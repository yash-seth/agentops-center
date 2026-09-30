# 6. Evals as a regression gate, deterministic by default

**Status:** accepted

## Context
Agent changes (prompts, chunking, tools) can silently degrade quality. The gate must run on every
pull request for free, without API keys or flakiness.

## Decision
- Evals cover three things per agent: retrieval quality (hit@k and MRR per chunking strategy),
  trajectories (expected tools, step budget, no loops) and answers (required content).
- The default mode is deterministic: hash embeddings and a scripted model. A run fails if a gated
  metric is worse than `evals/baseline.fake.json` by more than a tolerance.
- A second mode uses real models and embeddings, on demand and nightly, with its own baseline.

## Consequences
- Every PR gets a stable, free regression check; a test proves a deliberately degraded chunker fails it.
- Limit: the deterministic mode guards regressions, it does not measure absolute quality (its
  embedder is weaker than a real one, and one supply answer check fails because of that).
- Limit: the real-model baseline does not exist until real evals have been run with an API key.
