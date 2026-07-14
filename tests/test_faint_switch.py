from __future__ import annotations

import random

from pokemon_world_mcp.battle import flee_chance
from pokemon_world_mcp.catalog import Catalog
from pokemon_world_mcp.game import GameService
from pokemon_world_mcp.models import BattleState, GameState, PokemonInstance
from pokemon_world_mcp.save_store import MemorySaveStore


def _svc() -> tuple[GameService, Catalog, MemorySaveStore]:
    store = MemorySaveStore()
    cat = Catalog()
    return GameService(store, cat), cat, store


def _rng_seed_where_first_roll_fails(threshold: float) -> int:
    """Seed such that after GameService._rng advance, next random() >= threshold."""
    for seed in range(1, 10_000):
        r = random.Random(seed)
        r.randint(1, 2**31 - 1)
        if r.random() >= threshold:
            return seed
    raise AssertionError("no suitable rng seed")


def test_handle_active_fainted_switches_and_logs() -> None:
    svc, cat, _ = _svc()
    lead = PokemonInstance.from_species(cat.get("bulbasaur"))
    lead.hp = 0
    backup = PokemonInstance.from_species(cat.get("pikachu"))
    state = GameState(
        user_id=1,
        phase="battle",
        x=0,
        y=0,
        party=[lead, backup],
        battle=BattleState(enemy=PokemonInstance.from_species(cat.get("pidgey"))),
        rng_state=1,
        steps=0,
        won=False,
    )
    log: list[str] = []
    nxt = svc._handle_active_fainted(state, log)
    assert nxt is not None
    assert nxt.name == "pikachu"
    assert any("Go! pikachu" in line for line in log)
    assert state.phase == "battle"
    assert state.battle is not None


def test_handle_active_fainted_blackouts_when_party_wiped() -> None:
    svc, cat, _ = _svc()
    lead = PokemonInstance.from_species(cat.get("bulbasaur"))
    lead.hp = 0
    state = GameState(
        user_id=2,
        phase="battle",
        x=3,
        y=3,
        party=[lead],
        battle=BattleState(enemy=PokemonInstance.from_species(cat.get("pidgey"))),
        rng_state=1,
        steps=0,
        won=False,
    )
    log: list[str] = []
    assert svc._handle_active_fainted(state, log) is None
    assert state.phase == "exploring"
    assert state.battle is None
    assert any("blacked out" in line.lower() for line in log)


def test_do_fight_you_hp_is_switched_mon() -> None:
    svc, cat, store = _svc()
    lead = PokemonInstance.from_species(cat.get("bulbasaur"))
    lead.hp = 1
    lead.speed = 1
    backup = PokemonInstance.from_species(cat.get("pikachu"))
    enemy = PokemonInstance.from_species(cat.get("charmander"))
    enemy.speed = 999
    enemy.attack = 200
    state = GameState(
        user_id=10,
        phase="battle",
        x=0,
        y=0,
        party=[lead, backup],
        battle=BattleState(enemy=enemy),
        rng_state=99,
        steps=0,
        won=False,
    )
    store.save(state)
    result = svc.battle_action(10, "fight", lead.moves[0].name)
    assert result["ok"] is True
    assert result.get("blackout") is not True
    assert result["phase"] == "battle"
    assert any("fainted" in line.lower() for line in result["log"])
    assert any("Go! pikachu" in line for line in result["log"])
    assert result["you_hp"] == backup.max_hp
    assert result["you_hp"] > 0


def test_do_run_switches_instead_of_blackout_when_backup_alive() -> None:
    svc, cat, store = _svc()
    lead = PokemonInstance.from_species(cat.get("bulbasaur"))
    lead.hp = 1
    backup = PokemonInstance.from_species(cat.get("pikachu"))
    enemy = PokemonInstance.from_species(cat.get("charmander"))
    enemy.attack = 200
    state = GameState(
        user_id=11,
        phase="battle",
        x=0,
        y=0,
        party=[lead, backup],
        battle=BattleState(enemy=enemy),
        rng_state=_rng_seed_where_first_roll_fails(flee_chance()),
        steps=0,
        won=False,
    )
    store.save(state)
    result = svc._do_run(state)
    assert result.get("fled") is False
    assert result.get("blackout") is not True
    assert result["phase"] == "battle"
    assert any("Go! pikachu" in line for line in result["log"])


def test_do_catch_switches_instead_of_blackout_when_backup_alive() -> None:
    svc, cat, store = _svc()
    lead = PokemonInstance.from_species(cat.get("bulbasaur"))
    lead.hp = 1
    backup = PokemonInstance.from_species(cat.get("pikachu"))
    enemy = PokemonInstance.from_species(cat.get("pidgey"))
    enemy.hp = enemy.max_hp  # low catch chance
    enemy.attack = 200
    # First roll after _rng advance must fail catch ( >= catch_chance )
    from pokemon_world_mcp.battle import catch_chance

    threshold = catch_chance(enemy)
    seed = _rng_seed_where_first_roll_fails(threshold)
    state = GameState(
        user_id=12,
        phase="battle",
        x=0,
        y=0,
        party=[lead, backup],
        battle=BattleState(enemy=enemy),
        rng_state=seed,
        steps=0,
        won=False,
    )
    store.save(state)
    result = svc._do_catch(state)
    assert result.get("caught") is False
    assert result.get("blackout") is not True
    assert result["phase"] == "battle"
    assert any("Go! pikachu" in line for line in result["log"])
