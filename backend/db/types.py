"""Portable column types.

Production runs on PostgreSQL and wants JSONB (indexable, binary, no reparsing).
The test suite runs against SQLite so it needs no server. This decorator picks
the right one per dialect instead of forcing the tests onto a database they do
not need.
"""

from __future__ import annotations

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator


class PortableJSON(TypeDecorator):
    """JSONB on PostgreSQL, plain JSON everywhere else."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())
