from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from flask import current_app, has_app_context

from .production_db import create_sqlalchemy_engine

try:  # pragma: no cover - optional dependency path
    from sqlalchemy import text
except Exception:  # pragma: no cover
    text = None


_NAMED_PARAM_RE = re.compile(r":([a-zA-Z_][a-zA-Z0-9_]*)")
_POSITIONAL_PARAM_RE = re.compile(r"\?")
_ENGINE_CACHE: dict[str, Any] = {}
_RUNTIME_CONFIG_CACHE: dict[str, Any] | None = None


class CompatCursor:
    def __init__(self, cursor, *, lastrowid: int | None = None):
        self._cursor = cursor
        self.lastrowid = lastrowid if lastrowid is not None else getattr(cursor, "lastrowid", None)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)


@dataclass
class SqlAlchemyCompatConnection:
    raw_connection: Any
    dialect: str

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            try:
                self.raw_connection.rollback()
            finally:
                self.raw_connection.close()
            return False
        try:
            self.raw_connection.commit()
        finally:
            self.raw_connection.close()
        return False

    def execute(self, query: str, params: dict[str, Any] | tuple[Any, ...] | list[Any] | None = None):
        cursor = self.raw_connection.cursor()
        if isinstance(params, dict):
            named_query = _convert_named_params_for_psycopg(query)
            cursor.execute(named_query, params)
        else:
            positional_query = _convert_positional_params_for_psycopg(query)
            cursor.execute(positional_query, tuple(params or ()))
        lastrowid = getattr(cursor, "lastrowid", None)
        if lastrowid is None and query.lstrip().upper().startswith("INSERT INTO"):
            try:
                table_name = query.split("INSERT INTO", 1)[1].strip().split("(", 1)[0].strip().split()[0]
                lookup = self.raw_connection.cursor()
                lookup.execute(f"SELECT currval(pg_get_serial_sequence('{table_name}', 'id'))")
                row = lookup.fetchone()
                if row:
                    lastrowid = int(row[0])
                lookup.close()
            except Exception:
                lastrowid = None
        return CompatCursor(cursor, lastrowid=lastrowid)

    def commit(self) -> None:
        self.raw_connection.commit()

    def rollback(self) -> None:
        self.raw_connection.rollback()

    def close(self) -> None:
        self.raw_connection.close()


def _runtime_config() -> dict[str, Any]:
    global _RUNTIME_CONFIG_CACHE
    if has_app_context():
        _RUNTIME_CONFIG_CACHE = dict(current_app.config)
        return _RUNTIME_CONFIG_CACHE
    if _RUNTIME_CONFIG_CACHE is None:
        from .config import Config

        _RUNTIME_CONFIG_CACHE = dict(Config().__dict__)
    return _RUNTIME_CONFIG_CACHE


def _database_url() -> str:
    return str(_runtime_config().get("DATABASE_URL") or "")


def _sqlite_path(database_url: str) -> str:
    return database_url.replace("sqlite:///", "", 1)


def _get_engine(database_url: str):
    engine = _ENGINE_CACHE.get(database_url)
    if engine is None:
        engine = create_sqlalchemy_engine(database_url)
        _ENGINE_CACHE[database_url] = engine
    return engine


def _convert_positional_params_for_psycopg(query: str) -> str:
    return _POSITIONAL_PARAM_RE.sub("%s", query)


def _convert_named_params_for_psycopg(query: str) -> str:
    return _NAMED_PARAM_RE.sub(r"%(\1)s", query)


def get_connection():
    database_url = _database_url()
    if database_url.startswith("sqlite:///") or not database_url:
        db_config = _runtime_config()
        db_path = _sqlite_path(database_url) if database_url.startswith("sqlite:///") else str(db_config["DB_PATH"])
        connection = sqlite3.connect(db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection
    engine = _get_engine(database_url)
    raw_connection = engine.raw_connection()
    return SqlAlchemyCompatConnection(raw_connection=raw_connection, dialect=database_url.split(":", 1)[0])


@contextmanager
def transaction_scope():
    connection = get_connection()
    try:
        if isinstance(connection, sqlite3.Connection):
            connection.execute("BEGIN IMMEDIATE")
        else:
            connection.raw_connection.autocommit = False
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
