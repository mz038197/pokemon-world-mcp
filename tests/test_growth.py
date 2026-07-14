from __future__ import annotations

from pokemon_world_mcp.catalog import Catalog
import copy

import pytest

from pokemon_world_mcp.experience import (
    GROWTH_TABLES,
    MAX_LEVEL,
    apply_growth_tables_from_api,
    battle_exp_yield,
    exp_to_next,
    total_exp_at,
)
from pokemon_world_mcp.growth import (
    MOVE_SLOT_LIMIT,
    apply_stats,
    calc_stats,
    moves_for_level,
    try_learn_move,
)
from pokemon_world_mcp.models import MoveInfo, PendingLearn, PokemonInstance
from pokemon_world_mcp.game import GameError, GameService
from pokemon_world_mcp.save_store import MemorySaveStore
from pokemon_world_mcp.world import ALL_WILD_SPECIES, ZONE_POOLS, encounter_zone, wild_pool_for


def test_official_growth_anchors() -> None:
    assert total_exp_at("medium", 100) == 1_000_000
    assert total_exp_at("slow", 100) == 1_250_000
    assert total_exp_at("fast", 100) == 800_000
    assert total_exp_at("medium-slow", 100) == 1_059_860
    assert total_exp_at("slow-then-very-fast", 100) == 600_000
    assert total_exp_at("fast-then-very-slow", 100) == 1_640_000
    assert total_exp_at("medium", 1) == 0
    assert total_exp_at("medium-slow", 5) == 135


def test_battle_exp_yield() -> None:
    assert battle_exp_yield(64, 5) == max(1, 64 * 5 // 7)
    assert battle_exp_yield(0, 10) == 1


def test_exp_to_next_total_scheme() -> None:
    total = total_exp_at("medium-slow", 5)
    assert exp_to_next("medium-slow", 5, total) == total_exp_at("medium-slow", 6) - total
    assert exp_to_next("medium", MAX_LEVEL, 1_000_000) == 0


def test_stats_scale() -> None:
    cat = Catalog()
    sp = cat.get("bulbasaur")
    hp5, atk5, _, _ = calc_stats(sp, 5)
    hp20, atk20, _, _ = calc_stats(sp, 20)
    assert hp20 > hp5
    assert atk20 > atk5


def test_moves_for_level_caps_at_four() -> None:
    cat = Catalog()
    moves = moves_for_level(cat.get("bulbasaur"), 50)
    assert 1 <= len(moves) <= MOVE_SLOT_LIMIT


def test_level_up_uses_total_exp_and_evolves() -> None:
    store = MemorySaveStore()
    cat = Catalog()
    svc = GameService(store, cat)
    svc.new_game(1, "bulbasaur")
    state = store.load(1)
    assert state is not None
    mon = state.party[0]
    assert mon.exp_scheme == "total"
    assert mon.exp == total_exp_at("medium-slow", 5)
    mon.level = 15
    mon.exp = total_exp_at("medium-slow", 16)  # enough to hit 16
    apply_stats(mon, cat.get("bulbasaur"), preserve_hp_ratio=False)
    store.save(state)
    log: list[str] = []
    before = mon.exp
    svc._process_growth(state, 0, log)
    assert state.party[0].level >= 16
    assert state.party[0].name == "ivysaur"
    assert state.party[0].exp == before  # total exp not deducted
    assert any("evolved" in line.lower() for line in log)


def test_starter_total_exp() -> None:
    mon = PokemonInstance.from_species(Catalog().get("charmander"), level=5)
    assert mon.exp == total_exp_at("medium-slow", 5)
    assert mon.exp_scheme == "total"


def test_legacy_exp_migration() -> None:
    store = MemorySaveStore()
    cat = Catalog()
    svc = GameService(store, cat)
    svc.new_game(9, "bulbasaur")
    state = store.load(9)
    assert state is not None
    state.party[0].exp_scheme = "legacy"
    state.party[0].exp = 50  # old relative progress
    state.party[0].level = 5
    store.save(state)
    loaded = svc._require(9)
    assert loaded.party[0].exp_scheme == "total"
    assert loaded.party[0].exp == total_exp_at("medium-slow", 5)


def test_pending_learn_blocks_move() -> None:
    store = MemorySaveStore()
    svc = GameService(store, Catalog())
    svc.new_game(2, "bulbasaur")
    state = store.load(2)
    assert state is not None
    mon = state.party[0]
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
    assert mon.exp_scheme == "legacy"


def test_try_learn_when_slot_free() -> None:
    cat = Catalog()
    mon = PokemonInstance.from_species(cat.get("rattata"), level=1)
    mon.moves = [MoveInfo("tackle", "normal", 40)]
    msg = try_learn_move(mon, MoveInfo("bite", "normal", 60))
    assert msg is not None
    assert "bite" in msg


def test_grant_exp_from_enemy_yield() -> None:
    store = MemorySaveStore()
    cat = Catalog()
    svc = GameService(store, cat)
    svc.new_game(20, "bulbasaur")
    state = store.load(20)
    assert state is not None
    enemy = PokemonInstance.from_species(cat.get("rattata"), level=5)
    before = state.party[0].exp
    log: list[str] = []
    svc._grant_exp_from_enemy(state, enemy, log)
    expected = battle_exp_yield(cat.get("rattata").base_experience, 5)
    assert state.party[0].exp == before + expected
    assert any(f"{expected} exp" in line for line in log)


def test_apply_growth_tables_rejects_sparse_zeros() -> None:
    """API-shaped tables with omitted levels (zeros) must not overwrite globals."""
    before = copy.deepcopy(GROWTH_TABLES)
    sparse = [0] * (MAX_LEVEL + 1)  # level 1 is 0; 2..100 left unset
    with pytest.raises(ValueError, match="missing or non-increasing"):
        apply_growth_tables_from_api({"medium": sparse})
    assert GROWTH_TABLES == before


def test_apply_growth_tables_rejects_partial_batch() -> None:
    """One bad table must not partially apply earlier good tables."""
    before = copy.deepcopy(GROWTH_TABLES)
    good = list(before["fast"])
    sparse = [0] * (MAX_LEVEL + 1)
    with pytest.raises(ValueError, match="missing or non-increasing"):
        apply_growth_tables_from_api({"fast": good, "medium": sparse})
    assert GROWTH_TABLES == before


def test_apply_growth_tables_accepts_complete_overlay() -> None:
    before = copy.deepcopy(GROWTH_TABLES)
    overlay = {name: list(values) for name, values in before.items()}
    apply_growth_tables_from_api(overlay)
    assert GROWTH_TABLES == before
