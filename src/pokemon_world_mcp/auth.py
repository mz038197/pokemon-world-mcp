from __future__ import annotations

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
    INVALID_NOTICE,
    UNAVAILABLE,
    Identity,
    KeyCheckClient,
    Refusal,
    key_prefix,
)

logger = logging.getLogger(__name__)

VCR_URL_ENV = "VCR_URL"
SERVICE_CREDENTIAL_ENV = "VCR_SERVICE_CREDENTIAL"
BYPASS_KEY_ENV = "MCP_DEV_BYPASS_KEY"
PRODUCTION_ENV = "FLY_APP_NAME"


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
        key_check: KeyCheckClient | None = None,
        bypass_key: str | None = None,
    ) -> None:
        # base_url=None -> no RFC 9728 resource_metadata on 401 WWW-Authenticate
        super().__init__(base_url=None)
        self.key_check = key_check
        self.bypass_key = normalize_api_key(bypass_key) if bypass_key else ""

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> VcrApiKeyVerifier:
        """Build from settings; refuse to start on a production misconfiguration."""
        env = os.environ if environ is None else environ
        production = bool((env.get(PRODUCTION_ENV) or "").strip())
        bypass_key = (env.get(BYPASS_KEY_ENV) or "").strip()
        credential = (env.get(SERVICE_CREDENTIAL_ENV) or "").strip()
        if production and bypass_key:
            raise RuntimeError(
                f"{BYPASS_KEY_ENV} is set while {PRODUCTION_ENV} is set; refusing to start"
            )
        if production and not credential:
            raise RuntimeError(
                f"{SERVICE_CREDENTIAL_ENV} is required when {PRODUCTION_ENV} is set"
            )
        key_check = None
        if credential:
            key_check = KeyCheckClient(
                vcr_url=(env.get(VCR_URL_ENV) or "").strip() or DEFAULT_VCR_URL,
                service_credential=credential,
            )
        return cls(key_check=key_check, bypass_key=bypass_key or None)

    @property
    def auth_mode(self) -> str:
        return "key_check" if self.key_check is not None else "bypass_or_unconfigured"

    async def authenticate(self, token: str | None) -> AccessToken | Refusal:
        token = normalize_api_key(token)
        if self.bypass_key and token == self.bypass_key:
            return AccessToken(
                token=token,
                client_id="dev-bypass",
                scopes=["mcp:read"],
                expires_at=None,
                claims={
                    "user_id": 0,
                    "kind": "personal",
                    "class_id": None,
                    "key_prefix": "dev",
                    "auth_mode": "bypass",
                },
            )
        if self.key_check is None:
            logger.warning(
                "%s unset and token %s is not %s",
                SERVICE_CREDENTIAL_ENV,
                key_prefix(token),
                BYPASS_KEY_ENV,
            )
            return UNAVAILABLE
        answer = await self.key_check.check(token)
        if isinstance(answer, Refusal):
            return answer
        return _access_token(token, answer)

    async def verify_token(self, token: str) -> AccessToken | None:
        answer = await self.authenticate(token)
        return answer if isinstance(answer, AccessToken) else None


def _access_token(token: str, identity: Identity) -> AccessToken:
    # `user_id` is the Classroom `student_id` or the Personal `user_id`: the same
    # router integer the saves are keyed by.
    return AccessToken(
        token=token,
        client_id=str(identity.user_id),
        scopes=["mcp:read"],
        expires_at=None,
        claims={
            "user_id": identity.user_id,
            "kind": identity.kind,
            "class_id": identity.class_id,
            "key_prefix": key_prefix(token),
            "auth_mode": "key_check",
        },
    )


def unauthorized_response(message: str) -> Response:
    """Plain 401 whose body is the notice. No ``WWW-Authenticate: Bearer``."""
    return Response(
        content=message.encode("utf-8"),
        status_code=401,
        media_type="text/plain; charset=utf-8",
    )


class KeyCheckAuthBackend(AuthenticationBackend):
    """Bearer token -> ``AuthenticatedUser``; a refusal raises with its notice."""

    def __init__(self, verifier: VcrApiKeyVerifier) -> None:
        self.verifier = verifier

    async def authenticate(self, conn: HTTPConnection):
        header = conn.headers.get("authorization")
        if not header or not header.lower().startswith("bearer "):
            return None
        answer = await self.verifier.authenticate(header[7:])
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
            await unauthorized_response(INVALID_NOTICE)(scope, receive, send)
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
