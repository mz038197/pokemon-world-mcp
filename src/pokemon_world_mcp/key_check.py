"""Key Check client: ask the router whether a ``vcr_sk_`` key may be used.

One call returns an ``Identity`` or a ``Refusal`` (router ADRs 0077, 0079, 0082).
This module is this repository's own client; there is no shared package.

- An accepted answer is kept for 60 seconds, keyed by the key's SHA-256, never
  the key. A refusal is not kept. A kept answer is not extended while the
  router is down.
- The router owns every refusal notice; ``message`` is passed on as it is. The
  one notice carried here is ``unavailable``: the router could not be reached,
  timed out (3 seconds), or answered 5xx, 429, 401 or anything off-contract.
  Each such failure logs one ERROR, which the Signal forwarder sends on.
- No log line holds more than the key prefix.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

DEFAULT_VCR_URL = "https://ai.vanscoding.com"
KEY_CHECK_PATH = "/internal/key-check"
VCR_KEY_PREFIX = "vcr_sk_"
# The router stores the first 14 characters as the key prefix.
KEY_PREFIX_LENGTH = 14
KEY_CHECK_TIMEOUT_SEC = 3.0
ACCEPTED_TTL_SEC = 60.0
INVALID_NOTICE = "無效的 API 金鑰"
UNAVAILABLE_NOTICE = "驗證服務暫時無法使用，請稍後再試"


@dataclass(frozen=True)
class ClassRef:
    id: int
    name: str


@dataclass(frozen=True)
class Identity:
    """An accepted key. ``user_id`` is ``student_id`` for a Classroom API Key."""

    kind: str
    user_id: int
    class_id: int | None = None
    class_session_id: int | None = None
    is_admin: bool = False
    classes: tuple[ClassRef, ...] = ()


@dataclass(frozen=True)
class Refusal:
    reason: str
    message: str


INVALID = Refusal(reason="invalid", message=INVALID_NOTICE)
UNAVAILABLE = Refusal(reason="unavailable", message=UNAVAILABLE_NOTICE)


def key_prefix(api_key: str) -> str:
    """The part of a key that may appear in a log line or a Signal."""
    return api_key.strip()[:KEY_PREFIX_LENGTH]


class _RouterCannotAnswer(Exception):
    pass


class KeyCheckClient:
    def __init__(
        self,
        *,
        vcr_url: str,
        service_credential: str,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = KEY_CHECK_TIMEOUT_SEC,
    ) -> None:
        self._url = vcr_url.rstrip("/") + KEY_CHECK_PATH
        self._credential = service_credential
        self._transport = transport
        self._clock = clock
        self._timeout = timeout
        self._kept: dict[str, tuple[float, Identity]] = {}

    async def check(self, api_key: str | None) -> Identity | Refusal:
        api_key = (api_key or "").strip()
        if not api_key.startswith(VCR_KEY_PREFIX):
            return INVALID
        digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        kept = self._kept.get(digest)
        if kept is not None and self._clock() < kept[0]:
            return kept[1]
        self._kept.pop(digest, None)
        answer = await self._ask_router(api_key)
        if isinstance(answer, Identity):
            self._kept[digest] = (self._clock() + ACCEPTED_TTL_SEC, answer)
        return answer

    async def _ask_router(self, api_key: str) -> Identity | Refusal:
        try:
            body = await asyncio.wait_for(self._post(api_key), timeout=self._timeout)
            return _answer(body)
        except _RouterCannotAnswer as exc:
            failure = str(exc)
        except TimeoutError:
            failure = f"no answer within {self._timeout:g}s"
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            # Only the type: an exception text could repeat the request.
            failure = type(exc).__name__
        logger.error("Key Check unavailable for key %s: %s", key_prefix(api_key), failure)
        return UNAVAILABLE

    async def _post(self, api_key: str) -> dict:
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
            response = await client.post(
                self._url,
                json={"api_key": api_key},
                headers={"Authorization": f"Bearer {self._credential}"},
            )
        if response.status_code != 200:
            raise _RouterCannotAnswer(f"router answered HTTP {response.status_code}")
        body = response.json()
        if not isinstance(body, dict):
            raise _RouterCannotAnswer("router answer is not a JSON object")
        return body


def _answer(body: dict) -> Identity | Refusal:
    if body["accepted"] is False:
        return Refusal(reason=str(body["reason"]), message=str(body["message"]))
    if body["accepted"] is not True:
        raise _RouterCannotAnswer("router answer has no accepted flag")
    if body["kind"] == "classroom":
        return Identity(
            kind="classroom",
            user_id=int(body["student_id"]),
            class_id=int(body["class_id"]),
            class_session_id=int(body["class_session_id"]),
        )
    if body["kind"] == "personal":
        return Identity(
            kind="personal",
            user_id=int(body["user_id"]),
            is_admin=body["is_admin"] is True,
            classes=tuple(
                ClassRef(id=int(item["id"]), name=str(item["name"]))
                for item in body["classes"]
            ),
        )
    raise _RouterCannotAnswer("router answer has an unknown kind")
