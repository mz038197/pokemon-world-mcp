from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from pokemon_world_mcp.db import (
    SQLITE_SCHEMA_SQL,
    ensure_schema,
    normalize_database_url,
    pokemon_database_url_from_env,
    sqlite_path_from_env,
)
from pokemon_world_mcp.models import MoveInfo, Species

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
DEFAULT_TTL_HOURS = 24.0


@dataclass(frozen=True)
class CatalogCacheRow:
    species: dict[str, Species]
    updated_at: datetime


def catalog_cache_ttl_hours() -> float:
    raw = (os.environ.get("CATALOG_CACHE_TTL_HOURS") or "").strip()
    if not raw:
        return DEFAULT_TTL_HOURS
    try:
        return float(raw)
    except ValueError:
        logger.warning("invalid CATALOG_CACHE_TTL_HOURS=%r; using %s", raw, DEFAULT_TTL_HOURS)
        return DEFAULT_TTL_HOURS


def is_fresh(updated_at: datetime, *, ttl_hours: float | None = None) -> bool:
    ttl = catalog_cache_ttl_hours() if ttl_hours is None else ttl_hours
    now = datetime.now(timezone.utc)
    ts = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() < ttl * 3600


def _parse_updated_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # SQLite datetime('now') is UTC-ish naive
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def species_to_dict(species: Species) -> dict[str, Any]:
    return {
        "types": list(species.types),
        "hp": species.hp,
        "attack": species.attack,
        "defense": species.defense,
        "speed": species.speed,
        "base_experience": species.base_experience,
        "growth_rate": species.growth_rate,
        "learnset": [
            {
                "level": level,
                "name": move.name,
                "type": move.type,
                "power": move.power,
            }
            for level, move in species.learnset
        ],
    }


def species_from_dict(name: str, data: dict[str, Any]) -> Species:
    learnset: list[tuple[int, MoveInfo]] = []
    for item in data.get("learnset") or []:
        learnset.append(
            (
                int(item["level"]),
                MoveInfo(
                    name=str(item["name"]),
                    type=str(item["type"]),
                    power=int(item["power"]),
                ),
            )
        )
    return Species(
        name=name,
        types=list(data.get("types") or []),
        hp=int(data["hp"]),
        attack=int(data["attack"]),
        defense=int(data["defense"]),
        speed=int(data["speed"]),
        learnset=learnset,
        base_experience=int(data.get("base_experience") or 64),
        growth_rate=str(data.get("growth_rate") or "medium-slow"),
    )


def catalog_payload_from_species(species: dict[str, Species]) -> dict[str, Any]:
    return {
        "version": CACHE_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "species": {name: species_to_dict(sp) for name, sp in species.items()},
    }


def species_from_catalog_payload(payload: dict[str, Any]) -> dict[str, Species] | None:
    raw = payload.get("species")
    if not isinstance(raw, dict) or not raw:
        return None
    out: dict[str, Species] = {}
    for name, data in raw.items():
        if not isinstance(data, dict):
            continue
        out[str(name)] = species_from_dict(str(name), data)
    return out or None


def _ensure_sqlite_schema(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SQLITE_SCHEMA_SQL)
        conn.commit()


def load_catalog_cache() -> dict[str, Species] | None:
    row = load_catalog_cache_row()
    return row.species if row else None


def load_catalog_cache_row() -> CatalogCacheRow | None:
    url = pokemon_database_url_from_env()
    try:
        if url:
            return _load_postgres(url)
        return _load_sqlite(sqlite_path_from_env())
    except Exception:
        logger.exception("failed to load catalog cache")
        return None


def save_catalog_cache(species: dict[str, Species]) -> None:
    payload = catalog_payload_from_species(species)
    url = pokemon_database_url_from_env()
    try:
        if url:
            _save_postgres(url, payload)
        else:
            _save_sqlite(sqlite_path_from_env(), payload)
        logger.info("catalog cache saved (%s species)", len(species))
    except Exception:
        logger.exception("failed to save catalog cache")


def _row_from_payload_and_updated(
    payload: Any,
    updated_at_raw: Any,
) -> CatalogCacheRow | None:
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        return None
    species = species_from_catalog_payload(payload)
    if not species:
        return None
    updated_at = _parse_updated_at(updated_at_raw) or _parse_updated_at(
        payload.get("updated_at")
    )
    if updated_at is None:
        updated_at = datetime.now(timezone.utc)
    return CatalogCacheRow(species=species, updated_at=updated_at)


def _load_postgres(database_url: str) -> CatalogCacheRow | None:
    ensure_schema(database_url)
    url = normalize_database_url(database_url)
    with psycopg.connect(url, row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT payload, updated_at FROM pokemon_catalog_cache WHERE id = 1"
        ).fetchone()
    if not row:
        return None
    return _row_from_payload_and_updated(row["payload"], row["updated_at"])


def _save_postgres(database_url: str, payload: dict[str, Any]) -> None:
    ensure_schema(database_url)
    url = normalize_database_url(database_url)
    with psycopg.connect(url) as conn:
        conn.execute(
            """
            INSERT INTO pokemon_catalog_cache (id, payload, updated_at)
            VALUES (1, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                payload = EXCLUDED.payload,
                updated_at = NOW()
            """,
            (Jsonb(payload),),
        )
        conn.commit()


def _load_sqlite(path) -> CatalogCacheRow | None:
    _ensure_sqlite_schema(path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT payload, updated_at FROM pokemon_catalog_cache WHERE id = 1"
        ).fetchone()
    if not row:
        return None
    return _row_from_payload_and_updated(row["payload"], row["updated_at"])


def _save_sqlite(path, payload: dict[str, Any]) -> None:
    _ensure_sqlite_schema(path)
    blob = json.dumps(payload, ensure_ascii=False)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO pokemon_catalog_cache (id, payload, updated_at)
            VALUES (1, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                payload = excluded.payload,
                updated_at = datetime('now')
            """,
            (blob,),
        )
        conn.commit()
