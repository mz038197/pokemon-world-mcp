from __future__ import annotations

from pokemon_world_mcp.catalog import Catalog
from pokemon_world_mcp.game import GameError, GameService
from pokemon_world_mcp.save_store import MemorySaveStore
from pokemon_world_mcp.world import look_window, terrain_at


def test_terrain_and_look() -> None:
    assert terrain_at(0, 0) == "path"
    cells = look_window(0, 0)
    assert any(c["here"] for c in cells)
    assert all("terrain" in c for c in cells)


def test_move_and_bounds() -> None:
    svc = GameService(MemorySaveStore(), Catalog())
    svc.new_game(1, "bulbasaur")
    # Going west from (0,0) is OOB
    try:
        svc.move(1, "W")
        assert False, "expected GameError"
    except GameError as exc:
        assert "bounds" in str(exc).lower() or "cannot" in str(exc).lower()


def test_new_game_and_party() -> None:
    svc = GameService(MemorySaveStore(), Catalog())
    out = svc.new_game(7, "bulbasaur")
    assert out["ok"] is True
    party = svc.party(7)
    assert party["party"][0]["name"] == "bulbasaur"
    assert party["party"][0]["level"] == 5
    st = svc.get_status(7)
    assert st["phase"] == "exploring"
    assert st["x"] == 0 and st["y"] == 0


def test_forced_encounter_and_catch_path() -> None:
    store = MemorySaveStore()
    svc = GameService(store, Catalog())
    svc.new_game(2, "bulbasaur")
    state = store.load(2)
    assert state is not None
    # Force next grass step to encounter via rng and position on grass
    # Find a grass tile adjacent walk
    state.x, state.y = 1, 0  # row0: PPPPGG... so (4,0) is grass
    state.x, state.y = 4, 0
    assert terrain_at(state.x, state.y) == "grass"
    state.rng_state = 1
    store.save(state)

    # Monkey encounter by calling internal flow: set encounter rate by
    # repeatedly moving in grass with controlled rng — simpler: inject battle
    from pokemon_world_mcp.models import BattleState, PokemonInstance

    enemy = PokemonInstance.from_species(Catalog().get("rattata"), level=5)
    enemy.hp = 1
    state.phase = "battle"
    state.battle = BattleState(enemy=enemy, last_log=["test"])
    store.save(state)

    # Catch with high chance (low HP) — may still fail; retry with fixed rng
    caught = False
    for seed in range(1, 50):
        state = store.load(2)
        assert state is not None
        state.rng_state = seed
        enemy = PokemonInstance.from_species(Catalog().get("rattata"), level=5)
        enemy.hp = 1
        state.phase = "battle"
        state.battle = BattleState(enemy=enemy, last_log=[])
        state.party = state.party[:1]  # only starter
        store.save(state)
        result = svc.battle_action(2, "catch")
        if result.get("caught"):
            caught = True
            break
    assert caught
    names = {p["name"] for p in svc.party(2)["party"]}
    assert "rattata" in names