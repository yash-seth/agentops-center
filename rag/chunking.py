"""Chunking strategies compared in evals/test_retrieval.py."""

import re


def fixed_chunks(text: str, size: int = 400, overlap: int = 50) -> list[str]:
    step = max(1, size - overlap)
    pieces = (text[i : i + size].strip() for i in range(0, len(text), step))
    return [p for p in pieces if p]


def paragraph_chunks(text: str, max_chars: int = 600) -> list[str]:
    """Split on blank lines, then pack paragraphs up to max_chars (keeps sections intact)."""
    chunks: list[str] = []
    buf = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if buf and len(buf) + len(para) + 2 > max_chars:
            chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return chunks


def heading_chunks(text: str) -> list[str]:
    """One chunk per markdown section, heading kept as context."""
    parts = re.split(r"(?m)^(?=#{1,3} )", text)
    return [p.strip() for p in parts if p.strip()]


STRATEGIES = {"fixed": fixed_chunks, "paragraph": paragraph_chunks, "heading": heading_chunks}
