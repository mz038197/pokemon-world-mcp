from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pokemon_world_mcp.catalog import Catalog, _fallback_species
from pokemon_world_mcp.catalog_cache import (
    catalog_cache_ttl_hours,
    catalog_payload_from_species,
    is_fresh,
    load_catalog_cache,
    load_catalog_cache_row,
    save_catalog_cache,
    species_from_catalog_payload,
    species_from_dict,
    species_to_dict,
)
from pokemon_world_mcp.models import MoveInfo, Species


def _clear_db_env(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POKEMON_DATABASE_URL", raising=False)
    Catalog._set_api_fail_at(None)


def test_species_roundtrip_dict() -> None:
    sp = _fallback_species()["bulbasaur"]
    data = species_to_dict(sp)
    again = species_from_dict("bulbasaur", data)
    assert again.name == "bulbasaur"
    assert again.hp == sp.hp
    assert again.base_experience == sp.base_experience
    assert again.growth_rate == sp.growth_rate
    assert len(again.learnset) == len(sp.learnset)
    assert again.learnset[0][1].name == sp.learnset[0][1].name


def test_payload_roundtrip() -> None:
    species = _fallback_species()
    payload = catalog_payload_from_species(species)
    assert payload["version"] == 1
    loaded = species_from_catalog_payload(payload)
    assert loaded is not None
    assert "pikachu" in loaded
    assert loaded["pikachu"].growth_rate == "medium"


def test_is_fresh_and_ttl(monkeypatch) -> None:
    monkeypatch.delenv("CATALOG_CACHE_TTL_HOURS", raising=False)
    assert catalog_cache_ttl_hours() == 24.0
    now = datetime.now(timezone.utc)
    assert is_fresh(now - timedelta(hours=23)) is True
    assert is_fresh(now - timedelta(hours=25)) is False
    monkeypatch.setenv("CATALOG_CACHE_TTL_HOURS", "1")
    assert catalog_cache_ttl_hours() == 1.0
    assert is_fresh(now - timedelta(minutes=30), ttl_hours=1) is True
    assert is_fresh(now - timedelta(hours=2), ttl_hours=1) is False


def test_sqlite_catalog_cache_persist(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "pokemon_world.db"
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(db))

    species = _fallback_species()
    save_catalog_cache(species)
    loaded = load_catalog_cache()
    assert loaded is not None
    assert set(loaded) == set(species)
    assert loaded["squirtle"].base_experience == species["squirtle"].base_experience
    row = load_catalog_cache_row()
    assert row is not None
    assert is_fresh(row.updated_at)


def test_catalog_load_uses_cache_without_network(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "cache.db"
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(db))

    species = _fallback_species()
    species["bulbasaur"].base_experience = 999
    save_catalog_cache(species)

    def boom(*_a, **_k):
        raise AssertionError("must not call PokéAPI when cache is fresh")

    monkeypatch.setattr("pokemon_world_mcp.catalog._fetch_species", boom)
    monkeypatch.setattr("pokemon_world_mcp.catalog._refresh_growth_tables", boom)

    cat = Catalog.load(timeout=0.1)
    assert cat.get("bulbasaur").base_experience == 999


def test_catalog_load_empty_fetches_api_and_saves(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "empty.db"
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(db))

    fake = {
        "bulbasaur": Species(
            name="bulbasaur",
            types=["grass"],
            hp=45,
            attack=49,
            defense=49,
            speed=45,
            learnset=[(1, MoveInfo(name="tackle", type="normal", power=40))],
            base_experience=777,
            growth_rate="medium-slow",
        )
    }

    monkeypatch.setattr(
        "pokemon_world_mcp.catalog._fetch_species",
        lambda *_a, **_k: fake,
    )
    monkeypatch.setattr(
        "pokemon_world_mcp.catalog._refresh_growth_tables",
        lambda **_k: None,
    )

    cat = Catalog.load(timeout=0.1)
    assert cat.get("bulbasaur").base_experience == 777
    again = load_catalog_cache()
    assert again is not None
    assert again["bulbasaur"].base_experience == 777


def test_catalog_load_api_failure_fallback_does_not_write(
    tmp_path: Path, monkeypatch
) -> None:
    db = tmp_path / "fail.db"
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(db))

    def boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr("pokemon_world_mcp.catalog._fetch_species", boom)
    monkeypatch.setattr("pokemon_world_mcp.catalog._refresh_growth_tables", boom)

    cat = Catalog.load(timeout=0.1)
    assert "bulbasaur" in cat.names()
    assert load_catalog_cache() is None


