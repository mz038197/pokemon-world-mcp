"""The Key Check client, faked at the router's HTTP boundary.

The router answers come from the router's contract examples, copied into
``tests/fixtures/key_check/`` from mz038197/vans_coding_router
``docs/contracts/key-check/`` at commit bff594f (branch feat/key-check).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx
import pytest

from pokemon_world_mcp.key_check import ClassRef, Identity, KeyCheckClient, Refusal

CONTRACT = Path(__file__).resolve().parent / "fixtures" / "key_check"
ROUTER = "https://router.test"
CREDENTIAL = "pokemon-service-credential"
STUDENT_KEY = "vcr_sk_" + "a1" * 32


def _example(name: str) -> dict:
    return json.loads((CONTRACT / name).read_text(encoding="utf-8"))


class FakeRouter:
    """Answers POST /internal/key-check with a fixed response and records each call."""

    def __init__(self, body: dict | None = None, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def _client(router: FakeRouter, **kwargs) -> KeyCheckClient:
    return KeyCheckClient(
        vcr_url=ROUTER,
        service_credential=CREDENTIAL,
        transport=router.transport,
        **kwargs,
    )


async def test_accepted_classroom_key_is_a_student_identity() -> None:
    router = FakeRouter(_example("classroom-accepted.json"))

    answer = await _client(router).check(STUDENT_KEY)

    assert answer == Identity(
        kind="classroom",
        user_id=1234,
        class_id=56,
        class_session_id=789,
        is_admin=False,
        classes=(),
    )
    (request,) = router.requests
    assert request.method == "POST"
    assert str(request.url) == "https://router.test/internal/key-check"
    assert request.headers["authorization"] == f"Bearer {CREDENTIAL}"
    assert json.loads(request.content) == {"api_key": STUDENT_KEY}


async def test_accepted_personal_key_carries_admin_flag_and_classes() -> None:
    router = FakeRouter(_example("personal-accepted.json"))

    answer = await _client(router).check("vcr_sk_" + "b2" * 32)

    assert answer == Identity(
        kind="personal",
        user_id=42,
        class_id=None,
        class_session_id=None,
        is_admin=False,
        classes=(ClassRef(56, "週一 程式設計"), ClassRef(57, "週三 進階班")),
    )


async def test_refusal_carries_the_routers_reason_and_message_as_is() -> None:
    router = FakeRouter(_example("refused.json"))

    answer = await _client(router).check(STUDENT_KEY)

    assert answer == Refusal(reason="invalid", message="無效的 API 金鑰")


async def test_a_refusal_with_any_router_notice_is_shown_unchanged() -> None:
    router = FakeRouter(
        {"accepted": False, "reason": "not_started", "message": "課堂尚未開始，請在開始時間後再使用"}
    )

    answer = await _client(router).check(STUDENT_KEY)

    assert answer == Refusal(reason="not_started", message="課堂尚未開始，請在開始時間後再使用")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def test_an_accepted_answer_is_kept_for_60_seconds_then_asked_again() -> None:
    router = FakeRouter(_example("classroom-accepted.json"))
    clock = FakeClock()
    client = _client(router, clock=clock)

    first = await client.check(STUDENT_KEY)
    clock.advance(59.9)
    second = await client.check(STUDENT_KEY)
    assert len(router.requests) == 1
    assert second == first

    clock.advance(0.2)
    await client.check(STUDENT_KEY)
    assert len(router.requests) == 2


async def test_the_kept_answer_is_per_key() -> None:
    router = FakeRouter(_example("classroom-accepted.json"))
    client = _client(router, clock=FakeClock())

    await client.check(STUDENT_KEY)
    await client.check("vcr_sk_" + "c3" * 32)

    assert len(router.requests) == 2


async def test_a_refusal_is_not_kept() -> None:
    router = FakeRouter(_example("refused.json"))
    client = _client(router, clock=FakeClock())

    await client.check(STUDENT_KEY)
    await client.check(STUDENT_KEY)

    assert len(router.requests) == 2


@pytest.mark.parametrize("api_key", ["", "   ", "sk-not-ours", "Bearer vcr_sk_x", "VCR_SK_upper"])
async def test_a_key_without_the_vcr_sk_prefix_is_invalid_without_asking_the_router(
    api_key: str,
) -> None:
    router = FakeRouter(_example("classroom-accepted.json"))

    answer = await _client(router).check(api_key)

    assert answer == Refusal(reason="invalid", message="無效的 API 金鑰")
    assert router.requests == []


async def test_the_key_is_trimmed_before_it_is_checked() -> None:
    router = FakeRouter(_example("classroom-accepted.json"))

    answer = await _client(router).check(f"  {STUDENT_KEY}\n")

    assert isinstance(answer, Identity)
    assert json.loads(router.requests[0].content) == {"api_key": STUDENT_KEY}


UNAVAILABLE = Refusal(reason="unavailable", message="驗證服務暫時無法使用，請稍後再試")


def _errors(caplog) -> list:
    """ERROR records under pokemon_world_mcp: each one is forwarded as a Signal."""
    return [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR and record.name.startswith("pokemon_world_mcp")
    ]


def _logged_text(caplog) -> str:
    return "\n".join(
        record.getMessage() + (logging.Formatter().formatException(record.exc_info) if record.exc_info else "")
        for record in caplog.records
    )


def _unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _router_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


@pytest.mark.parametrize(
    "transport",
    [
        httpx.MockTransport(_unreachable),
        httpx.MockTransport(_router_timeout),
        FakeRouter({"error": "boom"}, status=500).transport,
        FakeRouter({"error": "boom"}, status=503).transport,
        FakeRouter({"error": {"code": "rate_limited"}}, status=429).transport,
        FakeRouter(
            {"error": {"code": "invalid_service_credential", "message": "A Partner Service Service Credential is required."}},
            status=401,
        ).transport,
        FakeRouter(_example("bad-request.json"), status=400).transport,
    ],
    ids=["unreachable", "timeout", "500", "503", "429", "401", "400"],
)
async def test_a_router_that_cannot_answer_refuses_as_unavailable_with_one_error(
    transport: httpx.MockTransport, caplog
) -> None:
    client = KeyCheckClient(vcr_url=ROUTER, service_credential=CREDENTIAL, transport=transport)

    with caplog.at_level(logging.WARNING):
        answer = await client.check(STUDENT_KEY)

    assert answer == UNAVAILABLE
    errors = _errors(caplog)
    assert len(errors) == 1
    assert STUDENT_KEY[:14] in errors[0].getMessage()
    assert STUDENT_KEY not in _logged_text(caplog)
    assert CREDENTIAL not in _logged_text(caplog)


async def test_an_answer_that_is_not_the_contract_is_unavailable(caplog) -> None:
    router = FakeRouter({"accepted": True, "kind": "mystery"})
    with caplog.at_level(logging.WARNING):
        answer = await _client(router).check(STUDENT_KEY)

    assert answer == UNAVAILABLE
    assert len(_errors(caplog)) == 1
    assert STUDENT_KEY not in _logged_text(caplog)


async def test_a_refusal_from_the_router_is_not_an_error(caplog) -> None:
    router = FakeRouter(_example("refused.json"))
    with caplog.at_level(logging.WARNING):
        await _client(router).check(STUDENT_KEY)

    assert _errors(caplog) == []


async def test_an_expired_kept_answer_is_not_extended_while_the_router_is_down(caplog) -> None:
    router = FakeRouter(_example("classroom-accepted.json"))
    clock = FakeClock()
    client = _client(router, clock=clock)
    assert isinstance(await client.check(STUDENT_KEY), Identity)

    router.status, router.body = 503, {"error": "down"}
    clock.advance(30)
    assert isinstance(await client.check(STUDENT_KEY), Identity)
    clock.advance(31)
    with caplog.at_level(logging.WARNING):
        assert await client.check(STUDENT_KEY) == UNAVAILABLE
    clock.advance(1)
    assert await client.check(STUDENT_KEY) == UNAVAILABLE
    assert len(router.requests) == 3


async def test_a_router_slower_than_3_seconds_is_unavailable(caplog) -> None:
    async def slow_router(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        return httpx.Response(200, json=_example("classroom-accepted.json"))

    client = KeyCheckClient(
        vcr_url=ROUTER, service_credential=CREDENTIAL, transport=httpx.MockTransport(slow_router)
    )

    started = time.monotonic()
    with caplog.at_level(logging.WARNING):
        answer = await client.check(STUDENT_KEY)
    waited = time.monotonic() - started

    assert answer == UNAVAILABLE
    assert 2.9 <= waited < 4.0
    assert len(_errors(caplog)) == 1
