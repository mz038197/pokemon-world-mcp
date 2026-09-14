from __future__ import annotations

import logging
import threading
from copy import deepcopy
from typing import Iterable

import httpx

from pokemon_world_mcp.models import MoveInfo, Species

logger = logging.getLogger(__name__)

POKEAPI_BASE = "https://pokeapi.co/api/v2"

# Base + evolution stages used by this game.
DEFAULT_SPECIES_IDS: list[int] = [
    1, 2, 3,  # bulbasaur line
    4, 5, 6,  # charmander line
    7, 8, 9,  # squirtle line
    16, 17, 18,  # pidgey line
    19, 20,  # rattata line
    25, 26,  # pikachu line
    39, 40,  # jigglypuff line
    52, 53,  # meowth line
    74, 75, 76,  # geodude line
    92, 93, 94,  # gastly line
]

STARTERS = ("bulbasaur", "charmander", "squirtle")

# from_name -> (to_name, min_level)
EVOLUTIONS: dict[str, tuple[str, int]] = {
    "bulbasaur": ("ivysaur", 16),
    "ivysaur": ("venusaur", 32),
    "charmander": ("charmeleon", 16),
    "charmeleon": ("charizard", 36),
    "squirtle": ("wartortle", 16),
    "wartortle": ("blastoise", 36),
    "pidgey": ("pidgeotto", 18),
    "pidgeotto": ("pidgeot", 36),
    "rattata": ("raticate", 20),
    "pikachu": ("raichu", 30),
    "jigglypuff": ("wigglytuff", 30),
    "meowth": ("persian", 28),
    "geodude": ("graveler", 25),
    "graveler": ("golem", 40),
    "gastly": ("haunter", 25),
    "haunter": ("gengar", 40),
}

# Offline fallback (simplified; online uses PokéAPI current-gen damage_relations).
FALLBACK_TYPE_CHART: dict[str, dict[str, float]] = {
    "normal": {"rock": 0.5, "ghost": 0.0, "steel": 0.5},
    "fire": {
        "fire": 0.5, "water": 0.5, "grass": 2.0, "ice": 2.0, "bug": 2.0,
        "rock": 0.5, "dragon": 0.5, "steel": 2.0,
    },
    "water": {
        "fire": 2.0, "water": 0.5, "grass": 0.5, "ground": 2.0, "rock": 2.0, "dragon": 0.5,
    },
    "electric": {
        "water": 2.0, "electric": 0.5, "grass": 0.5, "ground": 0.0, "flying": 2.0, "dragon": 0.5,
    },
    "grass": {
        "fire": 0.5, "water": 2.0, "grass": 0.5, "poison": 0.5, "ground": 2.0,
        "flying": 0.5, "bug": 0.5, "rock": 2.0, "dragon": 0.5, "steel": 0.5,
    },
    "ice": {
        "fire": 0.5, "water": 0.5, "grass": 2.0, "ice": 0.5, "ground": 2.0,
        "flying": 2.0, "dragon": 2.0, "steel": 0.5,
    },
    "fighting": {
        "normal": 2.0, "ice": 2.0, "poison": 0.5, "flying": 0.5, "psychic": 0.5,
        "bug": 0.5, "rock": 2.0, "ghost": 0.0, "dark": 2.0, "steel": 2.0, "fairy": 0.5,
    },
    "poison": {
        "grass": 2.0, "poison": 0.5, "ground": 0.5, "rock": 0.5, "ghost": 0.5,
        "steel": 0.0, "fairy": 2.0,
    },
    "ground": {
        "fire": 2.0, "electric": 2.0, "grass": 0.5, "poison": 2.0, "flying": 0.0,
        "bug": 0.5, "rock": 2.0, "steel": 2.0,
    },
    "flying": {
        "electric": 0.5, "grass": 2.0, "fighting": 2.0, "bug": 2.0, "rock": 0.5, "steel": 0.5,
    },
    "psychic": {
        "fighting": 2.0, "poison": 2.0, "psychic": 0.5, "dark": 0.0, "steel": 0.5,
    },
    "bug": {
        "fire": 0.5, "grass": 2.0, "fighting": 0.5, "poison": 0.5, "flying": 0.5,
        "psychic": 2.0, "ghost": 0.5, "dark": 2.0, "steel": 0.5, "fairy": 0.5,
    },
    "rock": {
        "fire": 2.0, "ice": 2.0, "fighting": 0.5, "ground": 0.5, "flying": 2.0,
        "bug": 2.0, "steel": 0.5,
    },
    "ghost": {"normal": 0.0, "psychic": 2.0, "ghost": 2.0, "dark": 0.5},
    "dragon": {"dragon": 2.0, "steel": 0.5, "fairy": 0.0},
    "dark": {
        "fighting": 0.5, "psychic": 2.0, "ghost": 2.0, "dark": 0.5, "fairy": 0.5,
    },
    "steel": {
        "fire": 0.5, "water": 0.5, "electric": 0.5, "ice": 2.0, "rock": 2.0,
        "steel": 0.5, "fairy": 2.0,
    },
    "fairy": {
        "fire": 0.5, "fighting": 2.0, "poison": 0.5, "dragon": 2.0, "dark": 2.0, "steel": 0.5,
    },
}

