"""Edge-secret check on the trusted identity headers (``api/auth/principal.py``).

With ``CHORUS_EDGE_SECRET`` set, ``X-Auth-User`` and ``X-Auth-Name`` count
only on requests whose ``X-Edge-Secret`` matches; unset, they are trusted as
before. Every value below is invented.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger

SECRET = "edge-secret-for-tests-0123456789abcdef"
WRONG = "edge-secret-for-tests-fedcba9876543210"
SHORT = "short-edge-secret-0123456789"
USER = "analyst-7"
DISPLAY = "Example Analyst"
DEFAULT = "dev-default"
IDENTITY = {"X-Auth-User": USER, "X-Auth-Name": DISPLAY}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Neutral principal env: no edge secret, no default identity, default header names.

    Empty rather than deleted: ``_env`` reads empty as unset, and a module
    reload's ``load_dotenv`` never overrides a variable that is present.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        The same monkeypatch, for the test's own overrides.
    """
    for key in ("CHORUS_EDGE_SECRET", "CHORUS_DEFAULT_IDENTITY", "CHORUS_AUTH_HEADER", "CHORUS_DISPLAY_NAME_HEADER"):
        monkeypatch.setenv(key, "")
    return monkeypatch


@pytest.fixture
def logs() -> Iterator[list[str]]:
    """Capture every loguru message emitted during the test.

    Yields:
        The formatted log lines, in order.
    """
    records: list[str] = []
    sink_id = logger.add(lambda m: records.append(str(m)), level="DEBUG")
    yield records
    logger.remove(sink_id)


def _client() -> TestClient:
    """Client for a minimal app with the whoami router (no lifespan, no Neo4j)."""
    from chorus.api.routers import whoami as whoami_router

    app = FastAPI()
    app.include_router(whoami_router.router)
    return TestClient(app)


def _ignored_warnings(records: list[str]) -> list[str]:
    """Return the per-request "identity headers ignored" warnings."""
    return [r for r in records if "identity headers ignored" in r]


def _trust_warnings(records: list[str]) -> list[str]:
    """Return the startup "trusted from any caller" warnings."""
    return [r for r in records if "trusted from any caller" in r]


def _fallback_warnings(records: list[str]) -> list[str]:
    """Return the startup "default identity is ignored" warnings."""
    return [r for r in records if "CHORUS_DEFAULT_IDENTITY is ignored" in r]


# --- per-request check -------------------------------------------------------


def test_unset_secret_trusts_identity_headers_as_before(env: pytest.MonkeyPatch, logs: list[str]) -> None:
    """Without a secret the headers are trusted, whatever ``X-Edge-Secret`` says."""
    client = _client()
    edges: tuple[dict[str, str], ...] = ({}, {"X-Edge-Secret": WRONG})
    for edge in edges:
        resp = client.get("/whoami", headers={**IDENTITY, **edge})
        assert resp.status_code == 200
        assert resp.json() == {"username": USER, "display_name": DISPLAY}
    assert not _ignored_warnings(logs)


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_secret_counts_as_unset(env: pytest.MonkeyPatch, blank: str) -> None:
    """An empty or whitespace-only secret keeps the legacy behaviour."""
    env.setenv("CHORUS_EDGE_SECRET", blank)
    resp = _client().get("/whoami", headers=IDENTITY)
    assert resp.status_code == 200
    assert resp.json() == {"username": USER, "display_name": DISPLAY}


def test_matching_secret_trusts_identity_headers(env: pytest.MonkeyPatch, logs: list[str]) -> None:
    """With the matching ``X-Edge-Secret`` both identity headers are trusted."""
    env.setenv("CHORUS_EDGE_SECRET", SECRET)
    resp = _client().get("/whoami", headers={**IDENTITY, "X-Edge-Secret": SECRET})
    assert resp.status_code == 200
    assert resp.json() == {"username": USER, "display_name": DISPLAY}
    assert not _ignored_warnings(logs)


