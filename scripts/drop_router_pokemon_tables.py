"""One-shot CLI: DROP pokemon_* tables from router Postgres (DATABASE_URL).

Requires POKEMON_DATABASE_URL to be set and different from DATABASE_URL.
Does not touch api_keys or the game database.

Usage:

  uv run python scripts/drop_router_pokemon_tables.py
  uv run python scripts/drop_router_pokemon_tables.py --yes
"""

from __future__ import annotations

import argparse
import sys

from pokemon_world_mcp.drop_router_tables import (
    drop_router_pokemon_tables,
    validate_drop_env,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drop pokemon_saves / pokemon_catalog_cache from router Postgres only."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation",
    )
    args = parser.parse_args(argv)

    try:
        validate_drop_env()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("Will DROP on router DATABASE_URL only:")
    print("  - pokemon_catalog_cache")
    print("  - pokemon_saves")
    print("Will NOT touch api_keys or POKEMON_DATABASE_URL.")
    if not args.yes:
        answer = input("Type DROP to confirm: ").strip()
        if answer != "DROP":
            print("Aborted.")
            return 1

    drop_router_pokemon_tables()
    print("Done: router pokemon_* tables dropped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
