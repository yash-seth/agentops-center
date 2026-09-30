import sqlite3
from functools import lru_cache

from langchain_core.tools import tool

from aoc_runtime.config import DATA_DIR
from data.seed_inventory import DB, seed
from rag.indexer import build_store


@lru_cache
def _store():
    return build_store(DATA_DIR / "docs" / "supply")


@tool
def rag_search(query: str) -> str:
    """Search SnackCo supply chain SOPs and supplier policies. Returns the top passages."""
    hits = _store().search(query, k=3)
    return "\n---\n".join(f"[{h.source}] {h.text}" for h in hits) or "no results"


@tool
def inventory_sql(sku: str) -> str:
    """Look up on-hand stock and daily demand for a SKU (e.g. SKU-1001) across DCs."""
    if not DB.exists():
        seed()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT dc, on_hand, daily_demand FROM inventory WHERE sku = ?", (sku,)
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return f"unknown sku {sku}"
    return "; ".join(
        f"{dc}: on_hand={oh}, daily_demand={dd}, days_cover={oh / dd:.1f}" for dc, oh, dd in rows
    )


@tool
def create_ticket(priority: str, summary: str) -> str:
    """Create a supply desk ticket. priority is P1, P2 or P3."""
    con = sqlite3.connect(DB)
    try:
        cur = con.execute(
            "INSERT INTO tickets (priority, summary) VALUES (?, ?)", (priority, summary)
        )
        con.commit()
        return f"ticket {cur.lastrowid} created"
    finally:
        con.close()


TOOLS = [rag_search, inventory_sql, create_ticket]
