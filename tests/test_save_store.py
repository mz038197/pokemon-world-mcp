from __future__ import annotations

from pokemon_world_mcp.catalog import Catalog
from pokemon_world_mcp.game import GameService
from pokemon_world_mcp.models import GameState, PokemonInstance
from pokemon_world_mcp.save_store import MemorySaveStore, SqliteSaveStore


def test_memory_save_roundtrip() -> None:
    store = MemorySaveStore()
    cat = Catalog()
    state = GameState(
        user_id=99,
        phase="exploring",
        x=3,
        y=4,
        party=[PokemonInstance.from_species(cat.get("pikachu"))],
        battle=None,
        rng_state=123,
        steps=10,
        won=False,
    )
    store.save(state)
    loaded = store.load(99)
    assert loaded is not None
    assert loaded.x == 3 and loaded.y == 4
    assert loaded.party[0].name == "pikachu"
    assert loaded.steps == 10

    other = MemorySaveStore()
    # Simulates "new process" only for memory — copy via dict roundtrip
    other.save(loaded)
    again = other.load(99)
    assert again is not None
    assert again.party[0].name == "pikachu"


def test_game_persists_after_move() -> None:
    store = MemorySaveStore()
    svc = GameService(store, Catalog())
    svc.new_game(5, "bulbasaur")
    svc.move(5, "E")
    loaded = store.load(5)
    assert loaded is not None
    assert loaded.x == 1
    assert loaded.y == 0
    assert loaded.steps == 1


def test_sqlite_save_roundtrip(tmp_path) -> None:
    db_path = tmp_path / "pokemon_world.db"
    store = SqliteSaveStore(db_path)
    cat = Catalog()
    state = GameState(
        user_id=42,
        phase="exploring",
        x=2,
        y=5,
        party=[PokemonInstance.from_species(cat.get("bulbasaur"))],
        battle=None,
        rng_state=7,
        steps=3,
        won=False,
    )
    store.save(state)

    reopened = SqliteSaveStore(db_path)
    loaded = reopened.load(42)
    assert loaded is not None
    assert loaded.x == 2 and loaded.y == 5
    assert loaded.party[0].name == "bulbasaur"
    assert loaded.party[0].level == 5
    assert loaded.steps == 3
    assert loaded.rng_state == 7

    reopened.delete(42)
    assert reopened.load(42) is None


def test_sqlite_game_persists_across_store_instances(tmp_path) -> None:
    db_path = tmp_path / "saves.db"
    svc = GameService(SqliteSaveStore(db_path), Catalog())
    svc.new_game(11, "charmander")
    svc.move(11, "S")

    loaded = SqliteSaveStore(db_path).load(11)
    assert loaded is not None
    assert loaded.x == 0
    assert loaded.y == 1
    assert loaded.steps == 1
    assert loaded.party[0].name == "charmander"