# 5. Redact before anything leaves the process, with pattern-based detection

**Status:** accepted, with known gaps

## Context
Prompts and answers can contain personal data. Traces, logs and the run store must not become a
second copy of it.

## Decision
- Redact the question in the gateway before it reaches the model, tracing or storage, and redact
  stored answers and step payloads.
- Use Microsoft Presidio pattern recognizers on a tokenizer-only pipeline (no model download), with
  added Indian PAN and Aadhaar recognizers.
- Store hashes, not raw text, in the append-only, hash-chained audit log.
- Offer a capture-content switch that keeps text out of the store and out of OpenInference spans.

## Consequences
- Deterministic, fast, free, and effective for structured identifiers (email, phone, cards, IPs, IDs).
- Gap: person names, locations and organisations are not detected without an NER model. Add one
  before handling real data.
- Gap: a model can echo PII from a tool result into a live response; a Collector-level redaction
  processor should be a second layer in production.
