"""Unit tests for TokenManager: proactive refresh, single-flight, fallback.

Network calls are mocked with `respx`; time is controlled by a fake clock
injected into TokenManager so tests never actually sleep or wait on a real
7-minute token TTL.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import jwt
import pytest
import respx

from clm_mcp.auth.errors import IdentityServiceError, MissingCredentialsError
from clm_mcp.auth.store import load_credentials, save_credentials
from clm_mcp.auth.token_manager import TokenManager
from clm_mcp.config import Settings

IDENTITY_URL = "https://clm.selisestage.com/api/identity/v25/identity/token"
IDENTITY_ORIGIN = "https://clm.selisestage.com/"


class FakeClock:
    """A settable, monotonically-controllable clock for tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_jwt(*, exp: float, sub: str = "user-1", site_id: str = "site-1") -> str:
    """Build a JWT with the claims TokenManager reads. Signature is never
    verified by the code under test, so any key/algorithm is fine."""
    payload = {
        "sub": sub,
        "user_id": sub,
        "site_id": site_id,
        "user_name": "user@example.com",
        "role": ["admin"],
        "iat": int(time.time()),
        "exp": int(exp),
    }
    return jwt.encode(payload, key="test-signing-key-at-least-32-bytes-long!!", algorithm="HS256")


def token_response_body(
    *, access_token: str, refresh_token: str = "rt-1", expires_in: int = 420
) -> dict[str, object]:
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "scope": "offline_access",
    }


def make_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "identity_token_url": IDENTITY_URL,
        "identity_origin": IDENTITY_ORIGIN,
        "token_refresh_skew_seconds": 90.0,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


# --------------------------------------------------------------------------
# 1. Expiry-with-skew: proactive refresh fires before the token actually
#    expires, once remaining lifetime drops below the configured skew.
# --------------------------------------------------------------------------


