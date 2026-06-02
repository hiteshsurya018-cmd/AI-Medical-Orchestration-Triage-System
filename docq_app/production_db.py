from __future__ import annotations

from dataclasses import dataclass
import sqlite3

try:  # pragma: no cover - optional dependency path
    from sqlalchemy import create_engine, text
except Exception:  # pragma: no cover
    create_engine = None
    text = None


@dataclass(frozen=True)
class DatabaseRuntime:
    url: str
    dialect: str


def build_database_runtime(database_url: str) -> DatabaseRuntime:
    dialect = database_url.split(":", 1)[0]
    return DatabaseRuntime(url=database_url, dialect=dialect)


def create_sqlalchemy_engine(database_url: str):
    if create_engine is None:
        raise RuntimeError("SQLAlchemy is not installed in the current runtime.")
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, pool_pre_ping=True, future=True, connect_args=connect_args)


def check_database_readiness(database_url: str) -> bool:
    if database_url.startswith("sqlite:///"):
        sqlite_path = database_url.replace("sqlite:///", "", 1)
        connection = sqlite3.connect(sqlite_path)
        try:
            connection.execute("SELECT 1")
            return True
        finally:
            connection.close()
    if create_engine is None or text is None:
        return False
    engine = create_sqlalchemy_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    finally:
        engine.dispose()


def json_payload_type(dialect: str) -> str:
    return "JSONB" if dialect.startswith("postgres") else "TEXT"
