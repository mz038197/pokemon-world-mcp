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


def test_catch_chance_increases_when_low_hp() -> None:
    cat = Catalog()
    full = PokemonInstance.from_species(cat.get("pidgey"))
    low = PokemonInstance.from_species(cat.get("pidgey"))
    low.hp = 1
    assert catch_chance(low) > catch_chance(full)


def test_find_move_normalizes() -> None:
    mon = PokemonInstance.from_species(Catalog().get("pikachu"))
    assert find_move(mon, "Thunder Shock") is not None or find_move(mon, "thunder-shock") is not None
