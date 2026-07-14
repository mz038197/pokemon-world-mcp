from __future__ import annotations

from pokemon_world_mcp.growth import (
    MAX_LEVEL,
    MOVE_SLOT_LIMIT,
    calc_stats,
    exp_to_next,
    moves_for_level,
    try_learn_move,
)
from pokemon_world_mcp.models import MoveInfo, PokemonInstance, PendingLearn
from pokemon_world_mcp.game import GameError, GameService
from pokemon_world_mcp.save_store import MemorySaveStore
from pokemon_world_mcp.world import ALL_WILD_SPECIES, ZONE_POOLS, encounter_zone, wild_pool_for
from pokemon_world_mcp.catalog import Catalog


def test_exp_and_stats_scale() -> None:
    cat = Catalog()
    sp = cat.get("bulbasaur")
    hp5, atk5, _, _ = calc_stats(sp, 5)
    hp20, atk20, _, _ = calc_stats(sp, 20)
    assert hp20 > hp5
    assert atk20 > atk5
    assert exp_to_next(5) == 100
    assert exp_to_next(MAX_LEVEL) == 0


def test_moves_for_level_caps_at_four() -> None:
    cat = Catalog()
    moves = moves_for_level(cat.get("bulbasaur"), 50)
    assert 1 <= len(moves) <= MOVE_SLOT_LIMIT


def test_level_up_learns_and_evolves() -> None:
    store = MemorySaveStore()
    cat = Catalog()
    svc = GameService(store, cat)
    svc.new_game(1, "bulbasaur")
    state = store.load(1)
    assert state is not None
    mon = state.party[0]
    mon.level = 15
    mon.exp = exp_to_next(15)
    from pokemon_world_mcp.growth import apply_stats

    apply_stats(mon, cat.get("bulbasaur"), preserve_hp_ratio=False)
    store.save(state)
    log: list[str] = []
    svc._process_growth(state, 0, log)
    assert state.party[0].level >= 16
    assert state.party[0].name == "ivysaur"
    assert any("evolved" in line.lower() for line in log)


def test_pending_learn_blocks_move() -> None:
    store = MemorySaveStore()
    svc = GameService(store, Catalog())
    svc.new_game(2, "bulbasaur")
    state = store.load(2)
    assert state is not None
    mon = state.party[0]
    # Fill 4 moves
    mon.moves = [
        MoveInfo("a", "normal", 40),
        MoveInfo("b", "normal", 40),
        MoveInfo("c", "normal", 40),
        MoveInfo("d", "normal", 40),
    ]
    state.pending_learn = [
        PendingLearn(
            party_index=0,
            new_move=MoveInfo("razor-leaf", "grass", 55),
            reason="level_up",
        )
    ]
    store.save(state)
    try:
        svc.move(2, "E")
        assert False, "expected GameError"
    except GameError as exc:
        assert "pending" in str(exc).lower()


def test_replace_and_skip_learn() -> None:
    store = MemorySaveStore()
    svc = GameService(store, Catalog())
    svc.new_game(3, "charmander")
    state = store.load(3)
    assert state is not None
    mon = state.party[0]
    mon.moves = [
        MoveInfo("scratch", "normal", 40),
        MoveInfo("ember", "fire", 40),
        MoveInfo("x", "normal", 10),
        MoveInfo("y", "normal", 10),
    ]
    new_move = MoveInfo("fire-fang", "fire", 65)
    state.pending_learn = [
        PendingLearn(party_index=0, new_move=new_move, reason="level_up")
    ]
    store.save(state)
    out = svc.battle_action(3, "replace_move", forget_move_name="x")
    assert out["ok"] is True
    assert any("fire-fang" in line for line in out["log"])
    names = {m.name for m in store.load(3).party[0].moves}  # type: ignore[union-attr]
    assert "fire-fang" in names
    assert "x" not in names

    state = store.load(3)
    assert state is not None
    state.pending_learn = [
        PendingLearn(
            party_index=0,
            new_move=MoveInfo("dragon-breath", "dragon", 60),
            reason="level_up",
        )
    ]
    # refill to 4
    while len(state.party[0].moves) < 4:
        state.party[0].moves.append(MoveInfo(f"z{len(state.party[0].moves)}", "normal", 10))
    store.save(state)
    out2 = svc.battle_action(3, "skip_learn")
    assert out2["ok"] is True
    assert any("did not learn" in line for line in out2["log"])


def test_starters_and_illegal() -> None:
    svc = GameService(MemorySaveStore(), Catalog())
    for name in ("bulbasaur", "charmander", "squirtle"):
        out = svc.new_game(10, name)
        assert out["ok"] is True
        assert svc.party(10)["party"][0]["name"] == name
        assert svc.party(10)["party"][0]["level"] == 5
    try:
        svc.new_game(11, "pikachu")
        assert False
    except GameError:
        pass


def test_zones_and_no_starters_in_wild() -> None:
    assert encounter_zone(0, 0) == "NW"
    assert encounter_zone(9, 0) == "NE"
    assert encounter_zone(0, 9) == "SW"
    assert encounter_zone(9, 9) == "SE"
    for pool in ZONE_POOLS.values():
        for name in pool:
            assert name not in {"bulbasaur", "charmander", "squirtle"}
    assert "bulbasaur" not in ALL_WILD_SPECIES
    assert set(wild_pool_for(1, 1)) == set(ZONE_POOLS["NW"])


def test_legacy_save_without_level() -> None:
    data = {
        "name": "pikachu",
        "types": ["electric"],
        "max_hp": 35,
        "hp": 35,
        "attack": 55,
        "defense": 40,
        "speed": 90,
        "moves": [{"name": "thunder-shock", "type": "electric", "power": 40}],
    }
    mon = PokemonInstance.from_dict(data)
    assert mon.level == 5
    assert mon.exp == 0


def test_try_learn_when_slot_free() -> None:
    cat = Catalog()
    mon = PokemonInstance.from_species(cat.get("rattata"), level=1)
    mon.moves = [MoveInfo("tackle", "normal", 40)]
    msg = try_learn_move(mon, MoveInfo("bite", "normal", 60))
    assert msg is not None
    assert "bite" in msg
