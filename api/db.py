from __future__ import annotations

import os
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from api.db_models import Base

_ENGINE = None
_SESSION_FACTORY: sessionmaker | None = None


def _database_url() -> str | None:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return None
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)

    # Ensure sslmode is set when using hosted Postgres (e.g. Supabase).
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if "sslmode" not in query and "supabase" in (parsed.hostname or ""):
        query["sslmode"] = ["require"]
        url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    return url


def get_engine():
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    url = _database_url()
    kwargs = {}
    if url.startswith("postgresql"):
        kwargs["connect_args"] = {"connect_timeout": 10}

    _ENGINE = create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        **kwargs
    )
    return _ENGINE


def get_session_factory() -> sessionmaker | None:
    global _SESSION_FACTORY
    if _SESSION_FACTORY is not None:
        return _SESSION_FACTORY
    engine = get_engine()
    if engine is None:
        return None
    _SESSION_FACTORY = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    return _SESSION_FACTORY


def init_db() -> bool:
    engine = get_engine()
    if engine is None:
        return False
    Base.metadata.create_all(engine)
    return True


def new_session() -> Optional[Session]:
    factory = get_session_factory()
    if factory is None:
        return None
    return factory()


def get_db():
    session = new_session()
    if session is None:
        return
    try:
        yield session
    finally:
        session.close()
