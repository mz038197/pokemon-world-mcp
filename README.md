# Pokémon World MCP

Agent playground: explore a small map, battle and catch real-named Pokémon via MCP tools.

- **Public URL (planned):** `https://poke.vanscoding.com`
- **MCP endpoint:** `https://poke.vanscoding.com/mcp/` (local: `http://127.0.0.1:8080/mcp/`)
- **Auth:** same as vans-mcp — `Authorization: Bearer vcr_sk_...` (router Neon `api_keys` via `DATABASE_URL`) or local `MCP_DEV_BYPASS_KEY`
- **Saves / catalog:** game Neon `POKEMON_DATABASE_URL` (`pokemon_saves`, `pokemon_catalog_cache`); otherwise local SQLite under `$HOME/.pokemon-world-mcp/`
- **Catalog cache:** if missing or older than **24h** (`CATALOG_CACHE_TTL_HOURS`), fetch PokéAPI and write the game DB. Fresh cache skips the API. Long-running processes re-check via `ensure_fresh`. API failure uses in-memory fallback and does **not** write fallback to DB.

This project is **separate** from `vans-mcp-server` (Notion / Calendar / Gmail).

## Goal (V1)

Collect **3 different species** in your party.

**Starter (required):** `bulbasaur` | `charmander` | `squirtle` at **Lv5**.

## Progression

- **Exp (official curves):** each species has a PokéAPI `growth_rate` and cumulative total-exp table (six rates). `party.exp` is **total** experience.
- **Battle yield:** active Pokémon gains `floor(enemy.base_experience × enemy.level / 7)` on win **or** catch
- **Level-up:** when total exp reaches the next level threshold; stats scale with level; may learn moves from the species learnset
- **Moves:** max **4**. If a new move is offered while full, resolve with `battle_action(replace_move, forget_move_name=...)` or `skip_learn` before exploring/battling again
- **Evolution:** automatic at species thresholds (e.g. bulbasaur → ivysaur at 16)

## Tools

| Tool | When |
|------|------|
| `new_game(starter)` | Start / reset save (starter required) |
| `get_status` | Phase, position, party summary, pending learn |
| `look` | 3×3 nearby tiles (exploring) |
| `move` | `N` / `S` / `E` / `W` — grass may encounter |
| `party` | Party details (level, exp, moves) |
| `battle_status` | In battle (or pending learn) |
| `battle_action` | `fight` + `move_name`, `catch`, `run`, or `replace_move` / `skip_learn` |

Tip for student agents: **after each tool call, narrate the result to the human before the next action.**

## Local development

```powershell
cd C:\Users\mz038\Desktop\peas-agent\pokemon-world-mcp
uv sync --extra dev
$env:MCP_DEV_BYPASS_KEY = "vcr_sk_dev_local_only"
# Optional — router Neon (api_keys):
# $env:DATABASE_URL = "postgresql://..."
# Optional — game Neon (saves + catalog); without this, SQLite is used:
# $env:POKEMON_DATABASE_URL = "postgresql://..."
uv run pokemon-world-mcp
```

Without `POKEMON_DATABASE_URL`, player saves and the catalog cache both live in the same SQLite file (`%USERPROFILE%\.pokemon-world-mcp\pokemon_world.db`, or `SQLITE_PATH`). Progress survives restarts.

- Health: `http://127.0.0.1:8080/health` (no key)
- MCP: `http://127.0.0.1:8080/mcp/` with Bearer header

Production on Fly must set both `DATABASE_URL` (router) and `POKEMON_DATABASE_URL` (game) — see [docs/deploy-fly.md](docs/deploy-fly.md).

## Tests

```powershell
uv run pytest
```

## Cursor / student Agent config (example)

Point MCP at this server (not `mcp.vanscoding.com`), same course API key:

```json
{
  "mcpServers": {
    "pokemon-world": {
      "url": "https://poke.vanscoding.com/mcp/",
      "headers": {
        "Authorization": "Bearer vcr_sk_YOUR_KEY"
      }
    }
  }
}
```

Local:

```json
{
  "mcpServers": {
    "pokemon-world": {
      "url": "http://127.0.0.1:8080/mcp/",
      "headers": {
        "Authorization": "Bearer vcr_sk_dev_local_only"
      }
    }
  }
}
```

## Map notes

- 10×10 fixed map: `path`, `grass`, `water` (blocked)
- ~25% encounter chance on grass steps
- Wild **level** ≈ party average ±2 (clamped 2–50)
- Wild **species** by map quadrant (no starters in the wild):

| Zone | Coordinates | Pool |
|------|-------------|------|
| NW | x≤4, y≤4 | pidgey, rattata, jigglypuff |
| NE | x≥5, y≤4 | pikachu, meowth, jigglypuff |
| SW | x≤4, y≥5 | geodude, meowth, rattata |
| SE | x≥5, y≥5 | gastly, geodude, pidgey |

- Blackout: return to start, party healed
