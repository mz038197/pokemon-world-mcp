from __future__ import annotations

import json

from pokemon_world_mcp.catalog import Catalog
from pokemon_world_mcp.game import GameService, dumps
from pokemon_world_mcp.save_store import MemorySaveStore


def test_tool_payload_json() -> None:
    svc = GameService(MemorySaveStore(), Catalog())
    raw = dumps(svc.new_game(3, "bulbasaur"))
    data = json.loads(raw)
    assert data["ok"] is True
    assert "status" in data


def test_phase_guard_battle_while_exploring() -> None:
    svc = GameService(MemorySaveStore(), Catalog())
    svc.new_game(4, "squirtle")
    try:
        svc.battle_status(4)
        assert False
    except Exception as exc:
        assert "not in battle" in str(exc).lower()