from __future__ import annotations

from dataclasses import replace

from pokemon_world_mcp.experience import MAX_LEVEL
from pokemon_world_mcp.models import MoveInfo, PokemonInstance, Species

MOVE_SLOT_LIMIT = 4

# Re-export for callers that imported MAX_LEVEL from growth.
__all__ = [
    "MAX_LEVEL",
    "MOVE_SLOT_LIMIT",
    "apply_stats",
    "calc_stats",
    "known_move_names",
    "moves_for_level",
    "moves_learned_at",
    "replace_move",
    "try_learn_move",
]


def calc_stats(species: Species, level: int) -> tuple[int, int, int, int]:
    """Return max_hp, attack, defense, speed for level."""
    level = max(1, min(MAX_LEVEL, level))
    max_hp = (species.hp * level) // 50 + level + 10
    attack = (species.attack * level) // 50 + 5
    defense = (species.defense * level) // 50 + 5
    speed = (species.speed * level) // 50 + 5
    return max_hp, attack, defense, speed


def apply_stats(mon: PokemonInstance, species: Species, *, preserve_hp_ratio: bool = True) -> None:
    old_max = mon.max_hp
    old_hp = mon.hp
    max_hp, attack, defense, speed = calc_stats(species, mon.level)
    mon.max_hp = max_hp
    mon.attack = attack
    mon.defense = defense
    mon.speed = speed
    if not preserve_hp_ratio or old_max <= 0:
        mon.hp = max_hp
        return
    if old_hp <= 0:
        mon.hp = 0
        return
    ratio = old_hp / old_max
    mon.hp = max(1, min(max_hp, int(max_hp * ratio)))


def moves_for_level(species: Species, level: int) -> list[MoveInfo]:
    """Auto-pick moves for wild/starter/legacy: all learnable <= level, keep last 4."""
    learned: list[MoveInfo] = []
    seen: set[str] = set()
    for lvl, move in sorted(species.learnset, key=lambda t: (t[0], t[1].name)):
        if lvl > level:
            break
        if move.name in seen:
            continue
        seen.add(move.name)
        learned.append(replace(move))
    if not learned:
        return [MoveInfo(name="tackle", type="normal", power=40)]
    if len(learned) > MOVE_SLOT_LIMIT:
        return learned[-MOVE_SLOT_LIMIT:]
    return learned


def known_move_names(mon: PokemonInstance) -> set[str]:
    return {m.name for m in mon.moves}


def moves_learned_at(species: Species, level: int) -> list[MoveInfo]:
    out: list[MoveInfo] = []
    seen: set[str] = set()
    for lvl, move in species.learnset:
        if lvl != level:
            continue
        if move.name in seen:
            continue
        seen.add(move.name)
        out.append(replace(move))
    return out


def try_learn_move(
    mon: PokemonInstance,
    move: MoveInfo,
) -> str | None:
    """Learn immediately if slot free. Returns log line, or None if needs pending."""
    if move.name in known_move_names(mon):
        return None
    if len(mon.moves) < MOVE_SLOT_LIMIT:
        mon.moves.append(replace(move))
        return f"{mon.name} learned {move.name}!"
    return None


def replace_move(mon: PokemonInstance, forget_name: str, new_move: MoveInfo) -> str:
    key = forget_name.strip().lower().replace(" ", "-")
    for i, m in enumerate(mon.moves):
        if m.name == key:
            old = m.name
            mon.moves[i] = replace(new_move)
            return f"{mon.name} forgot {old} and learned {new_move.name}!"
    names = ", ".join(m.name for m in mon.moves)
    raise ValueError(f"unknown move to forget; choose one of: {names}")
