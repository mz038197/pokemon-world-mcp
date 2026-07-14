from __future__ import annotations

import json
import random
from typing import Any

from pokemon_world_mcp.battle import calc_damage, catch_chance, find_move, flee_chance
from pokemon_world_mcp.catalog import STARTERS, Catalog
from pokemon_world_mcp.experience import (
    MAX_LEVEL,
    battle_exp_yield,
    exp_to_next,
    total_exp_at,
)
from pokemon_world_mcp.growth import (
    apply_stats,
    known_move_names,
    moves_learned_at,
    replace_move,
    try_learn_move,
)
from pokemon_world_mcp.models import (
    BattleState,
    Direction,
    GameState,
    PendingLearn,
    PokemonInstance,
)
from pokemon_world_mcp.save_store import SaveStore
from pokemon_world_mcp.world import (
    ENCOUNTER_RATE,
    START_X,
    START_Y,
    look_window,
    step,
    terrain_at,
    wild_pool_for,
)

WIN_SPECIES_COUNT = 3
PARTY_LIMIT = 6
STARTER_LEVEL = 5


class GameError(Exception):
    """Action rejected; message is safe to return to the agent."""


class GameService:
    def __init__(self, store: SaveStore, catalog: Catalog) -> None:
        self.store = store
        self.catalog = catalog

    def _rng(self, state: GameState) -> random.Random:
        rng = random.Random(state.rng_state)
        state.rng_state = rng.randint(1, 2**31 - 1)
        return rng

    def _require(self, user_id: int) -> GameState:
        state = self.store.load(user_id)
        if state is None:
            raise GameError("no save found; call new_game first")
        if self._migrate_party_exp(state):
            self.store.save(state)
        return state

    def _migrate_party_exp(self, state: GameState) -> bool:
        """Coarse migration: legacy relative exp -> total_at(level)."""
        changed = False
        for mon in state.party:
            if mon.exp_scheme == "total":
                continue
            try:
                species = self.catalog.get(mon.name)
                mon.exp = total_exp_at(species.growth_rate, mon.level)
            except KeyError:
                mon.exp = total_exp_at("medium", mon.level)
            mon.exp_scheme = "total"
            changed = True
        if state.battle is not None:
            enemy = state.battle.enemy
            if enemy.exp_scheme != "total":
                try:
                    species = self.catalog.get(enemy.name)
                    enemy.exp = total_exp_at(species.growth_rate, enemy.level)
                except KeyError:
                    enemy.exp = total_exp_at("medium", enemy.level)
                enemy.exp_scheme = "total"
                changed = True
        return changed

    def _persist(self, state: GameState) -> None:
        distinct = {p.name for p in state.party}
        if len(distinct) >= WIN_SPECIES_COUNT:
            state.won = True
        self.store.save(state)

    def _active(self, state: GameState) -> PokemonInstance:
        for mon in state.party:
            if mon.hp > 0:
                return mon
        raise GameError("all party pokemon fainted; move to recover at start")

    def _active_index(self, state: GameState) -> int:
        for i, mon in enumerate(state.party):
            if mon.hp > 0:
                return i
        raise GameError("all party pokemon fainted; move to recover at start")

    def _heal_party(self, state: GameState) -> None:
        for mon in state.party:
            mon.hp = mon.max_hp

    def _blackout(self, state: GameState) -> str:
        state.phase = "exploring"
        state.battle = None
        state.x = START_X
        state.y = START_Y
        self._heal_party(state)
        return "You blacked out! Returned to start and party HP restored."

    def _handle_active_fainted(self, state: GameState, log: list[str]) -> PokemonInstance | None:
        if all(p.hp <= 0 for p in state.party):
            log.append(self._blackout(state))
            return None
        nxt = self._active(state)
        log.append(f"Go! {nxt.name}!")
        return nxt

    def _require_no_pending(self, state: GameState) -> None:
        if state.pending_learn:
            raise GameError(
                "pending move learn; use battle_action replace_move (forget_move_name) "
                "or skip_learn before continuing"
            )

    def _pending_payload(self, state: GameState) -> dict[str, Any] | None:
        if not state.pending_learn:
            return None
        item = state.pending_learn[0]
        mon = state.party[item.party_index]
        return {
            "party_index": item.party_index,
            "pokemon": mon.name,
            "level": mon.level,
            "current_moves": [
                {"name": m.name, "type": m.type, "power": m.power} for m in mon.moves
            ],
            "new_move": {
                "name": item.new_move.name,
                "type": item.new_move.type,
                "power": item.new_move.power,
            },
            "reason": item.reason,
            "actions": ["replace_move", "skip_learn"],
            "queue_length": len(state.pending_learn),
        }

    def _party_avg_level(self, state: GameState) -> int:
        if not state.party:
            return STARTER_LEVEL
        return max(1, round(sum(p.level for p in state.party) / len(state.party)))

    def _wild_level(self, state: GameState, rng: random.Random) -> int:
        avg = self._party_avg_level(state)
        return max(2, min(50, avg + rng.randint(-2, 2)))

    def _offer_or_learn(
        self,
        state: GameState,
        party_index: int,
        move,
        *,
        reason: str,
        log: list[str],
    ) -> bool:
        """Return True if paused for pending_learn."""
        mon = state.party[party_index]
        if move.name in known_move_names(mon):
            return False
        msg = try_learn_move(mon, move)
        if msg:
            log.append(msg)
            return False
        state.pending_learn.append(
            PendingLearn(party_index=party_index, new_move=move, reason=reason)  # type: ignore[arg-type]
        )
        log.append(
            f"{mon.name} wants to learn {move.name} but already knows 4 moves! "
            "Use replace_move or skip_learn."
        )
        return True

    def _try_evolve(self, state: GameState, party_index: int, log: list[str]) -> bool:
        """Evolve if eligible. Returns True if paused on pending learn."""
        mon = state.party[party_index]
        evo = self.catalog.evolves_to(mon.name)
        if evo is None:
            return False
        next_name, min_level = evo
        if mon.level < min_level:
            return False
        old_name = mon.name
        species = self.catalog.get(next_name)
        mon.name = species.name
        mon.types = list(species.types)
        apply_stats(mon, species, preserve_hp_ratio=True)
        log.append(f"{old_name} evolved into {mon.name}!")
        paused = False
        for lvl, move in sorted(species.learnset, key=lambda t: (t[0], t[1].name)):
            if lvl > mon.level:
                break
            if move.name in known_move_names(mon):
                continue
            # Skip if already queued
            if any(
                p.party_index == party_index and p.new_move.name == move.name
                for p in state.pending_learn
            ):
                continue
            msg = try_learn_move(mon, move)
            if msg:
                log.append(msg)
                continue
            state.pending_learn.append(
                PendingLearn(party_index=party_index, new_move=move, reason="evolution")
            )
            paused = True
        if paused:
            first = state.pending_learn[0].new_move.name
            log.append(
                f"{mon.name} wants to learn new moves but already knows 4! "
                f"Next: {first}. Use replace_move or skip_learn."
            )
        return paused

    def _process_growth(self, state: GameState, party_index: int, log: list[str]) -> None:
        """Consume total exp for level-ups / learns / evolutions until pending or done."""
        while True:
            if state.pending_learn:
                return
            mon = state.party[party_index]
            if mon.level >= MAX_LEVEL:
                return
            species = self.catalog.get(mon.name)
            need = total_exp_at(species.growth_rate, mon.level + 1)
            if mon.exp < need:
                return
            mon.level += 1
            apply_stats(mon, species, preserve_hp_ratio=True)
            log.append(f"{mon.name} grew to level {mon.level}!")
            for move in moves_learned_at(species, mon.level):
                if self._offer_or_learn(state, party_index, move, reason="level_up", log=log):
                    return
            if self._try_evolve(state, party_index, log):
                return

    def _grant_exp_from_enemy(
        self,
        state: GameState,
        enemy: PokemonInstance,
        log: list[str],
    ) -> int:
        """Grant battle yield exp to current active. Returns party_index."""
        try:
            base_exp = self.catalog.get(enemy.name).base_experience
        except KeyError:
            base_exp = 64
        amount = battle_exp_yield(base_exp, enemy.level)
        idx = self._active_index(state)
        mon = state.party[idx]
        if mon.level < MAX_LEVEL:
            mon.exp += amount
            log.append(f"{mon.name} gained {amount} exp!")
            self._process_growth(state, idx, log)
        return idx
    def new_game(self, user_id: int, starter: str) -> dict[str, Any]:
        key = starter.strip().lower()
        if key not in STARTERS:
            raise GameError(
                "starter must be one of: bulbasaur, charmander, squirtle"
            )
        mon = PokemonInstance.from_species(self.catalog.get(key), level=STARTER_LEVEL)
        state = GameState(
            user_id=user_id,
            phase="exploring",
            x=START_X,
            y=START_Y,
            party=[mon],
            battle=None,
            rng_state=user_id * 9973 + 42,
            steps=0,
            won=False,
            pending_learn=[],
        )
        self._persist(state)
        return {
            "ok": True,
            "message": (
                f"New game started. You received {key} (Lv{STARTER_LEVEL}). "
                f"Goal: catch {WIN_SPECIES_COUNT} different species."
            ),
            "status": self._status_payload(state),
        }

    def get_status(self, user_id: int) -> dict[str, Any]:
        state = self._require(user_id)
        return self._status_payload(state)

    def look(self, user_id: int) -> dict[str, Any]:
        state = self._require(user_id)
        self._require_no_pending(state)
        if state.phase != "exploring":
            raise GameError("cannot look while in battle; use battle_status")
        cells = look_window(state.x, state.y)
        return {
            "x": state.x,
            "y": state.y,
            "terrain_here": terrain_at(state.x, state.y),
            "visible": cells,
            "hint": "Explore grass for wild pokemon. Water is blocked.",
        }

    def party(self, user_id: int) -> dict[str, Any]:
        state = self._require(user_id)
        payload: dict[str, Any] = {
            "party": [
                {
                    "name": p.name,
                    "level": p.level,
                    "exp": p.exp,
                    "exp_to_next": exp_to_next(
                        self.catalog.get(p.name).growth_rate,
                        p.level,
                        p.exp,
                    ),
                    "growth_rate": self.catalog.get(p.name).growth_rate,
                    "types": p.types,
                    "hp": p.hp,
                    "max_hp": p.max_hp,
                    "moves": [m.name for m in p.moves],
                }
                for p in state.party
            ],
            "species_caught": sorted({p.name for p in state.party}),
            "won": state.won,
        }
        pending = self._pending_payload(state)
        if pending:
            payload["pending_learn"] = pending
        return payload

    def move(self, user_id: int, direction: str) -> dict[str, Any]:
        state = self._require(user_id)
        self._require_no_pending(state)
        if state.phase != "exploring":
            raise GameError("cannot move during battle; use battle_action")
        direction = direction.strip().upper()
        if direction not in {"N", "S", "E", "W"}:
            raise GameError("direction must be N, S, E, or W")
        nx, ny = step(state.x, state.y, direction)  # type: ignore[arg-type]
        try:
            terrain = terrain_at(nx, ny)
        except ValueError:
            raise GameError("cannot move that way: out of bounds") from None
        if terrain == "water":
            raise GameError("cannot walk on water")

        state.x, state.y = nx, ny
        state.steps += 1
        result: dict[str, Any] = {
            "ok": True,
            "moved": direction,
            "x": state.x,
            "y": state.y,
            "terrain": terrain,
        }

        if terrain == "grass":
            rng = self._rng(state)
            if rng.random() < ENCOUNTER_RATE:
                pool = wild_pool_for(state.x, state.y)
                enemy_name = rng.choice(pool)
                wlevel = self._wild_level(state, rng)
                enemy = PokemonInstance.from_species(
                    self.catalog.get(enemy_name),
                    level=wlevel,
                )
                state.phase = "battle"
                state.battle = BattleState(
                    enemy=enemy,
                    last_log=[f"A wild {enemy_name} (Lv{wlevel}) appeared!"],
                )
                result["encounter"] = enemy_name
                result["enemy_level"] = wlevel
                result["message"] = (
                    f"A wild {enemy_name} (Lv{wlevel}) appeared! "
                    "Use battle_status / battle_action."
                )
                self._persist(state)
                return result

        result["message"] = f"Moved {direction} to ({state.x},{state.y}) [{terrain}]."
        self._persist(state)
        return result

    def battle_status(self, user_id: int) -> dict[str, Any]:
        state = self._require(user_id)
        pending = self._pending_payload(state)
        if pending and (state.phase != "battle" or state.battle is None):
            return {
                "phase": state.phase,
                "pending_learn": pending,
                "actions": ["replace_move", "skip_learn"],
            }
        if state.phase != "battle" or state.battle is None:
            raise GameError("not in battle")
        you = self._active(state)
        enemy = state.battle.enemy
        actions = ["fight", "catch", "run"]
        payload: dict[str, Any] = {
            "phase": "battle",
            "you": {
                "name": you.name,
                "level": you.level,
                "hp": you.hp,
                "max_hp": you.max_hp,
                "types": you.types,
                "moves": [{"name": m.name, "type": m.type, "power": m.power} for m in you.moves],
            },
            "enemy": {
                "name": enemy.name,
                "level": enemy.level,
                "hp": enemy.hp,
                "max_hp": enemy.max_hp,
                "types": enemy.types,
            },
            "last_log": list(state.battle.last_log),
            "actions": actions,
        }
        if pending:
            payload["pending_learn"] = pending
            payload["actions"] = ["replace_move", "skip_learn"]
        return payload

    def battle_action(
        self,
        user_id: int,
        action: str,
        move_name: str | None = None,
        forget_move_name: str | None = None,
    ) -> dict[str, Any]:
        state = self._require(user_id)
        action = action.strip().lower()

        if action in {"replace_move", "skip_learn"}:
            return self._resolve_pending(state, action, forget_move_name)

        if state.pending_learn:
            raise GameError(
                "pending move learn; use battle_action replace_move (forget_move_name) "
                "or skip_learn before continuing"
            )

        if state.phase != "battle" or state.battle is None:
            raise GameError("not in battle")
        if action not in {"fight", "catch", "run"}:
            raise GameError(
                "action must be fight, catch, run, replace_move, or skip_learn"
            )

        if action == "run":
            return self._do_run(state)
        if action == "catch":
            return self._do_catch(state)
        if not move_name:
            raise GameError("fight requires move_name")
        return self._do_fight(state, move_name)

    def _resolve_pending(
        self,
        state: GameState,
        action: str,
        forget_move_name: str | None,
    ) -> dict[str, Any]:
        if not state.pending_learn:
            raise GameError("no pending move to learn")
        item = state.pending_learn[0]
        mon = state.party[item.party_index]
        log: list[str] = []
        if action == "skip_learn":
            log.append(f"{mon.name} did not learn {item.new_move.name}.")
            state.pending_learn.pop(0)
        else:
            if not forget_move_name:
                raise GameError("replace_move requires forget_move_name")
            try:
                log.append(replace_move(mon, forget_move_name, item.new_move))
            except ValueError as exc:
                raise GameError(str(exc)) from exc
            state.pending_learn.pop(0)

        # Continue growth for that mon (remaining exp / evo / more learns).
        self._process_growth(state, item.party_index, log)
        self._persist(state)
        result: dict[str, Any] = {
            "ok": True,
            "log": log,
            "phase": state.phase,
            "pending_learn": self._pending_payload(state),
        }
        return result

    def _do_run(self, state: GameState) -> dict[str, Any]:
        assert state.battle is not None
        rng = self._rng(state)
        log: list[str] = []
        if rng.random() < flee_chance():
            log.append("Got away safely!")
            state.phase = "exploring"
            state.battle = None
            self._persist(state)
            return {"ok": True, "fled": True, "log": log, "phase": "exploring"}
        log.append("Couldn't escape!")
        you = self._active(state)
        enemy = state.battle.enemy
        emove = rng.choice(enemy.moves)
        dmg = calc_damage(enemy, you, emove)
        you.hp = max(0, you.hp - dmg)
        log.append(f"Enemy {enemy.name} used {emove.name} for {dmg} damage.")
        if you.hp <= 0:
            nxt = self._handle_active_fainted(state, log)
            self._persist(state)
            if nxt is None:
                return {
                    "ok": True,
                    "fled": False,
                    "blackout": True,
                    "log": log,
                    "phase": "exploring",
                }
            return {"ok": True, "fled": False, "log": log, "phase": "battle"}
        state.battle.last_log = log
        self._persist(state)
        return {"ok": True, "fled": False, "log": log, "phase": "battle"}

    def _do_catch(self, state: GameState) -> dict[str, Any]:
        assert state.battle is not None
        enemy = state.battle.enemy
        rng = self._rng(state)
        chance = catch_chance(enemy)
        log = [f"Throwing a ball at {enemy.name} (catch chance ~{chance:.0%})..."]
        if rng.random() < chance:
            if len(state.party) >= PARTY_LIMIT:
                log.append("Party full; could not keep the pokemon.")
                state.phase = "exploring"
                state.battle = None
                self._persist(state)
                return {"ok": True, "caught": False, "reason": "party_full", "log": log}
            caught = PokemonInstance.from_species(
                self.catalog.get(enemy.name),
                level=enemy.level,
                hp=enemy.max_hp,
            )
            caught.hp = caught.max_hp
            state.party.append(caught)
            log.append(f"Caught {enemy.name} (Lv{enemy.level})!")
            state.phase = "exploring"
            state.battle = None
            self._grant_exp_from_enemy(state, enemy, log)
            self._persist(state)
            result: dict[str, Any] = {
                "ok": True,
                "caught": True,
                "name": enemy.name,
                "log": log,
                "won": state.won,
                "phase": "exploring",
            }
            pending = self._pending_payload(state)
            if pending:
                result["pending_learn"] = pending
            return result

        log.append(f"{enemy.name} broke free!")
        you = self._active(state)
        emove = rng.choice(enemy.moves)
        dmg = calc_damage(enemy, you, emove)
        you.hp = max(0, you.hp - dmg)
        log.append(f"Enemy {enemy.name} used {emove.name} for {dmg} damage.")
        if you.hp <= 0:
            nxt = self._handle_active_fainted(state, log)
            self._persist(state)
            if nxt is None:
                return {
                    "ok": True,
                    "caught": False,
                    "blackout": True,
                    "log": log,
                    "phase": "exploring",
                }
            return {"ok": True, "caught": False, "log": log, "phase": "battle"}
        state.battle.last_log = log
        self._persist(state)
        return {"ok": True, "caught": False, "log": log, "phase": "battle"}

    def _do_fight(self, state: GameState, move_name: str) -> dict[str, Any]:
        assert state.battle is not None
        you = self._active(state)
        enemy = state.battle.enemy
        move = find_move(you, move_name)
        if move is None:
            names = ", ".join(m.name for m in you.moves)
            raise GameError(f"unknown move; choose one of: {names}")

        rng = self._rng(state)
        emove = rng.choice(enemy.moves)
        log: list[str] = []

        order = [("you", you, enemy, move), ("enemy", enemy, you, emove)]
        if you.speed < enemy.speed or (you.speed == enemy.speed and rng.random() < 0.5):
            order.reverse()

        fainted_enemy = False
        for who, attacker, defender, used in order:
            if attacker.hp <= 0:
                continue
            dmg = calc_damage(attacker, defender, used)
            defender.hp = max(0, defender.hp - dmg)
            actor = you.name if who == "you" else f"Enemy {enemy.name}"
            log.append(f"{actor} used {used.name} for {dmg} damage.")
            if defender.hp <= 0:
                if who == "you":
                    fainted_enemy = True
                    log.append(f"Wild {enemy.name} fainted!")
                else:
                    log.append(f"{you.name} fainted!")
                break

        if fainted_enemy:
            defeated = enemy
            state.phase = "exploring"
            state.battle = None
            self._grant_exp_from_enemy(state, defeated, log)
            self._persist(state)
            result: dict[str, Any] = {
                "ok": True,
                "won_battle": True,
                "log": log,
                "phase": "exploring",
            }
            pending = self._pending_payload(state)
            if pending:
                result["pending_learn"] = pending
            return result

        if you.hp <= 0:
            nxt = self._handle_active_fainted(state, log)
            if nxt is None:
                self._persist(state)
                return {
                    "ok": True,
                    "won_battle": False,
                    "blackout": True,
                    "log": log,
                    "phase": "exploring",
                }
            you = nxt

        state.battle.last_log = log
        self._persist(state)
        return {
            "ok": True,
            "won_battle": False,
            "log": log,
            "you_hp": you.hp,
            "enemy_hp": enemy.hp,
            "phase": "battle",
        }

    def _status_payload(self, state: GameState) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "phase": state.phase,
            "x": state.x,
            "y": state.y,
            "steps": state.steps,
            "party_summary": [
                {
                    "name": p.name,
                    "level": p.level,
                    "hp": p.hp,
                    "max_hp": p.max_hp,
                }
                for p in state.party
            ],
            "species_count": len({p.name for p in state.party}),
            "goal": f"Collect {WIN_SPECIES_COUNT} different species",
            "won": state.won,
            "in_battle": state.phase == "battle",
        }
        pending = self._pending_payload(state)
        if pending:
            payload["pending_learn"] = pending
        return payload


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
