from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


def make_engine(url):
    engine = create_engine(
        url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def configure(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")

    return engine


settings.data_dir.mkdir(parents=True, exist_ok=True)
engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as session:
        yield session
