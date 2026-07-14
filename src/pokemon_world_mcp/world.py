from __future__ import annotations

from pokemon_world_mcp.models import Direction, Terrain

# 10x10 fixed map. Rows are y=0..9 top-to-bottom in array, x left-to-right.
# P=path, G=grass, W=water
_RAW = [
    "PPPPGGPPWW",
    "PGGGPGPPWW",
    "PGPPPPGPPP",
    "PGPGGGPGGP",
    "PPPGPPPGPP",
    "GGPGPGGPGP",
    "GPPPPPGPPP",
    "GPGGGPGGGP",
    "PPPGPPPPPP",
    "WWWWPPGGPP",
]

_CHAR: dict[str, Terrain] = {"P": "path", "G": "grass", "W": "water"}

MAP_WIDTH = 10
MAP_HEIGHT = 10
START_X = 0
START_Y = 0
ENCOUNTER_RATE = 0.25  # chance when stepping on grass

# Zone encounter pools (first-stage only; no Kanto starters).
ZONE_POOLS: dict[str, tuple[str, ...]] = {
    "NW": ("pidgey", "rattata", "jigglypuff"),
    "NE": ("pikachu", "meowth", "jigglypuff"),
    "SW": ("geodude", "meowth", "rattata"),
    "SE": ("gastly", "geodude", "pidgey"),
}

ALL_WILD_SPECIES: tuple[str, ...] = tuple(
    sorted({name for pool in ZONE_POOLS.values() for name in pool})
)


def terrain_at(x: int, y: int) -> Terrain:
    if not (0 <= x < MAP_WIDTH and 0 <= y < MAP_HEIGHT):
        raise ValueError("out of bounds")
    return _CHAR[_RAW[y][x]]


def in_bounds(x: int, y: int) -> bool:
    return 0 <= x < MAP_WIDTH and 0 <= y < MAP_HEIGHT


def step(x: int, y: int, direction: Direction) -> tuple[int, int]:
    dx, dy = {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0)}[direction]
    return x + dx, y + dy


def encounter_zone(x: int, y: int) -> str:
    if x <= 4 and y <= 4:
        return "NW"
    if x >= 5 and y <= 4:
        return "NE"
    if x <= 4 and y >= 5:
        return "SW"
    return "SE"


def wild_pool_for(x: int, y: int) -> list[str]:
    return list(ZONE_POOLS[encounter_zone(x, y)])


def look_window(x: int, y: int) -> list[dict]:
    """3x3 view centered on player; cells outside map omitted."""
    cells: list[dict] = []
    for yy in range(y - 1, y + 2):
        for xx in range(x - 1, x + 2):
            if not in_bounds(xx, yy):
                continue
            cells.append(
                {
                    "x": xx,
                    "y": yy,
                    "terrain": terrain_at(xx, yy),
                    "here": xx == x and yy == y,
                }
            )
    return cells
