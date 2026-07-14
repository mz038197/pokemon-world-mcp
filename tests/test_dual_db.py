from __future__ import annotations

import pytest

from pokemon_world_mcp.db import (
    database_url_from_env,
    normalize_database_url,
    pokemon_database_url_from_env,
)
from pokemon_world_mcp.drop_router_tables import validate_drop_env


def test_pokemon_database_url_separate_from_router(monkeypatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgres://router.example/db",
    )
    monkeypatch.setenv(
        "POKEMON_DATABASE_URL",
        "postgresql://game.example/db",
    )
    assert database_url_from_env() == "postgresql://router.example/db"
    assert pokemon_database_url_from_env() == "postgresql://game.example/db"
    assert database_url_from_env() != pokemon_database_url_from_env()


def test_pokemon_database_url_unset(monkeypatch) -> None:
    monkeypatch.delenv("POKEMON_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://router.example/db")
    assert pokemon_database_url_from_env() is None
    assert database_url_from_env() == "postgresql://router.example/db"


def test_validate_drop_refuses_same_urls(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://same.example/db")
    monkeypatch.setenv("POKEMON_DATABASE_URL", "postgresql://same.example/db")
    with pytest.raises(ValueError, match="must differ"):
        validate_drop_env()


def test_validate_drop_requires_game_url(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://router.example/db")
    monkeypatch.delenv("POKEMON_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="POKEMON_DATABASE_URL"):
        validate_drop_env()


def test_validate_drop_ok(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://router.example/db")
    monkeypatch.setenv("POKEMON_DATABASE_URL", "postgresql://game.example/db")
    assert validate_drop_env() == "postgresql://router.example/db"


def test_normalize_postgres_scheme() -> None:
    assert normalize_database_url("postgres://x") == "postgresql://x"
