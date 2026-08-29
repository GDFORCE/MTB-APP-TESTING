from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    value = os.getenv("UCTSM_DATABASE_URL")
    if not value:
        raise RuntimeError("UCTSM_DATABASE_URL must be configured for universal schedule APIs")
    return value


def build_session_factory(url: str | None = None) -> sessionmaker[Session]:
    engine = create_engine(url or database_url(), pool_pre_ping=True)
    if engine.dialect.name == "sqlite" and os.getenv("UCTSM_DEMO_MODE", "").lower() in {"1", "true", "yes"}:
        # Local interactive testing only. Production always uses Alembic/PostgreSQL.
        from . import models  # noqa: F401
        Base.metadata.create_all(engine, checkfirst=True)
    return sessionmaker(bind=engine, expire_on_commit=False)


_session_factory: sessionmaker[Session] | None = None


def get_session() -> Generator[Session, None, None]:
    global _session_factory
    if _session_factory is None:
        _session_factory = build_session_factory()
    with _session_factory() as session:
        yield session
