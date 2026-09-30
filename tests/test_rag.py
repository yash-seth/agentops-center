import pytest

from aoc_runtime.config import DATA_DIR
from rag.chunking import STRATEGIES
from rag.embeddings import HashEmbedder
from rag.indexer import build_store

SUPPLY = DATA_DIR / "docs" / "supply"


@pytest.mark.parametrize("strategy", sorted(STRATEGIES))
def test_local_store_retrieves_right_document(strategy):
    store = build_store(SUPPLY, strategy=strategy, embedder=HashEmbedder(), backend="local")
    hits = store.search("standard lead time for imported ingredients", k=2)
    assert hits and hits[0].source == "supplier_policy.md"


def test_unknown_backend_rejected():
    with pytest.raises(ValueError):
        build_store(SUPPLY, embedder=HashEmbedder(), backend="nope")


def _pg_available() -> bool:
    try:
        import psycopg

        from aoc_runtime.config import get_settings

        psycopg.connect(get_settings().aoc_pg_dsn, connect_timeout=2).close()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _pg_available(), reason="needs Postgres+pgvector (docker compose up)")
def test_pgvector_matches_local_store():
    emb = HashEmbedder()
    pg = build_store(
        SUPPLY, embedder=emb, backend="pgvector", collection="test_supply", rebuild=True
    )
    local = build_store(SUPPLY, embedder=emb, backend="local")
    q = "when must a P1 ticket be opened"
    assert [h.text for h in pg.search(q, k=3)] == [h.text for h in local.search(q, k=3)]
    pg.reset()
    pg.close()