def test_stale_cache_api_fail_preserves_updated_at_clock(
    tmp_path: Path, monkeypatch
) -> None:
    """API failure must not reset TTL; backoff prevents hammering instead."""
    db = tmp_path / "stale_fail.db"
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(db))
    monkeypatch.setenv("CATALOG_CACHE_TTL_HOURS", "24")

    species = _fallback_species()
    species["bulbasaur"].base_experience = 555
    save_catalog_cache(species)
    age_hours = 30 * 24  # 30 days
    old = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE pokemon_catalog_cache SET updated_at = ? WHERE id = 1",
            (old,),
        )
        conn.commit()

    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise RuntimeError("network down")

    monkeypatch.setattr("pokemon_world_mcp.catalog._fetch_species", boom)
    monkeypatch.setattr("pokemon_world_mcp.catalog._refresh_growth_tables", boom)

    cat = Catalog.load(timeout=0.1)
    assert cat.get("bulbasaur").base_experience == 555
    age_sec = time.time() - cat._loaded_at
    assert age_sec > 29 * 24 * 3600
    assert calls["n"] == 1

    # ensure_fresh sees expired clock but backoff skips a second API hit
    cat.ensure_fresh()
    assert calls["n"] == 1
    assert cat.get("bulbasaur").base_experience == 555


def test_catalog_stale_cache_refetches(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "stale.db"
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(db))
    monkeypatch.setenv("CATALOG_CACHE_TTL_HOURS", "24")

    species = _fallback_species()
    species["bulbasaur"].base_experience = 111
    save_catalog_cache(species)

    # Backdate updated_at beyond TTL
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE pokemon_catalog_cache SET updated_at = ? WHERE id = 1",
            (old,),
        )
        conn.commit()

    fake = {
        "bulbasaur": Species(
            name="bulbasaur",
            types=["grass"],
            hp=45,
            attack=49,
            defense=49,
            speed=45,
            learnset=[(1, MoveInfo(name="tackle", type="normal", power=40))],
            base_experience=222,
            growth_rate="medium-slow",
        )
    }
    called = {"n": 0}

    def fetch(*_a, **_k):
        called["n"] += 1
        return fake

    monkeypatch.setattr("pokemon_world_mcp.catalog._fetch_species", fetch)
    monkeypatch.setattr(
        "pokemon_world_mcp.catalog._refresh_growth_tables",
        lambda **_k: None,
    )

    cat = Catalog.load(timeout=0.1)
    assert called["n"] == 1
    assert cat.get("bulbasaur").base_experience == 222


def test_ensure_fresh_reloads_when_memory_expired(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "mem.db"
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(db))
    monkeypatch.setenv("CATALOG_CACHE_TTL_HOURS", "24")

    species = _fallback_species()
    species["bulbasaur"].base_experience = 333
    save_catalog_cache(species)

    monkeypatch.setattr(
        "pokemon_world_mcp.catalog._fetch_species",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no fetch")),
    )
    monkeypatch.setattr(
        "pokemon_world_mcp.catalog._refresh_growth_tables",
        lambda **_k: None,
    )

    cat = Catalog.load(timeout=0.1)
    assert cat.get("bulbasaur").base_experience == 333

    # Expire in-memory age; DB still fresh → reload from DB without API
    cat._loaded_at = time.time() - (25 * 3600)
    assert cat.get("bulbasaur").base_experience == 333
    assert (time.time() - cat._loaded_at) < 60


def test_loaded_at_follows_cache_updated_at(tmp_path: Path, monkeypatch) -> None:
    """Fresh cache must not reset the TTL clock to now (avoid ~47h window)."""
    db = tmp_path / "ttl_clock.db"
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(db))
    monkeypatch.setenv("CATALOG_CACHE_TTL_HOURS", "24")

    species = _fallback_species()
    save_catalog_cache(species)
    age_hours = 23
    old = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE pokemon_catalog_cache SET updated_at = ? WHERE id = 1",
            (old,),
        )
        conn.commit()

    monkeypatch.setattr(
        "pokemon_world_mcp.catalog._fetch_species",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("cache still fresh")),
    )
    monkeypatch.setattr(
        "pokemon_world_mcp.catalog._refresh_growth_tables",
        lambda **_k: None,
    )

    cat = Catalog.load(timeout=0.1)
    age_sec = time.time() - cat._loaded_at
    assert abs(age_sec - age_hours * 3600) < 120
    # Remaining TTL ~1h, not a full new 24h from load time
    assert age_sec > 22 * 3600
