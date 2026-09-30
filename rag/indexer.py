from pathlib import Path

from aoc_runtime.config import DATA_DIR

from .chunking import STRATEGIES
from .embeddings import Embedder, get_embedder
from .store import LocalVectorStore


def build_store(
    docs_dir: Path | None = None, strategy: str = "heading", embedder: Embedder | None = None
) -> LocalVectorStore:
    store = LocalVectorStore(embedder or get_embedder())
    chunker = STRATEGIES[strategy]
    for path in sorted((docs_dir or DATA_DIR / "docs").glob("*.md")):
        store.add(chunker(path.read_text(encoding="utf-8")), source=path.name)
    return store