async def test_refreshes_proactively_within_skew_window(http_client: httpx.AsyncClient) -> None:
    clock = FakeClock(start=0.0)
    settings = make_settings(refresh_token="initial-rt", token_refresh_skew_seconds=90.0)
    manager = TokenManager(settings, http_client, clock=clock)

    call_count = 0

    def responder(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        wall_now = time.time()
        token = make_jwt(exp=wall_now + 420)
        return httpx.Response(200, json=token_response_body(access_token=token))

    with respx.mock(assert_all_called=False) as mock:
        mock.post(IDENTITY_URL).mock(side_effect=responder)

        await manager.get_access_token()
        assert call_count == 1

        # Well within validity (420s TTL, 90s skew) -> no second call.
        clock.advance(300)  # 120s remaining, > 90s skew
        await manager.get_access_token()
        assert call_count == 1

        # Now inside the skew window -> must refresh again.
        clock.advance(35)  # 85s remaining, < 90s skew
        await manager.get_access_token()
        assert call_count == 2


# --------------------------------------------------------------------------
# 2. Single-flight: N concurrent callers on a cold (no active token)
#    manager must trigger exactly one HTTP refresh, not N.
# --------------------------------------------------------------------------


async def test_concurrent_calls_single_flight(http_client: httpx.AsyncClient) -> None:
    clock = FakeClock(start=0.0)
    settings = make_settings(refresh_token="initial-rt")
    manager = TokenManager(settings, http_client, clock=clock)

    call_count = 0
    release = asyncio.Event()

    async def responder(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        # Force a window where concurrent callers are guaranteed to observe
        # "no active token yet" before the first refresh completes.
        await release.wait()
        token = make_jwt(exp=time.time() + 420)
        return httpx.Response(200, json=token_response_body(access_token=token))

    with respx.mock(assert_all_called=False) as mock:
        mock.post(IDENTITY_URL).mock(side_effect=responder)

        tasks = [asyncio.create_task(manager.get_access_token()) for _ in range(10)]
        await asyncio.sleep(0)  # let every task reach the lock/HTTP call
        release.set()
        results = await asyncio.gather(*tasks)

    assert call_count == 1
    assert len({r for r in results}) == 1  # all callers got the same token


# --------------------------------------------------------------------------
# 3. invalid_grant on the refresh token falls back to a password grant
#    when credentials are available.
# --------------------------------------------------------------------------


async def test_invalid_refresh_token_falls_back_to_password_grant(
    http_client: httpx.AsyncClient,
) -> None:
    clock = FakeClock(start=0.0)
    settings = make_settings(
        refresh_token="stale-rt", username="user@example.com", password="hunter2"
    )
    manager = TokenManager(settings, http_client, clock=clock)

    calls: list[dict[str, str]] = []

    def responder(request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        calls.append(form)
        if form["grant_type"] == "refresh_token":
            return httpx.Response(
                400, json={"error": "invalid_grant", "error_description": "expired"}
            )
        assert form["grant_type"] == "password"
        token = make_jwt(exp=time.time() + 420)
        return httpx.Response(
            200, json=token_response_body(access_token=token, refresh_token="new-rt")
        )

    with respx.mock(assert_all_called=False) as mock:
        mock.post(IDENTITY_URL).mock(side_effect=responder)
        access_token = await manager.get_access_token()

    assert len(calls) == 2
    assert calls[0]["grant_type"] == "refresh_token"
    assert calls[1]["grant_type"] == "password"
    decoded = jwt.decode(access_token, options={"verify_signature": False})
    assert decoded["site_id"] == "site-1"


async def test_no_credentials_raises_missing_credentials_error(
    http_client: httpx.AsyncClient,
) -> None:
    # credentials_path must point somewhere that can't exist: the default
    # (~/.config/clm-mcp/credentials.json) is a real path that `clm-mcp
    # login` may have already populated on the machine running this test,
    # which would make this test pass or fail based on developer-machine
    # state instead of the code under test.
    settings = make_settings(credentials_path="/nonexistent/credentials.json")
    manager = TokenManager(settings, http_client, clock=FakeClock())

    with pytest.raises(MissingCredentialsError):
        await manager.get_access_token()


# --------------------------------------------------------------------------
# 4. Missing-Origin regression: every request to the identity endpoint must
#    carry the required Origin header (its absence returns 400 upstream).
# --------------------------------------------------------------------------


async def test_every_identity_request_carries_origin_header(
    http_client: httpx.AsyncClient,
) -> None:
    settings = make_settings(refresh_token="initial-rt")
    manager = TokenManager(settings, http_client, clock=FakeClock())

    seen_origins: list[str | None] = []

    def responder(request: httpx.Request) -> httpx.Response:
        seen_origins.append(request.headers.get("origin"))
        token = make_jwt(exp=time.time() + 420)
        return httpx.Response(200, json=token_response_body(access_token=token))

    with respx.mock(assert_all_called=False) as mock:
        mock.post(IDENTITY_URL).mock(side_effect=responder)
        await manager.get_access_token()

    assert seen_origins == [IDENTITY_ORIGIN]


# --------------------------------------------------------------------------
# 5. Exact skew-boundary: remaining == skew is treated as "needs refresh"
#    (the freshness check is a strict `>`, not `>=`) — a boundary a fake
#    clock can hit exactly, which real wall-clock timing never reliably can.
# --------------------------------------------------------------------------


async def test_remaining_exactly_equal_to_skew_triggers_refresh(
    http_client: httpx.AsyncClient,
) -> None:
    clock = FakeClock(start=0.0)
    settings = make_settings(refresh_token="initial-rt", token_refresh_skew_seconds=90.0)
    manager = TokenManager(settings, http_client, clock=clock)

    call_count = 0

    def responder(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        token = make_jwt(exp=time.time() + 420)
        return httpx.Response(200, json=token_response_body(access_token=token))

    with respx.mock(assert_all_called=False) as mock:
        mock.post(IDENTITY_URL).mock(side_effect=responder)
        await manager.get_access_token()
        assert call_count == 1

        # 420 - 330 = 90s remaining == skew exactly -> must still refresh.
        clock.advance(330)
        await manager.get_access_token()
        assert call_count == 2


# --------------------------------------------------------------------------
# 6. A malformed/undecodable access token from the identity service must
#    surface as IdentityServiceError (a ClmAuthError), not an uncaught
#    jwt.PyJWTError — the latter would bypass every call site that
#    translates ClmAuthError -> ToolError, reducing to a useless generic
#    "Error executing tool X" (the same bug class fixed for missing
#    credentials — see tests/test_auth_error_propagation.py).
# --------------------------------------------------------------------------


async def test_malformed_access_token_raises_identity_service_error(
    http_client: httpx.AsyncClient,
) -> None:
    settings = make_settings(refresh_token="initial-rt")
    manager = TokenManager(settings, http_client, clock=FakeClock())

    with respx.mock(assert_all_called=False) as mock:
        mock.post(IDENTITY_URL).mock(
            return_value=httpx.Response(
                200, json=token_response_body(access_token="not-a-real-jwt")
            )
        )
        with pytest.raises(IdentityServiceError):
            await manager.get_access_token()


async def test_access_token_missing_required_claims_raises_identity_service_error(
    http_client: httpx.AsyncClient,
) -> None:
    """A syntactically valid JWT that's missing claims JwtClaims requires
    (e.g. no `sub`) must also translate to IdentityServiceError, not a raw
    pydantic ValidationError."""
    settings = make_settings(refresh_token="initial-rt")
    manager = TokenManager(settings, http_client, clock=FakeClock())

    incomplete_token = jwt.encode(
        {"iat": int(time.time()), "exp": int(time.time() + 420)},  # no sub/user_id
        key="test-signing-key-at-least-32-bytes-long!!",
        algorithm="HS256",
    )

    with respx.mock(assert_all_called=False) as mock:
        mock.post(IDENTITY_URL).mock(
            return_value=httpx.Response(
                200, json=token_response_body(access_token=incomplete_token)
            )
        )
        with pytest.raises(IdentityServiceError):
            await manager.get_access_token()


# --------------------------------------------------------------------------
# 7. The identity service being unreachable during a refresh must not
#    corrupt state — a subsequent call, once it's reachable again, must
#    succeed normally rather than being stuck on a bad cached value.
# --------------------------------------------------------------------------


async def test_identity_service_unreachable_then_recovers_on_next_call(
    http_client: httpx.AsyncClient,
) -> None:
    settings = make_settings(refresh_token="initial-rt")
    manager = TokenManager(settings, http_client, clock=FakeClock())

    with respx.mock(assert_all_called=False) as mock:
        mock.post(IDENTITY_URL).mock(side_effect=httpx.ConnectError("connection refused"))
        with pytest.raises(IdentityServiceError):
            await manager.get_access_token()

    # A fresh mock, standing in for the identity service coming back up.
    with respx.mock(assert_all_called=False) as mock:
        mock.post(IDENTITY_URL).mock(
            return_value=httpx.Response(
                200, json=token_response_body(access_token=make_jwt(exp=time.time() + 420))
            )
        )
        token = await manager.get_access_token()
        assert token  # succeeded normally; no leftover bad state from the failed attempt


# --------------------------------------------------------------------------
# 8. Refresh-token rotation: if the identity service ever returns a
#    *different* refresh token than the one just used (it doesn't today —
#    see PLAN.md — but nothing guarantees that forever), the new one must
#    be what the next refresh actually sends, and — when it came from the
#    on-disk store — persisted back to it.
# --------------------------------------------------------------------------


async def test_rotated_refresh_token_is_used_on_next_refresh(
    http_client: httpx.AsyncClient,
) -> None:
    clock = FakeClock(start=0.0)
    settings = make_settings(refresh_token="rt-original")
    manager = TokenManager(settings, http_client, clock=clock)

    sent_refresh_tokens: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        sent_refresh_tokens.append(form.get("refresh_token", ""))
        token = make_jwt(exp=time.time() + 420)
        # Every grant returns a freshly rotated refresh token.
        new_rt = f"rt-rotated-{len(sent_refresh_tokens)}"
        return httpx.Response(
            200, json=token_response_body(access_token=token, refresh_token=new_rt)
        )

    with respx.mock(assert_all_called=False) as mock:
        mock.post(IDENTITY_URL).mock(side_effect=responder)

        await manager.get_access_token()
        clock.advance(400)  # inside the skew window -> forces a second refresh
        await manager.get_access_token()

    assert sent_refresh_tokens == ["rt-original", "rt-rotated-1"]


async def test_rotated_refresh_token_from_store_is_persisted(
    http_client: httpx.AsyncClient, tmp_path: Path
) -> None:
    creds_path = tmp_path / "credentials.json"
    save_credentials(creds_path, "rt-from-disk")

    clock = FakeClock(start=0.0)
    settings = make_settings(credentials_path=creds_path)
    manager = TokenManager(settings, http_client, clock=clock)

    def responder(_request: httpx.Request) -> httpx.Response:
        token = make_jwt(exp=time.time() + 420)
        return httpx.Response(
            200, json=token_response_body(access_token=token, refresh_token="rt-rotated-on-disk")
        )

    with respx.mock(assert_all_called=False) as mock:
        mock.post(IDENTITY_URL).mock(side_effect=responder)
        await manager.get_access_token()

    persisted = load_credentials(creds_path)
    assert persisted is not None
    assert persisted.refresh_token.get_secret_value() == "rt-rotated-on-disk"


# --------------------------------------------------------------------------
# 9. force_refresh() (triggered by the API client on an unexpected 401)
#    racing a concurrent proactive get_access_token() must not deadlock or
#    corrupt state — both share the same lock, so this proves that lock
#    isn't reentrant-broken or double-acquired.
# --------------------------------------------------------------------------


async def test_force_refresh_racing_get_access_token_does_not_deadlock(
    http_client: httpx.AsyncClient,
) -> None:
    clock = FakeClock(start=0.0)
    settings = make_settings(refresh_token="initial-rt")
    manager = TokenManager(settings, http_client, clock=clock)

    call_count = 0

    def responder(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        token = make_jwt(exp=time.time() + 420)
        return httpx.Response(200, json=token_response_body(access_token=token))

    with respx.mock(assert_all_called=False) as mock:
        mock.post(IDENTITY_URL).mock(side_effect=responder)

        results = await asyncio.wait_for(
            asyncio.gather(
                manager.get_access_token(),
                manager.force_refresh(),
                manager.get_access_token(),
            ),
            timeout=5.0,  # any deadlock fails the test instead of hanging forever
        )

    assert all(results)  # every call returned a non-empty token, none hung/crashed
