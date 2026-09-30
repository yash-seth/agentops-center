# Responsible AI controls

What is implemented, where, and where the limits are.

## PII redaction
- **Where:** `aoc_runtime/guardrails.py`, applied in the gateway before the question reaches the
  model, the traces or the run store. Stored answers and step inputs/outputs go through the same
  redactor (`console/runs.py`).
- **How:** Microsoft Presidio pattern recognizers (email, phone, credit card, IP address, IBAN,
  URL and others) plus custom Indian PAN and Aadhaar recognizers. Matches are replaced with
  `<ENTITY_TYPE>` placeholders.
- **Limits:**
  - Presidio runs on a tokenizer-only pipeline, so it does **not** detect person names,
    locations or organisations (that needs an NER model). Add one before handling real data.
  - Pattern matching produces some false positives (a card number can also look like an Aadhaar
    number; the higher-scoring entity wins) and can miss unusual formats.
  - Model output is redacted when stored, but a model can still echo PII from a tool result into
    the live response or a span. In production add a redaction processor in the OpenTelemetry
    Collector as a second layer.

## Prompt-injection screening
- Heuristic rules (`ignore_instructions`, `reveal_system_prompt`, `role_override`,
  `exfiltration`) block a request with HTTP 422, write an audit event and increment
  `aoc_guardrail_blocks_total{type="prompt_injection"}`.
- This is a first line of defence, not a guarantee. Determined attackers can rephrase; pair it with
  least-privilege tools (the SQL tool is read-only) and output checks.

## Audit trail
- `audit_events` is append-only (no update or delete API) and hash-chained: each event's hash covers
  its content and the previous hash. `GET /audit/verify` recomputes the chain and reports the first
  broken event.
- Events record **hashes** of the original input and output, never the raw text, so the trail can
  prove what happened without storing sensitive content.
- Covers gateway runs, redactions, guardrail blocks and replays (with the actor).
- Limit: the chain detects edits but not deletion of the newest events. A production system would
  anchor the latest hash in an external write-once store.

## Content capture switch
- `AOC_CAPTURE_CONTENT=false` stores `[content not captured]` instead of questions, answers and step
  outputs, and configures the OpenInference instrumentation to hide inputs and outputs. Metadata
  (timings, tokens, costs, statuses) is still recorded.
- Runs recorded this way cannot be replayed, by design.

## Safety behaviour
- Loop guard and step/cost budgets stop runaway runs and return a safe fallback answer.
- Tool timeouts, retries and circuit breakers stop a failing dependency from cascading.
- Replays never call real tools or models in deterministic mode, so debugging cannot cause side
  effects.
