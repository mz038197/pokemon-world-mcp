from __future__ import annotations

from pokemon_world_mcp.catalog import Catalog, type_multiplier
from pokemon_world_mcp.models import MoveInfo, PokemonInstance
from pokemon_world_mcp.battle import calc_damage, catch_chance, find_move


def test_type_multiplier_super_effective() -> None:
    assert type_multiplier("water", ["fire"]) == 2.0
    assert type_multiplier("electric", ["ground"]) == 0.0


def test_calc_damage_positive() -> None:
    cat = Catalog()
    a = PokemonInstance.from_species(cat.get("squirtle"))
    b = PokemonInstance.from_species(cat.get("charmander"))
    move = find_move(a, "water-gun")
    assert move is not None
    dmg = calc_damage(a, b, move)
    assert dmg >= 1


def _mon(
    *,
    types: list[str],
    level: int,
    attack: int,
    defense: int,
    spa: int,
    spd: int,
) -> PokemonInstance:
    return PokemonInstance(
        name="test",
        types=types,
        max_hp=100,
        hp=100,
        attack=attack,
        defense=defense,
        special_attack=spa,
        special_defense=spd,
        speed=50,
        moves=[],
        level=level,
        exp=0,
    )


def test_calc_damage_official_formula_stab_and_type() -> None:
    # Level 50, Power 80, A=100, D=100
    # step1=(2*50)//5+2=22; step2=(22*80*100)//100=1760; base=1760//50+2=37
    # STAB 1.5 × type 2.0 → floor(37 * 1.5 * 2) = 111
    attacker = _mon(types=["water"], level=50, attack=100, defense=100, spa=100, spd=100)
    defender = _mon(types=["fire"], level=50, attack=100, defense=100, spa=100, spd=100)
    move = MoveInfo(name="surf", type="water", power=80, damage_class="special")
    assert calc_damage(attacker, defender, move) == 111


def test_calc_damage_uses_physical_stats() -> None:
    attacker = _mon(types=["normal"], level=50, attack=200, defense=100, spa=10, spd=100)
    defender = _mon(types=["normal"], level=50, attack=100, defense=100, spa=100, spd=10)
    physical = MoveInfo(name="tackle", type="normal", power=40, damage_class="physical")
    special = MoveInfo(name="hyper-voice", type="normal", power=40, damage_class="special")
    assert calc_damage(attacker, defender, physical) > calc_damage(attacker, defender, special)


def test_calc_damage_immune_is_zero() -> None:
    attacker = _mon(types=["electric"], level=50, attack=100, defense=100, spa=100, spd=100)
    defender = _mon(types=["ground"], level=50, attack=100, defense=100, spa=100, spd=100)
    move = MoveInfo(name="thunderbolt", type="electric", power=90, damage_class="special")
    assert calc_damage(attacker, defender, move) == 0


def test_calc_damage_status_is_zero() -> None:
    attacker = _mon(types=["normal"], level=50, attack=100, defense=100, spa=100, spd=100)
    defender = _mon(types=["normal"], level=50, attack=100, defense=100, spa=100, spd=100)
    move = MoveInfo(name="growl", type="normal", power=0, damage_class="status")
    assert calc_damage(attacker, defender, move) == 0


def test_catch_chance_increases_when_low_hp() -> None:
    cat = Catalog()
    full = PokemonInstance.from_species(cat.get("pidgey"))
    low = PokemonInstance.from_species(cat.get("pidgey"))
    low.hp = 1
    assert catch_chance(low) > catch_chance(full)


def test_find_move_normalizes() -> None:
    mon = PokemonInstance.from_species(Catalog().get("pikachu"))
    assert find_move(mon, "Thunder Shock") is not None or find_move(mon, "thunder-shock") is not None