# Active chart (overlaid from PokéAPI / cache when available).
TYPE_CHART: dict[str, dict[str, float]] = deepcopy(FALLBACK_TYPE_CHART)


def apply_type_chart(chart: dict[str, dict[str, float]]) -> None:
    """Replace the process-wide type chart used by battles."""
    global TYPE_CHART
    TYPE_CHART = {k: dict(v) for k, v in chart.items()}


def reset_type_chart() -> None:
    apply_type_chart(FALLBACK_TYPE_CHART)


def type_multiplier(move_type: str, defender_types: Iterable[str]) -> float:
    mult = 1.0
    row = TYPE_CHART.get(move_type, {})
    for dtype in defender_types:
        mult *= row.get(dtype, 1.0)
    return mult


def _ls(*pairs: tuple[int, str, str, int, str]) -> list[tuple[int, MoveInfo]]:
    """Build learnset from (level, name, type, power, damage_class)."""
    return [
        (lvl, MoveInfo(name, typ, power, damage_class=dc))
        for lvl, name, typ, power, dc in pairs
    ]


def _fallback_species() -> dict[str, Species]:
    """Offline seed with learnsets so tests/work without network."""
    # (hp, attack, defense, spa, spd, speed) — modern PokéAPI base stats.
    specs: list[Species] = [
        Species("bulbasaur", ["grass", "poison"], 45, 49, 49, 65, 65, 45, _ls(
            (1, "tackle", "normal", 40, "physical"),
            (3, "vine-whip", "grass", 45, "physical"),
            (7, "razor-leaf", "grass", 55, "physical"),
            (13, "seed-bomb", "grass", 80, "physical"),
        )),
        Species("ivysaur", ["grass", "poison"], 60, 62, 63, 80, 80, 60, _ls(
            (1, "tackle", "normal", 40, "physical"),
            (1, "vine-whip", "grass", 45, "physical"),
            (16, "razor-leaf", "grass", 55, "physical"),
            (24, "seed-bomb", "grass", 80, "physical"),
            (32, "solar-beam", "grass", 120, "special"),
        )),
        Species("venusaur", ["grass", "poison"], 80, 82, 83, 100, 100, 80, _ls(
            (1, "vine-whip", "grass", 45, "physical"),
            (1, "razor-leaf", "grass", 55, "physical"),
            (32, "seed-bomb", "grass", 80, "physical"),
            (40, "solar-beam", "grass", 120, "special"),
        )),
        Species("charmander", ["fire"], 39, 52, 43, 60, 50, 65, _ls(
            (1, "scratch", "normal", 40, "physical"),
            (4, "ember", "fire", 40, "special"),
            (8, "dragon-breath", "dragon", 60, "special"),
            (12, "fire-fang", "fire", 65, "physical"),
        )),
        Species("charmeleon", ["fire"], 58, 64, 58, 80, 65, 80, _ls(
            (1, "scratch", "normal", 40, "physical"),
            (1, "ember", "fire", 40, "special"),
            (16, "dragon-breath", "dragon", 60, "special"),
            (24, "flamethrower", "fire", 90, "special"),
            (36, "fire-blast", "fire", 110, "special"),
        )),
        Species("charizard", ["fire", "flying"], 78, 84, 78, 109, 85, 100, _ls(
            (1, "ember", "fire", 40, "special"),
            (1, "dragon-breath", "dragon", 60, "special"),
            (36, "flamethrower", "fire", 90, "special"),
            (44, "fire-blast", "fire", 110, "special"),
        )),
        Species("squirtle", ["water"], 44, 48, 65, 50, 64, 43, _ls(
            (1, "tackle", "normal", 40, "physical"),
            (4, "water-gun", "water", 40, "special"),
            (8, "bite", "dark", 60, "physical"),
            (12, "bubble-beam", "water", 65, "special"),
        )),
        Species("wartortle", ["water"], 59, 63, 80, 65, 80, 58, _ls(
            (1, "tackle", "normal", 40, "physical"),
            (1, "water-gun", "water", 40, "special"),
            (16, "bite", "dark", 60, "physical"),
            (24, "water-pulse", "water", 60, "special"),
            (36, "hydro-pump", "water", 110, "special"),
        )),
        Species("blastoise", ["water"], 79, 83, 100, 85, 105, 78, _ls(
            (1, "water-gun", "water", 40, "special"),
            (1, "bite", "dark", 60, "physical"),
            (36, "water-pulse", "water", 60, "special"),
            (44, "hydro-pump", "water", 110, "special"),
        )),
        Species("pidgey", ["normal", "flying"], 40, 45, 40, 35, 35, 56, _ls(
            (1, "tackle", "normal", 40, "physical"),
            (5, "gust", "flying", 40, "special"),
            (9, "quick-attack", "normal", 40, "physical"),
            (15, "wing-attack", "flying", 60, "physical"),
        )),
        Species("pidgeotto", ["normal", "flying"], 63, 60, 55, 50, 50, 71, _ls(
            (1, "tackle", "normal", 40, "physical"),
            (1, "gust", "flying", 40, "special"),
            (18, "wing-attack", "flying", 60, "physical"),
            (28, "aerial-ace", "flying", 60, "physical"),
        )),
        Species("pidgeot", ["normal", "flying"], 83, 80, 75, 70, 70, 101, _ls(
            (1, "gust", "flying", 40, "special"),
            (1, "wing-attack", "flying", 60, "physical"),
            (36, "aerial-ace", "flying", 60, "physical"),
            (44, "air-slash", "flying", 75, "special"),
        )),
        Species("rattata", ["normal"], 30, 56, 35, 25, 35, 72, _ls(
            (1, "tackle", "normal", 40, "physical"),
            (4, "quick-attack", "normal", 40, "physical"),
            (10, "bite", "dark", 60, "physical"),
            (16, "hyper-fang", "normal", 80, "physical"),
        )),
        Species("raticate", ["normal"], 55, 81, 60, 50, 70, 97, _ls(
            (1, "quick-attack", "normal", 40, "physical"),
            (1, "bite", "dark", 60, "physical"),
            (20, "hyper-fang", "normal", 80, "physical"),
            (30, "super-fang", "normal", 80, "physical"),
        )),
        Species("pikachu", ["electric"], 35, 55, 40, 50, 50, 90, _ls(
            (1, "thunder-shock", "electric", 40, "special"),
            (4, "quick-attack", "normal", 40, "physical"),
            (10, "spark", "electric", 65, "physical"),
            (18, "thunderbolt", "electric", 90, "special"),
        )),
        Species("raichu", ["electric"], 60, 90, 55, 90, 80, 110, _ls(
            (1, "thunder-shock", "electric", 40, "special"),
            (1, "quick-attack", "normal", 40, "physical"),
            (30, "thunderbolt", "electric", 90, "special"),
            (40, "thunder", "electric", 110, "special"),
        )),
        Species("jigglypuff", ["normal", "fairy"], 115, 45, 20, 45, 25, 20, _ls(
            (1, "pound", "normal", 40, "physical"),
            (5, "double-slap", "normal", 15, "physical"),
            (12, "disarming-voice", "fairy", 40, "special"),
            (20, "body-slam", "normal", 85, "physical"),
        )),
        Species("wigglytuff", ["normal", "fairy"], 140, 70, 45, 85, 50, 45, _ls(
            (1, "pound", "normal", 40, "physical"),
            (1, "double-slap", "normal", 15, "physical"),
            (30, "body-slam", "normal", 85, "physical"),
            (40, "hyper-voice", "normal", 90, "special"),
        )),
        Species("meowth", ["normal"], 40, 45, 35, 40, 40, 90, _ls(
            (1, "scratch", "normal", 40, "physical"),
            (6, "bite", "dark", 60, "physical"),
            (14, "pay-day", "normal", 40, "physical"),
            (22, "slash", "normal", 70, "physical"),
        )),
        Species("persian", ["normal"], 65, 70, 60, 65, 65, 115, _ls(
            (1, "scratch", "normal", 40, "physical"),
            (1, "bite", "dark", 60, "physical"),
            (28, "slash", "normal", 70, "physical"),
            (36, "power-gem", "rock", 80, "special"),
        )),
        Species("geodude", ["rock", "ground"], 40, 80, 100, 30, 30, 20, _ls(
            (1, "tackle", "normal", 40, "physical"),
            (6, "rock-throw", "rock", 50, "physical"),
            (12, "bulldoze", "ground", 60, "physical"),
            (20, "rock-slide", "rock", 75, "physical"),
        )),
        Species("graveler", ["rock", "ground"], 55, 95, 115, 45, 45, 35, _ls(
            (1, "tackle", "normal", 40, "physical"),
            (1, "rock-throw", "rock", 50, "physical"),
            (25, "bulldoze", "ground", 60, "physical"),
            (34, "earthquake", "ground", 100, "physical"),
        )),
        Species("golem", ["rock", "ground"], 80, 120, 130, 55, 65, 45, _ls(
            (1, "rock-throw", "rock", 50, "physical"),
            (1, "bulldoze", "ground", 60, "physical"),
            (40, "earthquake", "ground", 100, "physical"),
            (48, "stone-edge", "rock", 100, "physical"),
        )),
        Species("gastly", ["ghost", "poison"], 30, 35, 30, 100, 35, 80, _ls(
            (1, "lick", "ghost", 30, "physical"),
            (5, "smog", "poison", 30, "special"),
            (12, "shadow-ball", "ghost", 80, "special"),
            (20, "dark-pulse", "dark", 80, "special"),
        )),
        Species("haunter", ["ghost", "poison"], 45, 50, 45, 115, 55, 95, _ls(
            (1, "lick", "ghost", 30, "physical"),
            (1, "smog", "poison", 30, "special"),
            (25, "shadow-ball", "ghost", 80, "special"),
            (35, "sludge-bomb", "poison", 90, "special"),
        )),
        Species("gengar", ["ghost", "poison"], 60, 65, 60, 130, 75, 110, _ls(
            (1, "lick", "ghost", 30, "physical"),
            (1, "shadow-ball", "ghost", 80, "special"),
            (40, "sludge-bomb", "poison", 90, "special"),
            (48, "shadow-claw", "ghost", 70, "physical"),
        )),
    ]
    # Official-ish base_experience + growth_rate (PokéAPI / species).
    meta: dict[str, tuple[int, str]] = {
        "bulbasaur": (64, "medium-slow"),
        "ivysaur": (142, "medium-slow"),
        "venusaur": (236, "medium-slow"),
        "charmander": (62, "medium-slow"),
        "charmeleon": (142, "medium-slow"),
        "charizard": (240, "medium-slow"),
        "squirtle": (63, "medium-slow"),
        "wartortle": (142, "medium-slow"),
        "blastoise": (239, "medium-slow"),
        "pidgey": (50, "medium-slow"),
        "pidgeotto": (122, "medium-slow"),
        "pidgeot": (216, "medium-slow"),
        "rattata": (51, "medium"),
        "raticate": (145, "medium"),
        "pikachu": (112, "medium"),
        "raichu": (243, "medium"),
        "jigglypuff": (95, "fast"),
        "wigglytuff": (218, "fast"),
        "meowth": (58, "medium"),
        "persian": (154, "medium"),
        "geodude": (60, "medium-slow"),
        "graveler": (137, "medium-slow"),
        "golem": (223, "medium-slow"),
        "gastly": (62, "medium-slow"),
        "haunter": (142, "medium-slow"),
        "gengar": (250, "medium-slow"),
    }
    out: dict[str, Species] = {}
    for s in specs:
        be, gr = meta.get(s.name, (64, "medium-slow"))
        out[s.name] = Species(
            name=s.name,
            types=s.types,
            hp=s.hp,
            attack=s.attack,
            defense=s.defense,
            special_attack=s.special_attack,
            special_defense=s.special_defense,
            speed=s.speed,
            learnset=s.learnset,
            base_experience=be,
            growth_rate=gr,
        )
    return out


