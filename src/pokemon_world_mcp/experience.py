from __future__ import annotations

"""Official Pokémon experience growth tables (PokéAPI growth-rate names).

Tables store cumulative total experience required to be at each level.
Level 1 is always 0. Values match Bulbapedia / PokéAPI level charts.
"""

MAX_LEVEL = 100

GROWTH_RATES = (
    "slow",
    "medium",
    "fast",
    "medium-slow",
    "slow-then-very-fast",
    "fast-then-very-slow",
)


def _total_for_level(growth_rate: str, level: int) -> int:
    n = level
    if n <= 1:
        return 0
    if growth_rate == "slow":
        return (5 * n**3) // 4
    if growth_rate == "medium":
        return n**3
    if growth_rate == "fast":
        return (4 * n**3) // 5
    if growth_rate == "medium-slow":
        return (6 * n**3) // 5 - 15 * n**2 + 100 * n - 140
    if growth_rate == "slow-then-very-fast":
        if n <= 50:
            return (n**3 * (100 - n)) // 50
        if n <= 68:
            return (n**3 * (150 - n)) // 100
        if n <= 98:
            return (n**3 * (1274 + (n % 3) ** 2 - 9 * (n % 3) - 20 * (n // 3))) // 1000
        return (n**3 * (160 - n)) // 100
    if growth_rate == "fast-then-very-slow":
        if n <= 15:
            return (n**3 * (24 + (n + 1) // 3)) // 50
        if n <= 35:
            return (n**3 * (14 + n)) // 50
        return (n**3 * (32 + n // 2)) // 50
    raise KeyError(f"unknown growth_rate: {growth_rate}")


def _build_table(growth_rate: str) -> list[int]:
    # Index by level; slot 0 unused.
    return [0] + [_total_for_level(growth_rate, lv) for lv in range(1, MAX_LEVEL + 1)]


# Built once at import; equivalent to PokéAPI /growth-rate/{id}/ levels[].
GROWTH_TABLES: dict[str, list[int]] = {name: _build_table(name) for name in GROWTH_RATES}


def normalize_growth_rate(name: str) -> str:
    key = name.strip().lower()
    # PokéAPI alias: "medium" == medium-fast
    if key in GROWTH_TABLES:
        return key
    raise KeyError(f"unknown growth_rate: {name}")


def total_exp_at(growth_rate: str, level: int) -> int:
    rate = normalize_growth_rate(growth_rate)
    level = max(1, min(MAX_LEVEL, int(level)))
    return GROWTH_TABLES[rate][level]


def exp_to_next(growth_rate: str, level: int, current_total: int) -> int:
    """How much more total exp is needed to reach level+1."""
    rate = normalize_growth_rate(growth_rate)
    level = max(1, min(MAX_LEVEL, int(level)))
    if level >= MAX_LEVEL:
        return 0
    need = GROWTH_TABLES[rate][level + 1]
    return max(0, need - int(current_total))


def level_for_total(growth_rate: str, total: int) -> int:
    rate = normalize_growth_rate(growth_rate)
    total = max(0, int(total))
    table = GROWTH_TABLES[rate]
    level = 1
    for lv in range(1, MAX_LEVEL + 1):
        if table[lv] <= total:
            level = lv
        else:
            break
    return level


def battle_exp_yield(base_experience: int, level: int) -> int:
    """Simplified Gen yield: floor(base_experience * level / 7)."""
    return max(1, int(base_experience) * max(1, int(level)) // 7)


def _validate_growth_table(key: str, values: list[int]) -> list[int]:
    """Require levels 1..100 populated: level 1 is 0, then strictly increasing."""
    if len(values) < MAX_LEVEL + 1:
        raise ValueError(f"growth table {key} too short")
    table = list(values[: MAX_LEVEL + 1])
    if table[1] != 0:
        raise ValueError(f"growth table {key} level 1 must be 0")
    for lv in range(2, MAX_LEVEL + 1):
        if table[lv] <= table[lv - 1]:
            raise ValueError(
                f"growth table {key} missing or non-increasing at level {lv} "
                f"(got {table[lv]} after {table[lv - 1]})"
            )
    return table


def apply_growth_tables_from_api(tables: dict[str, list[int]]) -> None:
    """Optionally overlay tables fetched from PokéAPI (must cover levels 1..100)."""
    # Validate every table before mutating globals so a bad overlay cannot partially apply.
    prepared: dict[str, list[int]] = {}
    for name, values in tables.items():
        key = normalize_growth_rate(name)
        prepared[key] = _validate_growth_table(key, values)
    for key, table in prepared.items():
        GROWTH_TABLES[key] = table
