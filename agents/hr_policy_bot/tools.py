from functools import lru_cache

from langchain_core.tools import tool

from aoc_runtime.config import DATA_DIR
from rag.indexer import build_store


@lru_cache
def _store():
    return build_store(DATA_DIR / "docs" / "hr")


@tool
def rag_search(query: str) -> str:
    """Search SnackCo HR policies (leave, conduct, travel and expenses). Returns top passages."""
    hits = _store().search(query, k=3)
    return "\n---\n".join(f"[{h.source}] {h.text}" for h in hits) or "no results"


TOOLS = [rag_search]
