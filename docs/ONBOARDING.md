# Onboarding a new agent

Goal: a team goes from nothing to an observable, eval-gated agent in about ten minutes.

```bash
aoc new-agent claims-helper --template rag --owner claims-team@snackco.example
```

This creates, and validates, everything the platform needs:

| Created | Purpose |
|---|---|
| `agents/claims_helper/agent.yaml` | identity, owner, tenant, allowed models, tools, limits, redaction policy |
| `agents/claims_helper/prompts/v1.md` | the versioned system prompt |
| `agents/claims_helper/tools.py` | `rag_search` (and a stub `lookup_status` with `--template tools`) |
| `data/docs/claims_helper/guide.md` | sample knowledge base; replace with real documents |
| `evals/datasets/claims-helper.yaml` | retrieval judgements, expected tool use, answer checks |

The new agent is already observable: tracing, metrics, cost tagging, loop detection, tool retries and
circuit breakers, PII redaction and the audit trail come from the shared runtime, so there is
nothing to wire up.

## Checklist
1. Replace the sample documents and the eval dataset with real content and real judgements from
   domain experts. The scaffold's eval passes trivially by design; it is a starting point.
2. `aoc validate claims-helper` - must pass. It rejects, among other things: unknown config keys
   (typos), models missing from the price table (their cost would show as $0), tools declared but not
   implemented, missing owner/tenant/redaction policy, and missing eval datasets.
3. `aoc eval --agent claims-helper --update-baseline` - records the baseline the CI gate compares
   against; commit `evals/baseline.fake.json`.
4. Open a PR. CI runs validation, tests and the eval gate; label it `run-evals` for the fuller run.
5. After merge, register and promote: `aoc register`, then promote draft to staging to prod in the
   console (or the API). Nothing is served until a version is promoted.

## Versioning rules
- A registered version is immutable. Editing the prompt or tools means bumping `version` in
  `agent.yaml`; re-registering changed content under the same version is rejected.
- Promotion is `draft -> staging -> prod`. Rolling back re-promotes the previous prod version and is
  recorded in the deployment history.
- Use "rerun" replay against the new version to compare it with real failing runs before promoting.

## What the validator enforces
`aoc validate` is the same command CI runs, so a rule added in `cli/validate.py` becomes a merge
requirement for every agent.
