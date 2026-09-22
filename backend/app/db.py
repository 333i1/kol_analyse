
"""SQLAlchemy engine / session / Base."""
from __future__ import annotations

from collections.abc import Generator
from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
SessionLocal: Optional[sessionmaker] = None


def _make_engine(url: Optional[str] = None, *, static_pool: bool = False):
    from sqlalchemy.pool import StaticPool

    settings = get_settings()
    db_url = url or f"sqlite:///{settings.SQLITE_PATH}"
    # timeout=5s is sqlite3 busy timeout (seconds); PRAGMA busy_timeout is ms.
    connect_args = {"check_same_thread": False, "timeout": 5.0}
    kwargs = {"connect_args": connect_args, "future": True}
    if static_pool or db_url.startswith("sqlite:///:memory:"):
        kwargs["poolclass"] = StaticPool
    engine = create_engine(db_url, **kwargs)

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    return engine


def init_engine(url: Optional[str] = None, *, static_pool: bool = False):
    global _engine, SessionLocal
    _engine = _make_engine(url, static_pool=static_pool)
    SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    return _engine


def get_engine():
    global _engine
    if _engine is None:
        init_engine()
    return _engine


def init_db(url: Optional[str] = None, *, static_pool: bool = False):
    global _engine, SessionLocal
    # Tests may already have initialized an in-memory StaticPool engine.
    # Lifespan must not silently replace it with the file SQLite path.
    if url is None and _engine is not None and SessionLocal is not None:
        import app.models  # noqa: F401
        Base.metadata.create_all(bind=_engine)
        return _engine
    engine = init_engine(url, static_pool=static_pool)
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    return engine


def get_db() -> Generator[Session, None, None]:
    if SessionLocal is None:
        init_db()
    assert SessionLocal is not None
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
