"""Postgres + pgvector store with the same interface as LocalVectorStore.

One ``rag_chunks`` table holds every agent's chunks, separated by a ``collection`` column, so the
registry database can double as the vector database.
"""

from __future__ import annotations

from .embeddings import Embedder
from .store import Hit


class PgVectorStore:
    name = "pgvector"

    def __init__(self, embedder: Embedder, dsn: str, collection: str):
        import psycopg
        from pgvector.psycopg import register_vector

        self.embedder = embedder
        self.collection = collection
        self.dim = int(embedder.embed(["dimension probe"]).shape[1])

        self.conn = psycopg.connect(dsn, autocommit=True)
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(self.conn)
        self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS rag_chunks (
                id BIGSERIAL PRIMARY KEY,
                collection TEXT NOT NULL,
                source TEXT NOT NULL,
                text TEXT NOT NULL,
                embedding vector({self.dim}) NOT NULL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS rag_chunks_collection_idx ON rag_chunks (collection)"
        )

    def count(self) -> int:
        row = self.conn.execute(
            "SELECT count(*) FROM rag_chunks WHERE collection = %s", (self.collection,)
        ).fetchone()
        return int(row[0]) if row else 0

    def reset(self) -> None:
        self.conn.execute("DELETE FROM rag_chunks WHERE collection = %s", (self.collection,))

    def add(self, chunks: list[str], source: str) -> None:
        if not chunks:
            return
        vecs = self.embedder.embed(chunks)
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO rag_chunks (collection, source, text, embedding) "
                "VALUES (%s, %s, %s, %s)",
                [(self.collection, source, t, v) for t, v in zip(chunks, vecs, strict=True)],
            )

    def search(self, query: str, k: int = 4) -> list[Hit]:
        q = self.embedder.embed([query])[0]
        rows = self.conn.execute(
            "SELECT text, source, 1 - (embedding <=> %s) AS score FROM rag_chunks "
            "WHERE collection = %s ORDER BY embedding <=> %s LIMIT %s",
            (q, self.collection, q, k),
        ).fetchall()
        return [Hit(text=r[0], source=r[1], score=float(r[2])) for r in rows]

    def close(self) -> None:
        self.conn.close()
