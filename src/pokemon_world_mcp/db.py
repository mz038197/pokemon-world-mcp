from __future__ import annotations

import logging
import os
from pathlib import Path

import psycopg

logger = logging.getLogger(__name__)

APP_HOME_DIR = ".pokemon-world-mcp"
DEFAULT_SQLITE_DB_NAME = "pokemon_world.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pokemon_saves (
    user_id BIGINT PRIMARY KEY,
    phase TEXT NOT NULL,
    x INT NOT NULL,
    y INT NOT NULL,
    party JSONB NOT NULL DEFAULT '[]'::jsonb,
    battle JSONB,
    flags JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pokemon_catalog_cache (
    id INT PRIMARY KEY CHECK (id = 1),
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

SQLITE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pokemon_saves (
    user_id INTEGER PRIMARY KEY,
    phase TEXT NOT NULL,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    party TEXT NOT NULL DEFAULT '[]',
    battle TEXT,
    flags TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pokemon_catalog_cache (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def database_url_from_env() -> str | None:
    """Router Neon — api_keys only (auth)."""
    raw = os.environ.get("DATABASE_URL") or None
    return normalize_database_url(raw) if raw else None


def pokemon_database_url_from_env() -> str | None:
    """Game Neon — pokemon_saves + pokemon_catalog_cache."""
    raw = os.environ.get("POKEMON_DATABASE_URL") or None
    return normalize_database_url(raw) if raw else None


def default_sqlite_path() -> Path:
    return Path.home() / APP_HOME_DIR / DEFAULT_SQLITE_DB_NAME


def sqlite_path_from_env() -> Path:
    raw = os.environ.get("SQLITE_PATH")
    if raw:
        return Path(raw).expanduser()
    return default_sqlite_path()


def ensure_schema(database_url: str) -> None:
    """Ensure game tables on the pokemon / game database URL (not router)."""
    url = normalize_database_url(database_url)
    with psycopg.connect(url) as conn:
        conn.execute(SCHEMA_SQL)
        conn.commit()
    logger.info("pokemon_saves + pokemon_catalog_cache schema ensured")
