from __future__ import annotations

from pokemon_world_mcp.db import normalize_database_url, pokemon_database_url_from_env


def test_pokemon_database_url_is_read_from_its_own_setting(monkeypatch) -> None:
    monkeypatch.setenv("POKEMON_DATABASE_URL", "postgres://game.example/db")
    assert pokemon_database_url_from_env() == "postgresql://game.example/db"


def test_pokemon_database_url_unset(monkeypatch) -> None:
    monkeypatch.delenv("POKEMON_DATABASE_URL", raising=False)
    assert pokemon_database_url_from_env() is None


def test_normalize_postgres_scheme() -> None:
    assert normalize_database_url("postgres://x") == "postgresql://x"
