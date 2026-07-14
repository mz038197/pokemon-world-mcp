from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Phase = Literal["exploring", "battle"]
Terrain = Literal["path", "grass", "water"]
Direction = Literal["N", "S", "E", "W"]
LearnReason = Literal["level_up", "evolution"]


@dataclass
class MoveInfo:
    name: str
    type: str
    power: int


@dataclass
class Species:
    name: str
    types: list[str]
    hp: int
    attack: int
    defense: int
    speed: int
    learnset: list[tuple[int, MoveInfo]]
    base_experience: int = 64
    growth_rate: str = "medium-slow"


@dataclass
class PendingLearn:
    party_index: int
    new_move: MoveInfo
    reason: LearnReason

    def to_dict(self) -> dict[str, Any]:
        return {
            "party_index": self.party_index,
            "new_move": asdict(self.new_move),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingLearn:
        m = data["new_move"]
        return cls(
            party_index=int(data["party_index"]),
            new_move=MoveInfo(
                name=str(m["name"]),
                type=str(m["type"]),
                power=int(m["power"]),
            ),
            reason=data.get("reason") or "level_up",  # type: ignore[arg-type]
        )


@dataclass
class PokemonInstance:
    name: str
    types: list[str]
    max_hp: int
    hp: int
    attack: int
    defense: int
    speed: int
    moves: list[MoveInfo]
    level: int = 5
    exp: int = 0
    exp_scheme: str = "total"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "types": list(self.types),
            "max_hp": self.max_hp,
            "hp": self.hp,
            "attack": self.attack,
            "defense": self.defense,
            "speed": self.speed,
            "moves": [asdict(m) for m in self.moves],
            "level": self.level,
            "exp": self.exp,
            "exp_scheme": self.exp_scheme,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PokemonInstance:
        moves = [
            MoveInfo(
                name=str(m["name"]),
                type=str(m["type"]),
                power=int(m["power"]),
            )
            for m in data.get("moves") or []
        ]
        # Dual defaults are intentional:
        # - Runtime / from_species: exp_scheme="total" (cumulative total exp).
        # - Deserializing old saves: missing/blank exp_scheme => "legacy"
        #   (relative-to-next) so GameService._migrate_party_exp can rewrite.
        if "exp_scheme" not in data:
            scheme = "legacy"
        else:
            raw = data.get("exp_scheme")
            scheme = str(raw).strip() if raw is not None else ""
            if not scheme:
                scheme = "legacy"
        return cls(
            name=str(data["name"]),
            types=list(data.get("types") or []),
            max_hp=int(data["max_hp"]),
            hp=int(data["hp"]),
            attack=int(data["attack"]),
            defense=int(data["defense"]),
            speed=int(data["speed"]),
            moves=moves,
            level=int(data["level"]) if "level" in data else 5,
            exp=int(data["exp"]) if "exp" in data else 0,
            exp_scheme=scheme,
        )

    @classmethod
    def from_species(
        cls,
        species: Species,
        *,
        level: int = 5,
        hp: int | None = None,
        exp: int | None = None,
    ) -> PokemonInstance:
        from pokemon_world_mcp.experience import total_exp_at
        from pokemon_world_mcp.growth import apply_stats, moves_for_level

        # Under exp_scheme="total", the level floor (== zero progress to next)
        # is total_exp_at(...), not 0 (0 would desync level vs cumulative exp).
        total = total_exp_at(species.growth_rate, level) if exp is None else exp
        mon = cls(
            name=species.name,
            types=list(species.types),
            max_hp=1,
            hp=1,
            attack=1,
            defense=1,
            speed=1,
            moves=moves_for_level(species, level),
            level=level,
            exp=total,
            exp_scheme="total",
        )
        apply_stats(mon, species, preserve_hp_ratio=False)
        if hp is not None:
            mon.hp = max(0, min(mon.max_hp, hp))
        return mon


@dataclass
class BattleState:
    enemy: PokemonInstance
    last_log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"enemy": self.enemy.to_dict(), "last_log": list(self.last_log)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BattleState:
        return cls(
            enemy=PokemonInstance.from_dict(data["enemy"]),
            last_log=list(data.get("last_log") or []),
        )


@dataclass
class GameState:
    user_id: int
    phase: Phase
    x: int
    y: int
    party: list[PokemonInstance]
    battle: BattleState | None
    rng_state: int
    steps: int
    won: bool
    pending_learn: list[PendingLearn] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "phase": self.phase,
            "x": self.x,
            "y": self.y,
            "party": [p.to_dict() for p in self.party],
            "battle": self.battle.to_dict() if self.battle else None,
            "rng_state": self.rng_state,
            "steps": self.steps,
            "won": self.won,
            "pending_learn": [p.to_dict() for p in self.pending_learn],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GameState:
        battle_raw = data.get("battle")
        pending_raw = data.get("pending_learn") or []
        return cls(
            user_id=int(data["user_id"]),
            phase=data["phase"],  # type: ignore[arg-type]
            x=int(data["x"]),
            y=int(data["y"]),
            party=[PokemonInstance.from_dict(p) for p in data.get("party") or []],
            battle=BattleState.from_dict(battle_raw) if battle_raw else None,
            rng_state=int(data.get("rng_state") or 1),
            steps=int(data.get("steps") or 0),
            won=bool(data.get("won")),
            pending_learn=[PendingLearn.from_dict(p) for p in pending_raw],
        )
