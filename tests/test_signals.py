"""Production ERROR logs under pokemon_world_mcp are posted once to vans-signals."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from pokemon_world_mcp.auth import VcrApiKeyVerifier
from pokemon_world_mcp.catalog import Catalog, _fallback_species, _fetch_type_chart
from pokemon_world_mcp.game import GameError, GameService
from pokemon_world_mcp.save_store import MemorySaveStore

POKEMON_SOURCE = "pokemon-world-mcp"
EXAMPLE_PATH = Path("tests/fixtures/signal_body.example.json")

os.environ["SQLITE_PATH"] = "/tmp/pokemon-world-mcp-signals-import.db"
os.environ.pop("DATABASE_URL", None)
os.environ.pop("POKEMON_DATABASE_URL", None)
os.environ["VANS_SIGNALS_URL"] = "http://127.0.0.1:9"
os.environ["VANS_SIGNALS_TOKEN"] = "pokemon-token"
os.environ["VANS_SIGNALS_SOURCE"] = POKEMON_SOURCE

_forwarder_on_catalog_load: list[bool] = []


def _tracking_catalog_load(*, timeout: float = 10.0):
    handlers = logging.getLogger("pokemon_world_mcp").handlers
    _forwarder_on_catalog_load.append(
        any(type(h).__name__ == "SignalForwarder" for h in handlers)
    )
    return Catalog()


_catalog_load_patch = patch.object(Catalog, "load", side_effect=_tracking_catalog_load)
_catalog_load_patch.start()
try:
    from pokemon_world_mcp import app as pokemon_app  # noqa: E402
finally:
    _catalog_load_patch.stop()
    for handler in list(logging.getLogger("pokemon_world_mcp").handlers):
        if type(handler).__name__ == "SignalForwarder":
            handler.close()
    for key in ("VANS_SIGNALS_URL", "VANS_SIGNALS_TOKEN", "VANS_SIGNALS_SOURCE"):
        os.environ.pop(key, None)


class _RecordingSignals(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), _SignalsHandler)
        self.posts: list[dict] = []
        self.hold_response = threading.Event()
        self.release_response = threading.Event()
        self.status = 200


class _SignalsHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        server: _RecordingSignals = self.server  # type: ignore[assignment]
        server.posts.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": json.loads(raw.decode("utf-8")),
            }
        )
        if server.hold_response.is_set():
            server.release_response.wait(timeout=5)
        self.send_response(server.status)
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        return


class _SignalsReceiver:
    def __init__(self) -> None:
        self._server = _RecordingSignals()
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def posts(self) -> list[dict]:
        return self._server.posts

    def fail_with(self, status: int) -> None:
        self._server.status = status

    def hold_responses(self) -> None:
        self._server.hold_response.set()

    def release_responses(self) -> None:
        self._server.release_response.set()

    def close(self) -> None:
        self.release_responses()
        self._server.shutdown()
        self._thread.join(timeout=2)
        self._server.server_close()


def _failure_logs(caplog):
    return [record for record in caplog.records if record.name == "vans_signals_forwarder"]


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def _messages(receiver: _SignalsReceiver) -> list[str]:
    return [post["body"]["message"] for post in receiver.posts]


def _fetch_failure_posts(receiver: _SignalsReceiver) -> list[dict]:
    return [
        post
        for post in receiver.posts
        if "PokéAPI catalog fetch failed" in post["body"]["message"]
    ]


@pytest.fixture(autouse=True)
def _reset_catalog_backoff() -> None:
    Catalog._set_api_fail_at(None)
    yield
    Catalog._set_api_fail_at(None)


@pytest.fixture
def signals(monkeypatch):
    from pokemon_world_mcp.signal_forwarder import start_signal_forwarding

    receiver = _SignalsReceiver()
    monkeypatch.setenv("VANS_SIGNALS_URL", receiver.url)
    monkeypatch.setenv("VANS_SIGNALS_TOKEN", "pokemon-token")
    monkeypatch.setenv("VANS_SIGNALS_SOURCE", POKEMON_SOURCE)
    forwarder = start_signal_forwarding()
    assert forwarder is not None
    try:
        yield receiver
    finally:
        forwarder.close()
        receiver.close()


def _empty_cache_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POKEMON_DATABASE_URL", raising=False)
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "catalog.db"))
    Catalog._set_api_fail_at(None)


def test_forwarder_is_attached_before_catalog_load_at_import() -> None:
    assert _forwarder_on_catalog_load == [True]


def test_posted_json_matches_the_example_keys(signals) -> None:
    example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    receiver = signals
    logging.getLogger("pokemon_world_mcp.catalog").error(
        "upstream openrouter did not update"
    )
    _wait_until(lambda: len(receiver.posts) >= 1)

    assert len(receiver.posts) == 1
    post = receiver.posts[0]
    assert post["path"] == "/signals"
    assert post["authorization"] == "Bearer pokemon-token"
    body = post["body"]
    assert body.keys() == example.keys()
    assert len(body) == 5
    assert body["level"] == "ERROR"
    assert body["source"] == POKEMON_SOURCE
    logged_at = datetime.fromisoformat(body["log_time"])
    assert logged_at.tzinfo is not None and logged_at.utcoffset() is not None
    assert body["logger_name"] == "pokemon_world_mcp.catalog"
    assert body["message"] == "upstream openrouter did not update"


def test_missing_destination_token_or_source_sends_nothing(monkeypatch) -> None:
    from pokemon_world_mcp.signal_forwarder import start_signal_forwarding

    receiver = _SignalsReceiver()
    try:
        monkeypatch.setenv("VANS_SIGNALS_URL", receiver.url)
        monkeypatch.setenv("VANS_SIGNALS_TOKEN", "pokemon-token")
        monkeypatch.delenv("VANS_SIGNALS_SOURCE", raising=False)
        assert start_signal_forwarding() is None

        monkeypatch.setenv("VANS_SIGNALS_SOURCE", "   ")
        assert start_signal_forwarding() is None

        monkeypatch.setenv("VANS_SIGNALS_SOURCE", POKEMON_SOURCE)
        monkeypatch.setenv("VANS_SIGNALS_TOKEN", "")
        assert start_signal_forwarding() is None

        monkeypatch.setenv("VANS_SIGNALS_TOKEN", "pokemon-token")
        monkeypatch.setenv("VANS_SIGNALS_URL", "  ")
        assert start_signal_forwarding() is None

        logging.getLogger("pokemon_world_mcp.catalog").error("should stay local")
        time.sleep(0.15)
        assert receiver.posts == []
    finally:
        receiver.close()


def test_pokeapi_fetch_failure_posts_one_signal_fallback_warnings_do_not(
    tmp_path: Path, monkeypatch, signals
) -> None:
    receiver = signals
    _empty_cache_env(tmp_path, monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr("pokemon_world_mcp.catalog._fetch_species", boom)
    monkeypatch.setattr("pokemon_world_mcp.catalog._refresh_growth_tables", boom)

    Catalog.load(timeout=0.1)
    _wait_until(lambda: len(_fetch_failure_posts(receiver)) >= 1)
    time.sleep(0.15)

    assert len(_fetch_failure_posts(receiver)) == 1
    assert len(receiver.posts) == 1
    message = receiver.posts[0]["body"]["message"]
    assert message.startswith("PokéAPI catalog fetch failed\n")
    assert "Traceback (most recent call last):" in message
    assert "RuntimeError: network down" in message
    assert receiver.posts[0]["body"]["source"] == POKEMON_SOURCE
    assert receiver.posts[0]["body"]["level"] == "ERROR"


def test_stale_cache_fallback_warning_does_not_add_a_signal(
    tmp_path: Path, monkeypatch, signals
) -> None:
    from pokemon_world_mcp.catalog_cache import save_catalog_cache

    receiver = signals
    _empty_cache_env(tmp_path, monkeypatch)
    monkeypatch.setenv("CATALOG_CACHE_TTL_HOURS", "24")

    species = _fallback_species()
    save_catalog_cache(species)
    import sqlite3

    old = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with sqlite3.connect(tmp_path / "catalog.db") as conn:
        conn.execute(
            "UPDATE pokemon_catalog_cache SET updated_at = ? WHERE id = 1",
            (old,),
        )
        conn.commit()

    def boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr("pokemon_world_mcp.catalog._fetch_species", boom)
    monkeypatch.setattr("pokemon_world_mcp.catalog._refresh_growth_tables", boom)

    cat = Catalog.load(timeout=0.1)
    _wait_until(lambda: len(_fetch_failure_posts(receiver)) >= 1)
    time.sleep(0.15)
    assert len(receiver.posts) == 1
    assert cat.get("bulbasaur").name == "bulbasaur"

    cat.ensure_fresh()
    time.sleep(0.15)
    assert len(_fetch_failure_posts(receiver)) == 1
    assert len(receiver.posts) == 1


def test_backoff_after_failed_fetch_does_not_post_another_fetch_signal(
    tmp_path: Path, monkeypatch, signals
) -> None:
    receiver = signals
    _empty_cache_env(tmp_path, monkeypatch)
    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise RuntimeError("network down")

    monkeypatch.setattr("pokemon_world_mcp.catalog._fetch_species", boom)
    monkeypatch.setattr("pokemon_world_mcp.catalog._refresh_growth_tables", boom)

    Catalog.load(timeout=0.1)
    _wait_until(lambda: len(_fetch_failure_posts(receiver)) >= 1)
    assert calls["n"] == 1

    Catalog.load(timeout=0.1)
    time.sleep(0.2)
    assert calls["n"] == 1
    assert len(_fetch_failure_posts(receiver)) == 1


def test_partial_type_or_growth_skip_does_not_post_a_signal(signals) -> None:
    receiver = signals

    class BoomClient:
        def get(self, url: str):
            raise RuntimeError("type endpoint down")

    chart = _fetch_type_chart(BoomClient(), ["fire"])
    assert chart == {}

    logging.getLogger("pokemon_world_mcp.catalog").warning(
        "failed to load growth_rate for bulbasaur; using medium-slow"
    )
    time.sleep(0.15)
    assert receiver.posts == []


def test_db_down_in_memory_fallback_posts_one_or_two_signals_per_tool_call(
    monkeypatch, signals
) -> None:
    receiver = signals

    def boom_db(*_a, **_k):
        raise RuntimeError("db down")

    def boom_fetch(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr("pokemon_world_mcp.catalog_cache._load_sqlite", boom_db)
    monkeypatch.setattr("pokemon_world_mcp.catalog_cache._load_postgres", boom_db)
    monkeypatch.setattr("pokemon_world_mcp.catalog._fetch_species", boom_fetch)
    monkeypatch.setattr("pokemon_world_mcp.catalog._refresh_growth_tables", boom_fetch)

    cat = Catalog(loaded_at=0)
    svc = GameService(MemorySaveStore(), cat)
    svc.new_game(7, "bulbasaur")
    _wait_until(lambda: 1 <= len(receiver.posts) <= 2)
    first = len(receiver.posts)
    assert 1 <= first <= 2

    before = len(receiver.posts)
    svc.new_game(8, "charmander")
    _wait_until(lambda: len(receiver.posts) - before >= 1)
    added = len(receiver.posts) - before
    assert 1 <= added <= 2


def test_catalog_cache_write_failure_posts_a_signal(
    tmp_path: Path, monkeypatch, signals
) -> None:
    receiver = signals
    _empty_cache_env(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "pokemon_world_mcp.catalog._fetch_species",
        lambda *_a, **_k: _fallback_species(),
    )
    monkeypatch.setattr(
        "pokemon_world_mcp.catalog._refresh_growth_tables",
        lambda **_k: None,
    )

    def boom_save(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr("pokemon_world_mcp.catalog_cache._save_sqlite", boom_save)
    monkeypatch.setattr("pokemon_world_mcp.catalog_cache._save_postgres", boom_save)

    Catalog.load(timeout=0.1)
    _wait_until(
        lambda: any(
            "failed to save catalog cache" in post["body"]["message"]
            for post in receiver.posts
        )
    )
    assert receiver.posts[0]["body"]["source"] == POKEMON_SOURCE


def test_unexpected_tool_exception_posts_a_signal(monkeypatch, signals) -> None:
    receiver = signals
    monkeypatch.setattr(pokemon_app, "_require_user_id", lambda: 1)

    def boom(_user_id: int):
        raise RuntimeError("catalog exploded")

    monkeypatch.setattr(pokemon_app.game, "get_status", boom)
    with pytest.raises(RuntimeError, match="catalog exploded"):
        pokemon_app.get_status()
    _wait_until(lambda: len(receiver.posts) >= 1)
    assert len(receiver.posts) == 1
    message = receiver.posts[0]["body"]["message"]
    assert "catalog exploded" in message
    assert receiver.posts[0]["body"]["source"] == POKEMON_SOURCE
    assert receiver.posts[0]["body"]["logger_name"].startswith("pokemon_world_mcp")


def test_game_error_does_not_post_a_signal(monkeypatch, signals) -> None:
    receiver = signals
    monkeypatch.setattr(pokemon_app, "_require_user_id", lambda: 1)
    raw = pokemon_app.get_status()
    data = json.loads(raw)
    assert data["ok"] is False
    assert "no save found" in data["error"].lower()
    time.sleep(0.15)
    assert receiver.posts == []


def test_missing_user_id_in_a_tool_posts_a_signal(monkeypatch, signals) -> None:
    receiver = signals
    monkeypatch.setattr(pokemon_app, "get_access_token", lambda: None)
    with pytest.raises(RuntimeError, match="authenticated user_id missing"):
        pokemon_app.get_status()
    _wait_until(lambda: len(receiver.posts) >= 1)
    assert len(receiver.posts) == 1
    assert receiver.posts[0]["body"]["source"] == POKEMON_SOURCE


@pytest.mark.asyncio
async def test_api_key_db_error_posts_a_signal(monkeypatch, signals) -> None:
    receiver = signals
    verifier = VcrApiKeyVerifier(
        database_url="postgresql://unused",
        bypass_key=None,
    )

    def boom(_token: str):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(verifier.store, "verify", boom)
    token = await verifier.verify_token("vcr_sk_test")
    assert token is None
    _wait_until(
        lambda: any(
            "API key verification failed" in post["body"]["message"]
            for post in receiver.posts
        )
    )
    assert receiver.posts[0]["body"]["source"] == POKEMON_SOURCE


@pytest.mark.asyncio
async def test_invalid_api_key_does_not_post_a_signal(monkeypatch, signals) -> None:
    receiver = signals
    verifier = VcrApiKeyVerifier(
        database_url="postgresql://unused",
        bypass_key=None,
    )
    monkeypatch.setattr(verifier.store, "verify", lambda _token: None)
    token = await verifier.verify_token("vcr_sk_bad")
    assert token is None
    time.sleep(0.15)
    assert receiver.posts == []


def test_a_failed_post_is_not_retried_and_is_not_another_signal(caplog, monkeypatch) -> None:
    from pokemon_world_mcp.signal_forwarder import start_signal_forwarding

    receiver = _SignalsReceiver()
    receiver.fail_with(500)
    monkeypatch.setenv("VANS_SIGNALS_URL", receiver.url)
    monkeypatch.setenv("VANS_SIGNALS_TOKEN", "pokemon-token")
    monkeypatch.setenv("VANS_SIGNALS_SOURCE", POKEMON_SOURCE)
    forwarder = start_signal_forwarding()
    assert forwarder is not None
    try:
        with caplog.at_level(logging.ERROR, logger="vans_signals_forwarder"):
            logging.getLogger("pokemon_world_mcp.catalog").error("delivery will fail")
            _wait_until(lambda: len(receiver.posts) >= 1 and bool(_failure_logs(caplog)))
        time.sleep(0.3)
        assert len(receiver.posts) == 1
        assert len(_failure_logs(caplog)) == 1
        assert "pokemon-token" not in _failure_logs(caplog)[0].getMessage()
        assert not any(
            record.name.startswith("pokemon_world_mcp")
            for record in caplog.records
            if record.levelno >= logging.ERROR and "Signal post failed" in record.getMessage()
        )
    finally:
        forwarder.close()
        receiver.close()


def test_warning_and_unrelated_logger_produce_no_signal(signals) -> None:
    receiver = signals
    logging.getLogger("pokemon_world_mcp.catalog").warning(
        "PokéAPI backoff; using stale catalog cache"
    )
    logging.getLogger("pokemon_world_mcp.catalog").warning(
        "using stale catalog cache after PokéAPI failure"
    )
    logging.getLogger("pokemon_world_mcp.catalog").warning(
        "catalog using in-memory fallback only (3 species); not writing to DB"
    )
    logging.getLogger("uvicorn.error").error("web server failed")
    logging.getLogger("vans_signals_forwarder").error("Signal post failed: ConnectError")
    time.sleep(0.15)
    assert receiver.posts == []
