"""Guardrails: PII redaction (Microsoft Presidio) and prompt-injection screening.

Redaction runs *before* text is sent to the model, traced, logged or stored, so PII never reaches
telemetry. Presidio's pattern recognizers (email, phone, card, IP, IBAN, URL ...) run on a
tokenizer-only spaCy pipeline, so no NER model download is needed. That means names of people are
NOT detected; the trade-off is documented in docs/RESPONSIBLE_AI.md. Indian PAN and Aadhaar
recognizers are added because the role is India-based.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

MIN_SCORE = 0.35

INJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "ignore_instructions": re.compile(
        r"\b(ignore|disregard|forget)\b.{0,30}\b(previous|prior|above|earlier|all)\b.{0,30}"
        r"\b(instructions?|rules?|prompts?)\b",
        re.I,
    ),
    "reveal_system_prompt": re.compile(
        r"\b(reveal|show|print|repeat|leak)\b.{0,30}\b(system|hidden|initial)\b.{0,10}"
        r"\b(prompt|instructions?)\b",
        re.I,
    ),
    "role_override": re.compile(
        r"\b(you are now|act as|pretend to be)\b.{0,40}\b(dan|developer mode|unrestricted)\b", re.I
    ),
    "exfiltration": re.compile(r"\b(send|post|upload)\b.{0,40}\b(to|at)\b.{0,20}https?://", re.I),
}


@dataclass
class RedactionResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.counts)


def check_injection(text: str) -> str | None:
    """Return the name of the first matching rule, or None. Heuristic, not a guarantee."""
    for name, pattern in INJECTION_PATTERNS.items():
        if pattern.search(text):
            return name
    return None


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@lru_cache
def _engines():
    import spacy
    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
    from presidio_analyzer.nlp_engine import NlpArtifacts, NlpEngine
    from presidio_anonymizer import AnonymizerEngine

    class TokenizerOnlyEngine(NlpEngine):
        def __init__(self) -> None:
            self.nlp = None

        def load(self) -> None:
            self.nlp = spacy.blank("en")

        def is_loaded(self) -> bool:
            return self.nlp is not None

        def process_text(self, text: str, language: str) -> NlpArtifacts:
            doc = self.nlp(text)
            return NlpArtifacts(
                entities=[], tokens=doc, tokens_indices=[t.idx for t in doc],
                lemmas=[t.text.lower() for t in doc], nlp_engine=self, language=language, scores=[],
            )

        def process_batch(self, texts, language, batch_size=1, n_process=1, **kwargs):
            for t in texts:
                yield t, self.process_text(t, language)

        def is_stopword(self, word: str, language: str) -> bool:
            return False

        def is_punct(self, word: str, language: str) -> bool:
            return not word.isalnum()

        def get_supported_entities(self) -> list[str]:
            return []

        def get_supported_languages(self) -> list[str]:
            return ["en"]

    nlp_engine = TokenizerOnlyEngine()
    nlp_engine.load()
    registry = RecognizerRegistry()
    registry.load_predefined_recognizers(languages=["en"], nlp_engine=nlp_engine)
    registry.add_recognizer(
        PatternRecognizer(
            supported_entity="IN_AADHAAR",
            patterns=[Pattern("aadhaar", r"\b[2-9]\d{3}\s?\d{4}\s?\d{4}\b", 0.6)],
        )
    )
    registry.add_recognizer(
        PatternRecognizer(
            supported_entity="IN_PAN",
            patterns=[Pattern("pan", r"\b[A-Z]{5}\d{4}[A-Z]\b", 0.85)],
        )
    )
    analyzer = AnalyzerEngine(
        nlp_engine=nlp_engine, registry=registry, supported_languages=["en"]
    )
    return analyzer, AnonymizerEngine()


def redact(text: str) -> RedactionResult:
    """Replace detected PII with <ENTITY_TYPE> placeholders."""
    if not text:
        return RedactionResult(text)
    analyzer, anonymizer = _engines()
    found = analyzer.analyze(text=text, language="en", score_threshold=MIN_SCORE)
    if not found:
        return RedactionResult(text)
    out = anonymizer.anonymize(text=text, analyzer_results=found)
    counts: dict[str, int] = {}
    for item in out.items:
        counts[item.entity_type] = counts.get(item.entity_type, 0) + 1
    return RedactionResult(out.text, counts)


def redact_text(text: str) -> str:
    return redact(text).text


def redact_obj(obj: Any, fn=redact_text) -> Any:
    """Apply a text redactor to every string inside nested dicts and lists."""
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v, fn) for v in obj]
    return obj
