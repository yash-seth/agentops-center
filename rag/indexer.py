from pathlib import Path
from typing import Protocol

from aoc_runtime.config import DATA_DIR, get_settings

from .chunking import STRATEGIES
from .embeddings import Embedder, get_embedder
from .store import Hit, LocalVectorStore


class VectorStore(Protocol):
    name: str

    def add(self, chunks: list[str], source: str) -> None: ...
    def search(self, query: str, k: int = 4) -> list[Hit]: ...


def build_store(
    docs_dir: Path | None = None,
    strategy: str = "heading",
    embedder: Embedder | None = None,
    *,
    backend: str | None = None,
    collection: str | None = None,
    rebuild: bool = False,
) -> VectorStore:
    """Index every ``*.md`` in docs_dir into the configured vector store.

    ``backend`` defaults to AOC_VECTOR_STORE (local | pgvector). With pgvector the collection is
    only re-indexed when empty or when ``rebuild`` is set, so restarts don't re-embed.
    """
    settings = get_settings()
    docs_dir = docs_dir or DATA_DIR / "docs"
    backend = backend or settings.aoc_vector_store
    embedder = embedder or get_embedder()

    store: VectorStore
    if backend == "pgvector":
        from .pgvector_store import PgVectorStore

        pg = PgVectorStore(embedder, settings.aoc_pg_dsn, collection or docs_dir.name)
        if not rebuild and pg.count() > 0:
            return pg
        pg.reset()
        store = pg
    elif backend == "local":
        store = LocalVectorStore(embedder)
    else:
        raise ValueError(f"unknown vector store {backend!r}")

    chunker = STRATEGIES[strategy]
    for path in sorted(docs_dir.glob("*.md")):
        store.add(chunker(path.read_text(encoding="utf-8")), source=path.name)
    return store
