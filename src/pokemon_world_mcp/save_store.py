from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from pokemon_world_mcp.db import (
    SQLITE_SCHEMA_SQL,
    ensure_schema,
    normalize_database_url,
)
from pokemon_world_mcp.models import GameState

logger = logging.getLogger(__name__)


class SaveStore(Protocol):
    def load(self, user_id: int) -> GameState | None: ...

    def save(self, state: GameState) -> None: ...

    def delete(self, user_id: int) -> None: ...


def _state_from_row(row: dict[str, Any]) -> GameState:
    flags = row["flags"] or {}
    if isinstance(flags, str):
        flags = json.loads(flags) if flags else {}
    party = row["party"] or []
    if isinstance(party, str):
        party = json.loads(party) if party else []
    battle = row["battle"]
    if isinstance(battle, str):
        battle = json.loads(battle) if battle else None
    return GameState.from_dict(
        {
            "user_id": int(row["user_id"]),
            "phase": row["phase"],
            "x": int(row["x"]),
            "y": int(row["y"]),
            "party": party,
            "battle": battle,
            "rng_state": int(flags.get("rng_state") or 1),
            "steps": int(flags.get("steps") or 0),
            "won": bool(flags.get("won")),
            "pending_learn": flags.get("pending_learn") or [],
        }
    )


class MemorySaveStore:
    """In-memory store for unit tests."""

    def __init__(self) -> None:
        self._data: dict[int, GameState] = {}

    def load(self, user_id: int) -> GameState | None:
        state = self._data.get(user_id)
        if state is None:
            return None
        return GameState.from_dict(state.to_dict())

    def save(self, state: GameState) -> None:
        self._data[state.user_id] = GameState.from_dict(state.to_dict())

    def delete(self, user_id: int) -> None:
        self._data.pop(user_id, None)


class SqliteSaveStore:
    """File-backed store for local dev when DATABASE_URL is unset."""

    def __init__(self, path: str | Path, *, ensure: bool = True) -> None:
        self.path = Path(path)
        if ensure:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.execute(SQLITE_SCHEMA_SQL)
                conn.commit()
            logger.info("sqlite pokemon_saves schema ensured at %s", self.path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def load(self, user_id: int) -> GameState | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT user_id, phase, x, y, party, battle, flags
                FROM pokemon_saves
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return _state_from_row(dict(row))

    def save(self, state: GameState) -> None:
        flags = {
            "rng_state": state.rng_state,
            "steps": state.steps,
            "won": state.won,
            "pending_learn": [p.to_dict() for p in state.pending_learn],
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pokemon_saves
                    (user_id, phase, x, y, party, battle, flags, updated_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET
                    phase = excluded.phase,
                    x = excluded.x,
                    y = excluded.y,
                    party = excluded.party,
                    battle = excluded.battle,
                    flags = excluded.flags,
                    updated_at = datetime('now')
                """,
                (
                    state.user_id,
                    state.phase,
                    state.x,
                    state.y,
                    json.dumps([p.to_dict() for p in state.party]),
                    json.dumps(state.battle.to_dict()) if state.battle else None,
                    json.dumps(flags),
                ),
            )
            conn.commit()

    def delete(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM pokemon_saves WHERE user_id = ?", (user_id,))
            conn.commit()


class PostgresSaveStore:
    def __init__(self, database_url: str, *, ensure: bool = True) -> None:
        self.database_url = normalize_database_url(database_url)
        if ensure:
            ensure_schema(self.database_url)

    def load(self, user_id: int) -> GameState | None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                SELECT user_id, phase, x, y, party, battle, flags
                FROM pokemon_saves
                WHERE user_id = %s
                """,
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return _state_from_row(row)

    def save(self, state: GameState) -> None:
        flags = {
            "rng_state": state.rng_state,
            "steps": state.steps,
            "won": state.won,
            "pending_learn": [p.to_dict() for p in state.pending_learn],
        }
        with psycopg.connect(self.database_url) as conn:
            conn.execute(
                """
                INSERT INTO pokemon_saves
                    (user_id, phase, x, y, party, battle, flags, updated_at)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    phase = EXCLUDED.phase,
                    x = EXCLUDED.x,
                    y = EXCLUDED.y,
                    party = EXCLUDED.party,
                    battle = EXCLUDED.battle,
                    flags = EXCLUDED.flags,
                    updated_at = NOW()
                """,
                (
                    state.user_id,
                    state.phase,
                    state.x,
                    state.y,
                    Jsonb([p.to_dict() for p in state.party]),
                    Jsonb(state.battle.to_dict()) if state.battle else None,
                    Jsonb(flags),
                ),
            )
            conn.commit()

    def delete(self, user_id: int) -> None:
        with psycopg.connect(self.database_url) as conn:
            conn.execute("DELETE FROM pokemon_saves WHERE user_id = %s", (user_id,))
            conn.commit()