# After PokéAPI failure, skip re-fetch for this long (avoid hammering) while
# still preserving the cache updated_at clock (no fake 24h TTL reset).
API_FAIL_BACKOFF_SEC = 300.0
_API_FAIL_LOCK = threading.Lock()


class Catalog:
    _last_api_fail_at: float | None = None

    def __init__(
        self,
        species: dict[str, Species] | None = None,
        *,
        timeout: float = 10.0,
        loaded_at: float | None = None,
    ) -> None:
        import time

        self._species = species or _fallback_species()
        self._timeout = timeout
        self._loaded_at = time.time() if loaded_at is None else loaded_at

    @classmethod
    def load(cls, *, timeout: float = 10.0) -> Catalog:
        species, loaded_at = cls._resolve_species(timeout=timeout)
        return cls(species, timeout=timeout, loaded_at=loaded_at)

    @staticmethod
    def _epoch_from_updated_at(updated_at) -> float:
        from datetime import timezone

        ts = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
        return ts.timestamp()

    @classmethod
    def _expired_loaded_at(cls, *, now: float) -> float:
        """Mark memory as already past TTL so ensure_fresh keeps trying (subject to backoff)."""
        from pokemon_world_mcp.catalog_cache import catalog_cache_ttl_hours

        return now - catalog_cache_ttl_hours() * 3600

    @classmethod
    def _snapshot_api_fail_at(cls) -> float | None:
        with _API_FAIL_LOCK:
            return cls._last_api_fail_at

    @classmethod
    def _set_api_fail_at(cls, value: float | None) -> None:
        with _API_FAIL_LOCK:
            cls._last_api_fail_at = value

    @classmethod
    def _resolve_species(
        cls,
        *,
        timeout: float,
    ) -> tuple[dict[str, Species], float]:
        import time

        from pokemon_world_mcp.catalog_cache import (
            is_fresh,
            load_catalog_cache_row,
            save_catalog_cache,
        )

        cached_row = load_catalog_cache_row()
        if cached_row and is_fresh(cached_row.updated_at):
            logger.info(
                "catalog loaded from fresh cache (%s species)",
                len(cached_row.species),
            )
            if cached_row.type_chart:
                apply_type_chart(cached_row.type_chart)
            else:
                reset_type_chart()
            # Align memory TTL with DB updated_at (do not reset the clock).
            return cached_row.species, cls._epoch_from_updated_at(cached_row.updated_at)

        now = time.time()
        last_fail = cls._snapshot_api_fail_at()
        if last_fail is not None and (now - last_fail) < API_FAIL_BACKOFF_SEC:
            if cached_row and cached_row.species:
                logger.warning("PokéAPI backoff; using stale catalog cache")
                if cached_row.type_chart:
                    apply_type_chart(cached_row.type_chart)
                return cached_row.species, cls._epoch_from_updated_at(cached_row.updated_at)
            base = _fallback_species()
            reset_type_chart()
            logger.warning("PokéAPI backoff; using in-memory fallback")
            return base, cls._expired_loaded_at(now=now)

        try:
            _refresh_growth_tables(timeout=timeout)
            fetched = _fetch_species(DEFAULT_SPECIES_IDS, timeout=timeout)
            if fetched:
                base = _fallback_species()
                base.update(fetched)
                save_catalog_cache(base, type_chart=TYPE_CHART)
                cls._set_api_fail_at(None)
                logger.info("catalog fetched from PokéAPI (%s species)", len(fetched))
                return base, time.time()
            raise RuntimeError("PokéAPI returned no species")
        except Exception:
            cls._set_api_fail_at(time.time())
            logger.exception("PokéAPI catalog fetch failed")

        if cached_row and cached_row.species:
            logger.warning("using stale catalog cache after PokéAPI failure")
            if cached_row.type_chart:
                apply_type_chart(cached_row.type_chart)
            # Keep original updated_at — do not grant a fresh 24h TTL.
            return cached_row.species, cls._epoch_from_updated_at(cached_row.updated_at)

        base = _fallback_species()
        reset_type_chart()
        logger.warning(
            "catalog using in-memory fallback only (%s species); not writing to DB",
            len(base),
        )
        return base, cls._expired_loaded_at(now=time.time())

    def ensure_fresh(self) -> None:
        """Reload from DB / PokéAPI when in-memory catalog exceeds TTL."""
        import time

        from pokemon_world_mcp.catalog_cache import catalog_cache_ttl_hours

        ttl_sec = catalog_cache_ttl_hours() * 3600
        if self._species and (time.time() - self._loaded_at) < ttl_sec:
            return
        self._species, self._loaded_at = type(self)._resolve_species(
            timeout=self._timeout,
        )

    def get(self, name: str) -> Species:
        self.ensure_fresh()
        key = name.strip().lower()
        if key not in self._species:
            raise KeyError(f"unknown species: {name}")
        return self._species[key]

    def names(self) -> list[str]:
        self.ensure_fresh()
        return sorted(self._species)

    def evolves_to(self, name: str) -> tuple[str, int] | None:
        self.ensure_fresh()
        return EVOLUTIONS.get(name.strip().lower())

    def wild_pool(self) -> list[str]:
        """Union of all first-stage non-starter wilds (for tests)."""
        from pokemon_world_mcp.world import ALL_WILD_SPECIES

        self.ensure_fresh()
        return [n for n in ALL_WILD_SPECIES if n in self._species]


