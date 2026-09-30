from dataclasses import dataclass

import numpy as np

from .embeddings import Embedder


@dataclass
class Hit:
    text: str
    source: str
    score: float


class LocalVectorStore:
    """In-memory cosine-similarity store. Swap for pgvector via the same interface (week 2)."""

    name = "local"

    def __init__(self, embedder: Embedder):
        self.embedder = embedder
        self.texts: list[str] = []
        self.sources: list[str] = []
        self.matrix = np.zeros((0, 0), dtype=np.float32)

    def add(self, chunks: list[str], source: str) -> None:
        if not chunks:
            return
        vecs = self.embedder.embed(chunks)
        self.texts += chunks
        self.sources += [source] * len(chunks)
        self.matrix = vecs if self.matrix.size == 0 else np.vstack([self.matrix, vecs])

    def search(self, query: str, k: int = 4) -> list[Hit]:
        if not self.texts:
            return []
        q = self.embedder.embed([query])[0]
        scores = self.matrix @ q
        top = np.argsort(-scores)[:k]
        return [Hit(self.texts[i], self.sources[i], float(scores[i])) for i in top]
