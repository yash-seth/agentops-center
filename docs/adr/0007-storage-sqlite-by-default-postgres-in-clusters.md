# 7. SQLite by default, Postgres in clusters, no migrations yet

**Status:** accepted for now

## Context
Local development should need no services. The cluster deployment needs a shared database for the
gateway and console API, and the vector store can reuse it.

## Decision
Use SQLAlchemy with a SQLite file by default and a Postgres URL in compose and Kubernetes. The vector
store has a local in-memory implementation and a pgvector implementation behind one interface. Tables
are created with `create_all`, retried to tolerate concurrent starts.

## Consequences
- Zero-setup development and identical application code across environments.
- Limit: no schema migrations; changing a model means resetting the dev database. Adopt Alembic before
  anyone depends on persisted data.
- Limit: the Postgres and pgvector paths are covered by a test that needs a running database, which has
  not been exercised on the machine this was developed on.
