from __future__ import annotations

import pytest

from pokemon_world_mcp.auth import RequireApiKeyMiddleware, VcrApiKeyVerifier, normalize_api_key


@pytest.mark.asyncio
async def test_bypass_key_accepts() -> None:
    v = VcrApiKeyVerifier(database_url=None, bypass_key="vcr_sk_dev")
    token = await v.verify_token("vcr_sk_dev")
    assert token is not None
    assert token.claims["user_id"] == 0
    assert token.claims["auth_mode"] == "bypass"


@pytest.mark.asyncio
async def test_wrong_key_rejected_without_db() -> None:
    v = VcrApiKeyVerifier(database_url=None, bypass_key="vcr_sk_dev")
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
    assert len(body) == 24
    # Must not advertise OAuth (no WWW-Authenticate) or VS Code starts DCR.
    assert b"www-authenticate" not in {k.lower() for k in headers}