def _stat_map(stats: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in stats:
        name = item["stat"]["name"]
        out[name] = int(item["base_stat"])
    return out


def _build_learnset(
    client: httpx.Client,
    move_entries: list[dict],
    *,
    limit: int = 12,
) -> list[tuple[int, MoveInfo]]:
    scored: list[tuple[int, str, str]] = []
    for entry in move_entries:
        move_name = entry["move"]["name"]
        move_url = entry["move"]["url"]
        level = 100
        found = False
        for detail in entry.get("version_group_details") or []:
            if detail.get("move_learn_method", {}).get("name") == "level-up":
                found = True
                level = min(level, int(detail.get("level_learned_at") or 100))
        if not found:
            continue
        scored.append((level, move_name, move_url))
    scored.sort(key=lambda t: (t[0], t[1]))

    learnset: list[tuple[int, MoveInfo]] = []
    seen: set[str] = set()
    for level, move_name, move_url in scored:
        if len(learnset) >= limit:
            break
        if move_name in seen:
            continue
        try:
            data = client.get(move_url).raise_for_status().json()
        except Exception:
            continue
        power = data.get("power")
        if not power:
            continue
        mtype = data.get("type", {}).get("name") or "normal"
        dc = data.get("damage_class", {}).get("name") or "physical"
        if dc == "status":
            continue
        seen.add(move_name)
        learnset.append(
            (
                level,
                MoveInfo(
                    name=move_name,
                    type=mtype,
                    power=int(power),
                    damage_class=str(dc),
                ),
            )
        )
    if not learnset:
        learnset.append(
            (1, MoveInfo(name="tackle", type="normal", power=40, damage_class="physical"))
        )
    return learnset


def _fetch_type_chart(
    client: httpx.Client,
    type_names: Iterable[str],
) -> dict[str, dict[str, float]]:
    chart: dict[str, dict[str, float]] = {}
    for name in sorted(set(type_names)):
        try:
            data = client.get(f"/type/{name}").raise_for_status().json()
        except Exception:
            logger.warning("failed to load type %s; skipping", name)
            continue
        rel = data.get("damage_relations") or {}
        row: dict[str, float] = {}
        for t in rel.get("double_damage_to") or []:
            row[str(t["name"])] = 2.0
        for t in rel.get("half_damage_to") or []:
            row[str(t["name"])] = 0.5
        for t in rel.get("no_damage_to") or []:
            row[str(t["name"])] = 0.0
        chart[name] = row
    return chart


def _fetch_species_and_types(
    ids: list[int], *, timeout: float
) -> tuple[dict[str, Species], dict[str, dict[str, float]]]:
    out: dict[str, Species] = {}
    type_names: set[str] = set()
    with httpx.Client(base_url=POKEAPI_BASE, timeout=timeout) as client:
        for sid in ids:
            data = client.get(f"/pokemon/{sid}").raise_for_status().json()
            name = str(data["name"])
            stats = _stat_map(data["stats"])
            types = [t["type"]["name"] for t in sorted(data["types"], key=lambda x: x["slot"])]
            type_names.update(types)
            learnset = _build_learnset(client, data.get("moves") or [])
            for _, move in learnset:
                type_names.add(move.type)
            base_exp = int(data.get("base_experience") or 64)
            growth_rate = "medium-slow"
            try:
                species_data = client.get(f"/pokemon-species/{sid}").raise_for_status().json()
                growth_rate = str(species_data.get("growth_rate", {}).get("name") or growth_rate)
            except Exception:
                logger.warning("failed to load growth_rate for %s; using %s", name, growth_rate)
            out[name] = Species(
                name=name,
                types=types,
                hp=stats.get("hp", 40),
                attack=stats.get("attack", 40),
                defense=stats.get("defense", 40),
                special_attack=stats.get("special-attack", 40),
                special_defense=stats.get("special-defense", 40),
                speed=stats.get("speed", 40),
                learnset=learnset,
                base_experience=base_exp,
                growth_rate=growth_rate,
            )
        type_chart = _fetch_type_chart(client, type_names)
    if not type_chart:
        type_chart = deepcopy(FALLBACK_TYPE_CHART)
    else:
        # Keep fallback rows for types we did not fetch.
        merged = deepcopy(FALLBACK_TYPE_CHART)
        merged.update(type_chart)
        type_chart = merged
    return out, type_chart


def _fetch_species(ids: list[int], *, timeout: float) -> dict[str, Species]:
    """Compat wrapper for tests that patch `_fetch_species`."""
    species, type_chart = _fetch_species_and_types(ids, timeout=timeout)
    apply_type_chart(type_chart)
    return species


def _refresh_growth_tables(*, timeout: float) -> None:
    """Overlay in-memory tables from PokéAPI when online."""
    from pokemon_world_mcp.experience import MAX_LEVEL, apply_growth_tables_from_api

    tables: dict[str, list[int]] = {}
    with httpx.Client(base_url=POKEAPI_BASE, timeout=timeout) as client:
        for gid in range(1, 7):
            data = client.get(f"/growth-rate/{gid}").raise_for_status().json()
            name = str(data["name"])
            vals = [0] * (MAX_LEVEL + 1)
            seen: set[int] = set()
            for row in data.get("levels") or []:
                lv = int(row["level"])
                if 1 <= lv <= MAX_LEVEL:
                    vals[lv] = int(row["experience"])
                    seen.add(lv)
            vals[1] = 0
            seen.add(1)
            missing = [lv for lv in range(1, MAX_LEVEL + 1) if lv not in seen]
            if missing:
                raise ValueError(
                    f"growth-rate {name} omitted levels from PokéAPI: "
                    f"{missing[0]}..{missing[-1]} ({len(missing)} missing)"
                )
            tables[name] = vals
    apply_growth_tables_from_api(tables)
    logger.info("growth-rate tables refreshed from PokéAPI (%s)", ", ".join(sorted(tables)))
