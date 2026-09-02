"""Engine and session factory."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.settings import get_settings


@lru_cache(maxsize=4)
def get_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    # pool_pre_ping: this process is idle for ~24h between scheduled runs, long
    # enough for the database to have closed the connection underneath it.
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args)


@lru_cache(maxsize=4)
def get_sessionmaker(database_url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(database_url), expire_on_commit=False, future=True)


@contextmanager
def session_scope(database_url: str | None = None) -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back on any exception."""
    session = get_sessionmaker(database_url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
