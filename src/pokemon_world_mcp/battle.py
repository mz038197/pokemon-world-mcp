from __future__ import annotations

import math

from pokemon_world_mcp.catalog import type_multiplier
from pokemon_world_mcp.models import MoveInfo, PokemonInstance


def calc_damage(attacker: PokemonInstance, defender: PokemonInstance, move: MoveInfo) -> int:
    """Official Gen III+ core damage (no random / crit / weather / burn)."""
    if move.power <= 0 or move.damage_class == "status":
        return 0
    if move.damage_class == "special":
        a = attacker.special_attack
        d = max(1, defender.special_defense)
    else:
        a = attacker.attack
        d = max(1, defender.defense)

    level = max(1, attacker.level)
    # ((((2 * Level / 5 + 2) * Power * A / D) / 50) + 2)
    step1 = (2 * level) // 5 + 2
    step2 = (step1 * move.power * a) // d
    base = step2 // 50 + 2

    stab = 1.5 if move.type in attacker.types else 1.0
    mult = type_multiplier(move.type, defender.types)
    if mult <= 0:
        return 0
    return max(1, int(math.floor(base * stab * mult)))


def find_move(pokemon: PokemonInstance, move_name: str) -> MoveInfo | None:
    key = move_name.strip().lower().replace(" ", "-")
    for move in pokemon.moves:
        if move.name == key:
            return move
    return None


def catch_chance(enemy: PokemonInstance) -> float:
    """Higher when HP is lower. Range roughly 0.15 .. 0.85."""
    ratio = enemy.hp / max(1, enemy.max_hp)
    return max(0.15, min(0.85, 0.85 - 0.7 * ratio))


def flee_chance() -> float:
    return 0.5
