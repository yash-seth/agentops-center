import hashlib
import re
from typing import Protocol

import numpy as np

from aoc_runtime.config import get_settings


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashEmbedder:
    """Deterministic bag-of-words hashing embedder. Offline; used in tests/CI only."""

    def __init__(self, dim: int = 384):
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in re.findall(r"[a-z0-9]+", t.lower()):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                out[i, h % self.dim] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


class FastEmbedder:
    def __init__(self, model: str = "BAAI/bge-small-en-v1.5"):
        from fastembed import TextEmbedding

        self._m = TextEmbedding(model_name=model)

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = np.array(list(self._m.embed(texts)), dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.where(norms == 0, 1, norms)


def get_embedder() -> Embedder:
    return HashEmbedder() if get_settings().aoc_embedder == "hash" else FastEmbedder()
