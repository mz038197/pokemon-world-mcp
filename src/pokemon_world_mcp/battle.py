from __future__ import annotations

import math

from pokemon_world_mcp.catalog import type_multiplier
from pokemon_world_mcp.models import MoveInfo, PokemonInstance


def calc_damage(attacker: PokemonInstance, defender: PokemonInstance, move: MoveInfo) -> int:
    if move.power <= 0:
        return 0
    ratio = attacker.attack / max(1, defender.defense)
    mult = type_multiplier(move.type, defender.types)
    level_factor = max(1, attacker.level) / 50
    raw = ratio * move.power * mult * 0.4 * level_factor
    return max(1, int(math.floor(raw))) if mult > 0 else 0


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
