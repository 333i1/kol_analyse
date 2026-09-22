
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("YOUTUBE_API_KEY", "")
os.environ.setdefault("LLM_API_KEY", "")
os.environ.setdefault("WHISPER_ENABLED", "false")

from app.config import get_settings
from app.db import Base, SessionLocal, init_db
from app.services import worker as worker_mod
from app.services.worker import WorkerDeps


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return BACKEND_ROOT.parent


@pytest.fixture()
def engine():
    get_settings.cache_clear()
    eng = init_db("sqlite:///:memory:", static_pool=True)
    yield eng
    Base.metadata.drop_all(bind=eng)


@pytest.fixture()
def db(engine) -> Session:
    assert SessionLocal is not None
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(engine):
    from app.main import app

    # lifespan would start worker + re-init_db on file sqlite; override
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_worker_deps():
    from app.services.channel_worker import reset_channel_queue
    from app.services.worker import reset_queue, set_deps
    set_deps(None)
    reset_queue()
    reset_channel_queue()
    yield
    set_deps(None)
    reset_queue()
    reset_channel_queue()


import tempfile
from pathlib import Path as _Path

def make_test_db_url() -> str:
    """Unique file SQLite per call — avoids :memory: cross-thread surprises."""
    fd, name = tempfile.mkstemp(prefix="mvp_test_", suffix=".db")
    import os
    os.close(fd)
    return "sqlite:///" + _Path(name).as_posix()
