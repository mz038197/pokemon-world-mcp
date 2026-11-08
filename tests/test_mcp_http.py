"""The MCP HTTP endpoint with the Key Check client, the router faked by MockTransport.

Router answers come from the contract examples in ``tests/fixtures/key_check/``
(see ``test_key_check.py`` for their source commit).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from starlette.testclient import TestClient

from pokemon_world_mcp.auth import VcrApiKeyVerifier
from pokemon_world_mcp.catalog import Catalog
from pokemon_world_mcp.game import GameService
from pokemon_world_mcp.key_check import KeyCheckClient
from pokemon_world_mcp.save_store import MemorySaveStore

CONTRACT = Path(__file__).resolve().parent / "fixtures" / "key_check"
STUDENT_KEY = "vcr_sk_" + "a1" * 32
TEACHER_KEY = "vcr_sk_" + "b2" * 32
ACCEPT = "application/json, text/event-stream"


def _example(name: str) -> dict:
    return json.loads((CONTRACT / name).read_text(encoding="utf-8"))


def _app_module():
    # Imported late so tests/test_signals.py imports the app first (it checks the
    # import order of the Signal forwarder); Catalog.load stays off the network.
    with patch.object(Catalog, "load", return_value=Catalog()):
        from pokemon_world_mcp import app as pokemon_app
    return pokemon_app


class FakeRouter:
    def __init__(self, answers: dict[str, tuple[int, dict]]) -> None:
        self.answers = answers
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        api_key = json.loads(request.content)["api_key"]
        status, body = self.answers.get(api_key, (200, _example("refused.json")))
        return httpx.Response(status, json=body)


@pytest.fixture
def store(monkeypatch) -> MemorySaveStore:
    pokemon_app = _app_module()
    saves = MemorySaveStore()
    monkeypatch.setattr(pokemon_app, "game", GameService(saves, Catalog()))
    return saves


def _client(router: FakeRouter | None = None, *, bypass_key: str | None = None) -> TestClient:
    pokemon_app = _app_module()
    key_check = (
        KeyCheckClient(
            vcr_url="https://router.test",
            service_credential="pokemon-credential",
            transport=httpx.MockTransport(router),
        )
        if router is not None
        else None
    )
    verifier = VcrApiKeyVerifier(key_check=key_check, bypass_key=bypass_key)
    return TestClient(pokemon_app.create_app(verifier))


def _rpc(client: TestClient, key: str, method: str, params: dict | None = None, *, session: str | None = None):
    headers = {"Authorization": f"Bearer {key}", "Accept": ACCEPT}
    if session:
        headers["mcp-session-id"] = session
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not method.startswith("notifications/"):
        body["id"] = 1
    return client.post("/mcp/", json=body, headers=headers)


def _result(response: httpx.Response) -> dict:
    if response.headers["content-type"].startswith("text/event-stream"):
        data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1])
    return response.json()


def _call_tool(client: TestClient, key: str, name: str, arguments: dict | None = None) -> dict:
    init = _rpc(
        client,
        key,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    )
    assert init.status_code == 200, init.text
    session = init.headers.get("mcp-session-id")
    _rpc(client, key, "notifications/initialized", session=session)
    response = _rpc(client, key, "tools/call", {"name": name, "arguments": arguments or {}}, session=session)
    assert response.status_code == 200, response.text
    content = _result(response)["result"]["content"]
    return json.loads(content[0]["text"])


def _assert_plain_401(response: httpx.Response, message: str) -> None:
    assert response.status_code == 401
    assert response.text == message
    assert response.headers["content-length"] == str(len(response.content))
    assert "www-authenticate" not in {key.lower() for key in response.headers}


def test_a_classroom_key_plays_on_the_save_kept_under_its_student_id(store) -> None:
    store_game = _app_module().game
    store_game.new_game(1234, "squirtle")
    router = FakeRouter({STUDENT_KEY: (200, _example("classroom-accepted.json"))})

    with _client(router) as client:
        party = _call_tool(client, STUDENT_KEY, "party")

    assert party["party"][0]["name"] == "squirtle"


def test_a_personal_key_plays_under_its_user_id(store) -> None:
    router = FakeRouter({TEACHER_KEY: (200, _example("personal-accepted.json"))})

    with _client(router) as client:
        started = _call_tool(client, TEACHER_KEY, "new_game", {"starter": "bulbasaur"})

    assert started["ok"] is True
    assert store.load(42) is not None


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (_example("refused.json"), "無效的 API 金鑰"),
        (
            {"accepted": False, "reason": "not_started", "message": "課堂尚未開始，請在開始時間後再使用"},
            "課堂尚未開始，請在開始時間後再使用",
        ),
        (
            {"accepted": False, "reason": "expired", "message": "API 金鑰已過期，請至 Portal 重新取得邀請碼"},
            "API 金鑰已過期，請至 Portal 重新取得邀請碼",
        ),
    ],
    ids=["invalid", "not_started", "expired"],
)
def test_a_refused_key_gets_a_plain_401_with_the_routers_message(store, answer, message) -> None:
    router = FakeRouter({STUDENT_KEY: (200, answer)})

    with _client(router) as client:
        response = _rpc(client, STUDENT_KEY, "initialize")

    _assert_plain_401(response, message)


def test_an_unreachable_router_gets_a_plain_401_with_the_unavailable_notice(store) -> None:
    router = FakeRouter({STUDENT_KEY: (503, {"error": "down"})})

    with _client(router) as client:
        response = _rpc(client, STUDENT_KEY, "initialize")

    _assert_plain_401(response, "驗證服務暫時無法使用，請稍後再試")


def test_no_authorization_header_gets_a_plain_401(store) -> None:
    router = FakeRouter({})

    with _client(router) as client:
        response = client.post("/mcp/", json={}, headers={"Accept": ACCEPT})

    _assert_plain_401(response, "無效的 API 金鑰")
    assert router.calls == 0


def test_the_dev_bypass_key_plays_without_a_router(store) -> None:
    with _client(None, bypass_key="vcr_sk_dev_local_only") as client:
        started = _call_tool(client, "vcr_sk_dev_local_only", "new_game", {"starter": "charmander"})

    assert started["ok"] is True
    assert store.load(0) is not None


def test_without_a_service_credential_a_real_key_is_refused_as_unavailable(store) -> None:
    with _client(None, bypass_key="vcr_sk_dev_local_only") as client:
        response = _rpc(client, STUDENT_KEY, "initialize")

    _assert_plain_401(response, "驗證服務暫時無法使用，請稍後再試")


def test_health_reports_key_check_auth(store) -> None:
    router = FakeRouter({})

    with _client(router) as client:
        health = client.get("/health").json()

    assert health["auth"] == "key_check"
    assert router.calls == 0
