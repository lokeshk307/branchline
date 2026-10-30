from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

_engine = None
_Session: sessionmaker[Session] | None = None


class Base(DeclarativeBase):
    pass


def reset_db() -> None:
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None


def get_engine():
    global _engine, _Session
    if _engine is None:
        url = get_settings().database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args)
        _Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def get_session() -> Session:
    get_engine()
    assert _Session is not None
    return _Session()


def get_db() -> Iterator[Session]:
    db = get_session()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(get_engine())
