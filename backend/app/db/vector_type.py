"""A cross-dialect embedding column type (Phase 12): a real, fixed-
dimension `pgvector` column on PostgreSQL — enabling database-side ANN
similarity search — and a plain JSON float array on every other dialect
(SQLite, used by this codebase's default test suite), so the one existing
`KnowledgeChunk.embedding` column keeps working unchanged for every test
that doesn't specifically target PostgreSQL.

This is the standard SQLAlchemy "backend-agnostic column type" pattern
(see SQLAlchemy's own docs' GUID-type recipe) applied to vectors — not a
second storage representation to keep in sync: there is exactly one
column, one value, and this type decides at DDL/bind/result time how to
represent it for whichever engine is actually connected.
"""

from __future__ import annotations

from sqlalchemy import JSON, Float
from sqlalchemy.types import TypeDecorator, TypeEngine

try:
    from pgvector.sqlalchemy import Vector as _PGVector
except ImportError:  # pragma: no cover — pgvector is a real, declared dependency (requirements.txt)
    _PGVector = None


class _VectorComparator(TypeEngine.Comparator):
    """`TypeDecorator` doesn't automatically forward the dialect-specific
    impl type's comparator methods, so `cosine_distance()` (used by
    KnowledgeRetrievalService.search() to build `ORDER BY embedding <=>
    :query_vector` on PostgreSQL) has to be defined explicitly here. This
    is only ever invoked on the PostgreSQL code path — the SQLite code
    path never calls it (see search()'s dialect branch), so the operator
    being PostgreSQL-only SQL is never an issue in practice."""

    def cosine_distance(self, other):
        return self.op("<=>", return_type=Float)(other)


class PortableVector(TypeDecorator):
    """`vector(dimensions)` on PostgreSQL, `JSON` everywhere else. Values
    are always a plain `list[float]` at the Python level regardless of
    dialect — callers never need to know which representation is active."""

    impl = JSON
    cache_ok = True
    comparator_factory = _VectorComparator

    def __init__(self, dimensions: int, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.dimensions = dimensions

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql" and _PGVector is not None:
            return dialect.type_descriptor(_PGVector(self.dimensions))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        # pgvector's own SQLAlchemy type accepts a plain list[float]
        # directly; JSON does too — no dialect-specific conversion needed
        # on the way in.
        return list(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        # Defensively normalize to a plain list[float] so every caller
        # (JSON-serializing API responses, the cosine-similarity fallback)
        # sees the exact same Python type regardless of dialect.
        return [float(v) for v in value]