@pytest.mark.parametrize(
    "edge",
    [
        {},
        {"X-Edge-Secret": ""},
        {"X-Edge-Secret": WRONG},
        {"X-Edge-Secret": SECRET[:-1]},
        {"X-Edge-Secret": SECRET + "0"},
    ],
    ids=["missing", "empty", "wrong", "prefix", "longer"],
)
def test_missing_or_wrong_secret_ignores_identity_headers(env: pytest.MonkeyPatch, edge: dict[str, str]) -> None:
    """Without the matching secret a forged identity fails closed: 401, no default identity."""
    env.setenv("CHORUS_EDGE_SECRET", SECRET)
    resp = _client().get("/whoami", headers={**IDENTITY, **edge})
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [{}, {**IDENTITY, "X-Edge-Secret": WRONG}],
    ids=["no-identity", "unverified-identity"],
)
def test_default_identity_is_ignored_while_the_secret_is_set(env: pytest.MonkeyPatch, headers: dict[str, str]) -> None:
    """With the secret set, the dev default identity never applies: no verified identity means 401."""
    env.setenv("CHORUS_EDGE_SECRET", SECRET)
    env.setenv("CHORUS_DEFAULT_IDENTITY", DEFAULT)
    resp = _client().get("/whoami", headers=headers)
    assert resp.status_code == 401


def test_default_identity_applies_without_a_secret(env: pytest.MonkeyPatch) -> None:
    """Without the secret, a request without identity headers gets the dev default identity."""
    env.setenv("CHORUS_DEFAULT_IDENTITY", DEFAULT)
    resp = _client().get("/whoami")
    assert resp.status_code == 200
    assert resp.json() == {"username": DEFAULT, "display_name": None}


def test_ignored_headers_warning_names_method_and_path_only(env: pytest.MonkeyPatch, logs: list[str]) -> None:
    """One warning per request, with no secret, no header value and no query string."""
    env.setenv("CHORUS_EDGE_SECRET", SECRET)
    _client().get("/whoami?probe=query-marker", headers={**IDENTITY, "X-Edge-Secret": WRONG})
    warnings = _ignored_warnings(logs)
    assert len(warnings) == 1
    assert "identity headers ignored: missing or invalid edge secret on GET /whoami" in warnings[0]
    logged = "".join(logs)
    for value in (SECRET, WRONG, USER, DISPLAY, "query-marker"):
        assert value not in logged


def test_no_warning_without_identity_headers(env: pytest.MonkeyPatch, logs: list[str]) -> None:
    """A request without identity headers has nothing to ignore, so nothing is logged."""
    env.setenv("CHORUS_EDGE_SECRET", SECRET)
    resp = _client().get("/whoami")
    assert resp.status_code == 401
    assert not _ignored_warnings(logs)


_VERIFIED = {**IDENTITY, "X-Edge-Secret": SECRET}
_FORGED = {**IDENTITY, "X-Edge-Secret": WRONG}


@pytest.mark.parametrize(
    ("secret", "default", "headers", "expected"),
    [
        ("", "", {}, None),
        ("", "", IDENTITY, USER),
        ("", DEFAULT, {}, DEFAULT),
        ("", DEFAULT, IDENTITY, USER),
        (SECRET, "", {}, None),
        (SECRET, "", _VERIFIED, USER),
        (SECRET, "", _FORGED, None),
        (SECRET, DEFAULT, {}, None),
        (SECRET, DEFAULT, _VERIFIED, USER),
        (SECRET, DEFAULT, _FORGED, None),
    ],
    ids=[
        "no-secret-no-default-anonymous",
        "no-secret-no-default-header",
        "no-secret-default-anonymous",
        "no-secret-default-header-wins",
        "secret-no-default-anonymous",
        "secret-no-default-verified",
        "secret-no-default-forged",
        "secret-default-anonymous",
        "secret-default-verified",
        "secret-default-forged",
    ],
)
def test_identity_matrix(
    env: pytest.MonkeyPatch, secret: str, default: str, headers: dict[str, str], expected: str | None
) -> None:
    """Every edge-secret x default-identity combination: who the request is, or 401.

    The default identity only ever applies without a secret; with one, only a
    verified header identifies the caller.
    """
    env.setenv("CHORUS_EDGE_SECRET", secret)
    env.setenv("CHORUS_DEFAULT_IDENTITY", default)
    resp = _client().get("/whoami", headers=headers)
    if expected is None:
        assert resp.status_code == 401
    else:
        assert resp.status_code == 200
        assert resp.json()["username"] == expected


@pytest.mark.parametrize(
    ("secret", "default", "trust_warnings", "fallback_warnings"),
    [("", "", 1, 0), ("", DEFAULT, 0, 0), (SECRET, "", 0, 0), (SECRET, DEFAULT, 0, 1)],
    ids=["neither", "default-only", "secret-only", "both"],
)
def test_startup_warning_matrix(
    env: pytest.MonkeyPatch,
    logs: list[str],
    secret: str,
    default: str,
    trust_warnings: int,
    fallback_warnings: int,
) -> None:
    """One warning when headers are trusted from anyone, one when the fallback is ignored, else none."""
    from chorus.api.auth.principal import check_principal_config

    env.setenv("CHORUS_EDGE_SECRET", secret)
    env.setenv("CHORUS_DEFAULT_IDENTITY", default)
    check_principal_config()
    assert len(_trust_warnings(logs)) == trust_warnings
    assert len(_fallback_warnings(logs)) == fallback_warnings


