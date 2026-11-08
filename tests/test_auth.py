from __future__ import annotations

import pytest

import json

import httpx

from pokemon_world_mcp.auth import RequireApiKeyMiddleware, VcrApiKeyVerifier, normalize_api_key
from pokemon_world_mcp.key_check import DEFAULT_VCR_URL, KeyCheckClient


def _unreachable_key_check() -> KeyCheckClient:
    def router(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no router in this test")

    return KeyCheckClient(
        vcr_url="https://router.test",
        service_credential="credential",
        transport=httpx.MockTransport(router),
    )


def test_the_bypass_key_starts_locally() -> None:
    verifier = VcrApiKeyVerifier.from_env({"MCP_DEV_BYPASS_KEY": "vcr_sk_dev"})
    assert verifier.bypass_key == "vcr_sk_dev"


def test_the_bypass_key_refuses_to_start_in_production() -> None:
    with pytest.raises(RuntimeError, match="MCP_DEV_BYPASS_KEY"):
        VcrApiKeyVerifier.from_env(
            {
                "FLY_APP_NAME": "pokemon-world-mcp",
                "MCP_DEV_BYPASS_KEY": "vcr_sk_dev",
                "VCR_SERVICE_CREDENTIAL": "credential",
            }
        )


def test_production_without_a_service_credential_starts_and_asks_the_router_anyway() -> None:
    # Same as vans-mcp-server: the router answers 401, so every key is unavailable + ERROR.
    verifier = VcrApiKeyVerifier.from_env({"FLY_APP_NAME": "pokemon-world-mcp"})
    assert verifier.auth_mode == "key_check"


def test_production_with_a_service_credential_uses_the_key_check() -> None:
    verifier = VcrApiKeyVerifier.from_env(
        {"FLY_APP_NAME": "pokemon-world-mcp", "VCR_SERVICE_CREDENTIAL": "credential"}
    )
    assert verifier.auth_mode == "key_check"
    assert verifier.bypass_key == ""


def test_vcr_url_defaults_to_the_public_router() -> None:
    assert DEFAULT_VCR_URL == "https://ai.vanscoding.com"


@pytest.mark.asyncio
async def test_bypass_key_accepts() -> None:
    v = VcrApiKeyVerifier(key_check=_unreachable_key_check(), bypass_key="vcr_sk_dev")
    token = await v.verify_token("vcr_sk_dev")
    assert token is not None
    assert token.claims["user_id"] == 0
    assert token.claims["auth_mode"] == "bypass"


@pytest.mark.asyncio
async def test_wrong_key_rejected_when_the_router_cannot_answer() -> None:
    v = VcrApiKeyVerifier(key_check=_unreachable_key_check(), bypass_key="vcr_sk_dev")
    assert await v.verify_token("vcr_sk_other") is None


def test_normalize_api_key() -> None:
    assert normalize_api_key("  abc  ") == "abc"
    assert normalize_api_key(None) == ""


@pytest.mark.asyncio
async def test_require_api_key_401_content_length_matches_body() -> None:
    mw = RequireApiKeyMiddleware(app=None)  # type: ignore[arg-type]
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    async def receive() -> dict:
        return {"type": "http.disconnect"}

    await mw({"type": "http", "user": None}, receive, send)

    assert sent[0]["status"] == 401
    headers = dict(sent[0]["headers"])
    body = sent[1]["body"]
    assert headers[b"content-length"] == str(len(body)).encode("ascii")
    assert json.loads(body) == {"error": "unauthorized"}
    # Must not advertise OAuth (no WWW-Authenticate) or VS Code starts DCR.
    assert b"www-authenticate" not in {k.lower() for k in headers}
