from __future__ import annotations

import logging
import os
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from pokemon_world_mcp.auth import VcrApiKeyVerifier, api_key_http_middleware
from pokemon_world_mcp.catalog import Catalog
from pokemon_world_mcp.db import database_url_from_env, sqlite_path_from_env
from pokemon_world_mcp.game import GameError, GameService, dumps
from pokemon_world_mcp.save_store import PostgresSaveStore, SaveStore, SqliteSaveStore

def _log_level_from_env() -> int:
    name = (os.environ.get("LOG_LEVEL") or "INFO").strip().upper()
    return logging.getLevelNamesMapping().get(name, logging.INFO)


logging.basicConfig(
    level=_log_level_from_env(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("pokemon_world_mcp")

auth = VcrApiKeyVerifier.from_env()
catalog = Catalog.load()


def _build_store() -> SaveStore:
    url = database_url_from_env()
    if url:
        logger.info("using PostgresSaveStore")
        return PostgresSaveStore(url)
    path = sqlite_path_from_env()
    logger.info("DATABASE_URL unset — using SqliteSaveStore at %s", path)
    return SqliteSaveStore(path)


store = _build_store()
_save_backend = "postgres" if database_url_from_env() else "sqlite"
game = GameService(store, catalog)

# Do not pass auth= to FastMCP: its RequireAuthMiddleware advertises OAuth via
# WWW-Authenticate and VS Code enters Dynamic Client Registration.
mcp = FastMCP(
    "pokemon_world_mcp",
    instructions=(
        "Pokémon World MCP at poke.vanscoding.com. "
        "Explore a small map, encounter wild pokemon, battle, catch, level up, and evolve. "
        "Call new_game(starter) first — starter must be bulbasaur, charmander, or squirtle. "
        "While exploring: look, move. Wild encounters depend on map zone; levels scale with party. "
        "In battle: battle_status, battle_action(fight|catch|run). "
        "Pokemon gain exp from wins/catches, learn moves on level-up (max 4). "
        "If a 5th move is offered: battle_action(replace_move, forget_move_name=...) or skip_learn "
        "before moving or battling again. "
        "Goal: collect 3 different species. "
        "After each action, narrate the result to the human user before continuing."
    ),
)


def _claims() -> dict[str, Any]:
    token = get_access_token()
    if token is None:
        return {}
    return dict(token.claims or {})


def _require_user_id() -> int:
    raw = _claims().get("user_id")
    if raw is None:
        raise RuntimeError("authenticated user_id missing")
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("authenticated user_id missing") from exc


def _tool_result(fn, *args, **kwargs) -> str:
    try:
        return dumps(fn(*args, **kwargs))
    except GameError as exc:
        return dumps({"ok": False, "error": str(exc)})


@mcp.tool(
    name="new_game",
    annotations={
        "title": "Start or reset Pokémon World save",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def new_game(starter: str) -> str:
    """Create or reset the authenticated user's game.

    Args:
        starter: Required. One of: bulbasaur, charmander, squirtle.
    """
    return _tool_result(game.new_game, _require_user_id(), starter)


@mcp.tool(
    name="get_status",
    annotations={
        "title": "Get adventure status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def get_status() -> str:
    """Return phase, position, party summary, and win flag."""
    return _tool_result(game.get_status, _require_user_id())


@mcp.tool(
    name="look",
    annotations={
        "title": "Look at nearby tiles",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def look() -> str:
    """Show a 3x3 view around the player (exploring only)."""
    return _tool_result(game.look, _require_user_id())


@mcp.tool(
    name="move",
    annotations={
        "title": "Move on the map",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def move(direction: str) -> str:
    """Move one step. direction: N, S, E, or W. May trigger a wild encounter.

    Args:
        direction: Cardinal direction N/S/E/W.
    """
    return _tool_result(game.move, _require_user_id(), direction)


@mcp.tool(
    name="party",
    annotations={
        "title": "List party pokemon",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def party() -> str:
    """List party HP, types, and moves."""
    return _tool_result(game.party, _require_user_id())


@mcp.tool(
    name="battle_status",
    annotations={
        "title": "Get current battle state",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def battle_status() -> str:
    """Show your active pokemon, enemy HP/types, and available moves."""
    return _tool_result(game.battle_status, _require_user_id())


@mcp.tool(
    name="battle_action",
    annotations={
        "title": "Take a battle action",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def battle_action(
    action: str,
    move_name: str | None = None,
    forget_move_name: str | None = None,
) -> str:
    """Battle or resolve a pending move learn.

    Args:
        action: fight | catch | run | replace_move | skip_learn
        move_name: Required when action is fight (e.g. vine-whip).
        forget_move_name: Required when action is replace_move.
    """
    return _tool_result(
        game.battle_action,
        _require_user_id(),
        action,
        move_name,
        forget_move_name,
    )


async def health(_request: Request) -> JSONResponse:
    auth_mode = "neon" if database_url_from_env() else "bypass_or_unconfigured"
    return JSONResponse(
        {
            "ok": True,
            "service": "pokemon-world-mcp",
            "public_url": os.environ.get("PUBLIC_URL", "https://poke.vanscoding.com"),
            "auth": auth_mode,
            "saves": _save_backend,
        }
    )


mcp_app = mcp.http_app(path="/")
for mw in reversed(api_key_http_middleware(auth)):
    mcp_app.add_middleware(mw.cls, *mw.args, **mw.kwargs)

app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Mount("/mcp", app=mcp_app),
    ],
    lifespan=mcp_app.lifespan,
)