# --- config load and startup -------------------------------------------------


def test_secret_loads_trimmed_and_stays_out_of_repr(env: pytest.MonkeyPatch) -> None:
    """Surrounding whitespace is dropped; ``repr`` of the config never shows the secret."""
    from chorus.utils.env_cfg import load_principal_env

    env.setenv("CHORUS_EDGE_SECRET", f"  {SECRET}\t")
    cfg = load_principal_env()
    assert cfg.edge_secret == SECRET
    assert SECRET not in repr(cfg)


def test_secret_length_boundary(env: pytest.MonkeyPatch) -> None:
    """32 characters load; 31 do not."""
    from chorus.utils.env_cfg import load_principal_env

    env.setenv("CHORUS_EDGE_SECRET", "s" * 32)
    assert load_principal_env().edge_secret == "s" * 32
    env.setenv("CHORUS_EDGE_SECRET", "s" * 31)
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        load_principal_env()


def test_short_secret_error_does_not_echo_the_value(env: pytest.MonkeyPatch) -> None:
    """The config error names the variable and the rule, never the value."""
    from chorus.utils.env_cfg import load_principal_env

    env.setenv("CHORUS_EDGE_SECRET", SHORT)
    with pytest.raises(RuntimeError, match="CHORUS_EDGE_SECRET must be at least 32 characters") as exc:
        load_principal_env()
    assert SHORT not in str(exc.value)


def test_startup_warns_once_when_headers_are_trusted_from_anyone(env: pytest.MonkeyPatch, logs: list[str]) -> None:
    """No secret and no default identity (production shape) logs exactly one warning."""
    from chorus.api.auth.principal import check_principal_config

    check_principal_config()
    warnings = _trust_warnings(logs)
    assert len(warnings) == 1
    assert "WARNING" in warnings[0]
    assert (
        "identity headers are trusted from any caller that can reach the backend; "
        "set CHORUS_EDGE_SECRET (see edge-plane docs/identity-contract.md)"
    ) in warnings[0]


@pytest.mark.parametrize(("key", "value"), [("CHORUS_EDGE_SECRET", SECRET), ("CHORUS_DEFAULT_IDENTITY", DEFAULT)])
def test_startup_is_quiet_with_a_secret_or_a_default_identity(
    env: pytest.MonkeyPatch, logs: list[str], key: str, value: str
) -> None:
    """A configured secret, or the dev default identity, needs no startup warning."""
    from chorus.api.auth.principal import check_principal_config

    env.setenv(key, value)
    check_principal_config()
    assert not _trust_warnings(logs)
    assert not _fallback_warnings(logs)


def test_startup_warns_once_when_the_default_identity_is_ignored(env: pytest.MonkeyPatch, logs: list[str]) -> None:
    """With both the secret and a default identity set, one warning says the fallback is ignored."""
    from chorus.api.auth.principal import check_principal_config

    env.setenv("CHORUS_EDGE_SECRET", SECRET)
    env.setenv("CHORUS_DEFAULT_IDENTITY", DEFAULT)
    check_principal_config()
    warnings = _fallback_warnings(logs)
    assert len(warnings) == 1
    assert "WARNING" in warnings[0]
    assert not _trust_warnings(logs)
    logged = "".join(logs)
    assert SECRET not in logged
    assert DEFAULT not in logged


def test_lifespan_fails_fast_on_a_short_secret(env: pytest.MonkeyPatch) -> None:
    """The app refuses to start, before it opens the Neo4j driver."""
    from chorus.api import main

    def _no_driver() -> None:
        raise AssertionError("Neo4j driver opened before the principal config was checked")

    env.setattr(main, "init_logger", lambda: None)
    env.setattr(main, "get_driver", _no_driver)
    env.setenv("CHORUS_EDGE_SECRET", SHORT)

    async def _start() -> None:
        async with main.lifespan(main.app):
            pass

    with pytest.raises(RuntimeError, match="CHORUS_EDGE_SECRET") as exc:
        asyncio.run(_start())
    assert SHORT not in str(exc.value)
