from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping

from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
)
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from fastmcp.server.auth import AccessToken, TokenVerifier

from pokemon_world_mcp.key_check import (
    DEFAULT_VCR_URL,
    KeyCheckClient,
    Refusal,
    key_prefix,
)

logger = logging.getLogger(__name__)

VCR_URL_ENV = "VCR_URL"
SERVICE_CREDENTIAL_ENV = "VCR_SERVICE_CREDENTIAL"
BYPASS_KEY_ENV = "MCP_DEV_BYPASS_KEY"
PRODUCTION_ENV = "FLY_APP_NAME"
SCOPES = ["mcp:read"]
BEARER = "bearer "


def normalize_api_key(api_key: str | None) -> str:
    if not api_key:
        return ""
    return api_key.strip()


class VcrApiKeyVerifier(TokenVerifier):
    """Bearer vcr_sk_ -> the router's Key Check (or the local bypass key).

    Intentionally does not set ``base_url`` / resource metadata. Advertising
    ``resource_metadata`` makes VS Code / Cursor start an OAuth DCR flow and
    ignore static ``Authorization: Bearer vcr_sk_...`` headers.
    """

    def __init__(
        self,
        *,
        key_check: KeyCheckClient,
        bypass_key: str | None = None,
    ) -> None:
        # base_url=None -> no RFC 9728 resource_metadata on 401 WWW-Authenticate
        super().__init__(base_url=None)
        self.key_check = key_check
        self.bypass_key = normalize_api_key(bypass_key) if bypass_key else ""

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> VcrApiKeyVerifier:
        """Build from settings; the bypass key refuses to start in production.

        A missing ``VCR_SERVICE_CREDENTIAL`` does not stop the start: the router answers
        401, so every key is unavailable and each try sends an ERROR Signal
        (the same as vans-mcp-server).
        """
        env = os.environ if environ is None else environ
        production = bool((env.get(PRODUCTION_ENV) or "").strip())
        bypass_key = (env.get(BYPASS_KEY_ENV) or "").strip()
        if production and bypass_key:
            raise RuntimeError(
                f"{BYPASS_KEY_ENV} is set while {PRODUCTION_ENV} is set; refusing to start"
            )
        key_check = KeyCheckClient(
            vcr_url=(env.get(VCR_URL_ENV) or "").strip() or DEFAULT_VCR_URL,
            service_credential=(env.get(SERVICE_CREDENTIAL_ENV) or "").strip(),
        )
        return cls(key_check=key_check, bypass_key=bypass_key or None)

    @property
    def auth_mode(self) -> str:
        return "key_check"

    async def authenticate(self, token: str | None) -> AccessToken | Refusal:
        token = normalize_api_key(token)
        if self.bypass_key and token == self.bypass_key:
            return _access_token(
                token,
                client_id="dev-bypass",
                user_id=0,
                kind="personal",
                class_id=None,
                prefix="dev",
                auth_mode="bypass",
            )
        answer = await self.key_check.check(token)
        if isinstance(answer, Refusal):
            return answer
        # `user_id` is the Classroom `student_id` or the Personal `user_id`: the same
        # router integer the saves are keyed by.
        return _access_token(
            token,
            client_id=str(answer.user_id),
            user_id=answer.user_id,
            kind=answer.kind,
            class_id=answer.class_id,
            prefix=key_prefix(token),
            auth_mode="key_check",
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        answer = await self.authenticate(token)
        return answer if isinstance(answer, AccessToken) else None


def _access_token(
    token: str,
    *,
    client_id: str,
    user_id: int,
    kind: str,
    class_id: int | None,
    prefix: str,
    auth_mode: str,
) -> AccessToken:
    """The one place the claims the tools read are built."""
    return AccessToken(
        token=token,
        client_id=client_id,
        scopes=SCOPES,
        expires_at=None,
        claims={
            "user_id": user_id,
            "kind": kind,
            "class_id": class_id,
            "key_prefix": prefix,
            "auth_mode": auth_mode,
        },
    )


def unauthorized_response(message: str | None = None) -> Response:
    """Plain 401, the same body as vans-mcp-server: ``{"error": "unauthorized", "message": ...}``.

    ``message`` is the router's notice, left out when there was no key to check.
    No ``WWW-Authenticate: Bearer``, so VS Code keeps the configured header.
    """
    body: dict = {"error": "unauthorized"}
    if message:
        body["message"] = message
    return Response(
        json.dumps(body, ensure_ascii=False),
        status_code=401,
        media_type="application/json",
    )


class KeyCheckAuthBackend(AuthenticationBackend):
    """Bearer token -> ``AuthenticatedUser``; a refusal raises with its notice."""

    def __init__(self, verifier: VcrApiKeyVerifier) -> None:
        self.verifier = verifier

    async def authenticate(self, conn: HTTPConnection):
        header = conn.headers.get("authorization")
        if not header or not header.lower().startswith(BEARER):
            return None
        answer = await self.verifier.authenticate(header[len(BEARER) :])
        if isinstance(answer, Refusal):
            raise AuthenticationError(answer.message)
        return AuthCredentials(answer.scopes), AuthenticatedUser(answer)


def _refused(_conn: HTTPConnection, exc: AuthenticationError) -> Response:
    return unauthorized_response(str(exc))


class RequireApiKeyMiddleware:
    """Reject unauthenticated MCP requests without advertising OAuth.

    FastMCP's built-in RequireAuthMiddleware sends ``WWW-Authenticate: Bearer``,
    which makes VS Code start Dynamic Client Registration. API-key clients only
    need a plain 401 so configured ``Authorization`` headers are used instead.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not isinstance(scope.get("user"), AuthenticatedUser):
            await unauthorized_response()(scope, receive, send)
            return
        await self.app(scope, receive, send)


def api_key_http_middleware(verifier: VcrApiKeyVerifier) -> list[Middleware]:
    return [
        Middleware(
            AuthenticationMiddleware,
            backend=KeyCheckAuthBackend(verifier),
            on_error=_refused,
        ),
        Middleware(AuthContextMiddleware),
        Middleware(RequireApiKeyMiddleware),
    ]
