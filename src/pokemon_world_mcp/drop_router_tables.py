"""Drop pokemon_* tables from router Postgres (DATABASE_URL). Never touches api_keys."""

from __future__ import annotations

import psycopg

from pokemon_world_mcp.db import (
    database_url_from_env,
    normalize_database_url,
    pokemon_database_url_from_env,
)

DROP_SQL = """
DROP TABLE IF EXISTS pokemon_catalog_cache;
DROP TABLE IF EXISTS pokemon_saves;
"""


def validate_drop_env() -> str:
    """Return normalized router URL, or raise ValueError with a reason."""
    router = database_url_from_env()
    game = pokemon_database_url_from_env()
    if not router:
        raise ValueError("DATABASE_URL (router) is required")
    if not game:
        raise ValueError(
            "POKEMON_DATABASE_URL must be set (game DB) before dropping router tables"
        )
    router_n = normalize_database_url(router)
    game_n = normalize_database_url(game)
    if router_n == game_n:
        raise ValueError(
            "DATABASE_URL and POKEMON_DATABASE_URL must differ; refusing to DROP"
        )
    return router_n


def drop_router_pokemon_tables() -> None:
    url = validate_drop_env()
    with psycopg.connect(url) as conn:
        conn.execute(DROP_SQL)
        conn.commit()
