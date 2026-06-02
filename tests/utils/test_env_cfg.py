"""Env-loader tests focused on ingestion and NER-client configuration."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_load_ingestion_env_defaults_to_chorus_home_ingest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With no override, ``source_dir`` is ``<CHORUS_HOME>/ingest``.

    Pins the default drop point so a fresh install has a predictable
    location for the operator to push table dumps.
    """
    monkeypatch.setenv("CHORUS_HOME", str(tmp_path))
    monkeypatch.delenv("INGESTION_SOURCE_DIR", raising=False)
    import importlib

    import chorus.utils.env_cfg as env_cfg

    importlib.reload(env_cfg)

    cfg = env_cfg.load_ingestion_env()
    assert cfg.source_dir == tmp_path / "ingest"


def test_load_ingestion_env_honors_explicit_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``INGESTION_SOURCE_DIR`` wins over the chorus-home default."""
    explicit = tmp_path / "elsewhere"
    monkeypatch.setenv("CHORUS_HOME", str(tmp_path))
    monkeypatch.setenv("INGESTION_SOURCE_DIR", str(explicit))
    import importlib

    import chorus.utils.env_cfg as env_cfg

    importlib.reload(env_cfg)

    cfg = env_cfg.load_ingestion_env()
    assert cfg.source_dir == explicit


def test_load_ingestion_env_expands_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A ``~``-prefixed override is expanded against ``$HOME``.

    Mirrors :func:`load_path_env`'s handling of ``CHORUS_HOME``.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CHORUS_HOME", str(tmp_path))
    monkeypatch.setenv("INGESTION_SOURCE_DIR", "~/drops")
    import importlib

    import chorus.utils.env_cfg as env_cfg

    importlib.reload(env_cfg)

    cfg = env_cfg.load_ingestion_env()
    assert cfg.source_dir == tmp_path / "drops"


def test_load_ner_client_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no NER_* env vars set, all defaults apply.

    Defaults point at the full vllm-service stack (LiteLLM router) with
    NER enabled and the canonical GLiNER model version stamped on each
    edge; the ner-only deployment shape requires an explicit override.

    The loader reads ``os.environ`` on every call, so no module reload
    is required after ``monkeypatch.delenv`` — and reloading would
    re-trigger ``load_dotenv`` and restore values from the local
    ``.env``, which would defeat the test.
    """
    for key in (
        "NER_ENABLED",
        "NER_API_BASE",
        "NER_API_KEY",
        "NER_THRESHOLD",
        "NER_TIMEOUT",
        "NER_MODEL_VERSION",
    ):
        monkeypatch.delenv(key, raising=False)

    from chorus.utils.env_cfg import load_ner_client_env

    cfg = load_ner_client_env()
    assert cfg.enabled is True
    assert cfg.api_base == "http://vllm-router:4000"
    assert cfg.api_key is None
    assert cfg.threshold == 0.3
    assert cfg.timeout == 30.0
    assert cfg.model_version == "gliner-community/gliner_large-v2.5"


def test_load_ner_client_env_honors_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each NER_* env var maps to the corresponding config field."""
    monkeypatch.setenv("NER_ENABLED", "false")
    monkeypatch.setenv("NER_API_BASE", "http://gliner-ner:8000")
    monkeypatch.setenv("NER_API_KEY", "sk-secret")
    monkeypatch.setenv("NER_THRESHOLD", "0.55")
    monkeypatch.setenv("NER_TIMEOUT", "12")
    monkeypatch.setenv("NER_MODEL_VERSION", "gliner-custom-v1")

    from chorus.utils.env_cfg import load_ner_client_env

    cfg = load_ner_client_env()
    assert cfg.enabled is False
    assert cfg.api_base == "http://gliner-ner:8000"
    assert cfg.api_key == "sk-secret"
    assert cfg.threshold == 0.55
    assert cfg.timeout == 12.0
    assert cfg.model_version == "gliner-custom-v1"


def test_load_ner_client_env_strips_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trailing slashes in ``NER_API_BASE`` are removed at load time.

    The client appends ``/gliner`` itself, so a trailing slash in the
    env value would otherwise produce ``//gliner``.
    """
    monkeypatch.setenv("NER_API_BASE", "http://gliner-ner:8000/")

    from chorus.utils.env_cfg import load_ner_client_env

    assert load_ner_client_env().api_base == "http://gliner-ner:8000"


def test_load_ner_client_env_treats_blank_api_key_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty or whitespace ``NER_API_KEY`` resolves to ``None``.

    Avoids accidentally sending ``Authorization: Bearer `` when an
    operator commits an empty ``NER_API_KEY=`` line.
    """
    monkeypatch.setenv("NER_API_KEY", "   ")

    from chorus.utils.env_cfg import load_ner_client_env

    assert load_ner_client_env().api_key is None


def test_load_resolution_env_vector_k_default_and_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``RES_VECTOR_K`` defaults to 5 and is overridable."""
    import importlib

    import chorus.utils.env_cfg as env_cfg

    monkeypatch.delenv("RES_VECTOR_K", raising=False)
    importlib.reload(env_cfg)
    assert env_cfg.load_resolution_env().vector_k == 5

    monkeypatch.setenv("RES_VECTOR_K", "8")
    importlib.reload(env_cfg)
    assert env_cfg.load_resolution_env().vector_k == 8


def test_load_agent_env_tool_message_limits_default_and_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tool-message compaction caps default to 8/280 and are overridable."""
    import importlib

    import chorus.utils.env_cfg as env_cfg

    monkeypatch.delenv("AGENT_TOOL_MESSAGE_MAX_ITEMS", raising=False)
    monkeypatch.delenv("AGENT_TOOL_MESSAGE_MAX_CHARS", raising=False)
    importlib.reload(env_cfg)
    cfg = env_cfg.load_agent_env()
    assert cfg.tool_message_max_items == 8
    assert cfg.tool_message_max_chars == 280

    monkeypatch.setenv("AGENT_TOOL_MESSAGE_MAX_ITEMS", "3")
    monkeypatch.setenv("AGENT_TOOL_MESSAGE_MAX_CHARS", "120")
    importlib.reload(env_cfg)
    cfg = env_cfg.load_agent_env()
    assert cfg.tool_message_max_items == 3
    assert cfg.tool_message_max_chars == 120


def test_load_retention_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no RETENTION_* env vars set, retention is enabled at 365 days.

    The loader reads ``os.environ`` on every call, so ``delenv`` alone —
    without a module reload, which would re-trigger ``load_dotenv`` and
    restore values from the local ``.env`` — exercises the defaults.
    """
    monkeypatch.delenv("RETENTION_ENABLED", raising=False)
    monkeypatch.delenv("RETENTION_DAYS_DEFAULT", raising=False)

    from chorus.utils.env_cfg import load_retention_env

    cfg = load_retention_env()
    assert cfg.enabled is True
    assert cfg.default_days == 365


def test_load_retention_env_disable_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """``RETENTION_ENABLED=false`` fully disables retention."""
    monkeypatch.setenv("RETENTION_ENABLED", "false")

    from chorus.utils.env_cfg import load_retention_env

    assert load_retention_env().enabled is False


def test_retention_config_until_adds_window_when_enabled() -> None:
    """``until`` adds the retention window to the anchor timestamp."""
    from datetime import UTC, datetime

    from chorus.utils.env_cfg import RetentionConfig

    basis = datetime(2026, 1, 1, tzinfo=UTC)

    assert RetentionConfig(default_days=30).until(basis) == datetime(2026, 1, 31, tzinfo=UTC)


def test_retention_config_until_none_when_disabled() -> None:
    """When retention is disabled, ``until`` returns ``None`` for any anchor."""
    from datetime import UTC, datetime

    from chorus.utils.env_cfg import RetentionConfig

    cfg = RetentionConfig(default_days=30, enabled=False)

    assert cfg.until(datetime(2026, 1, 1, tzinfo=UTC)) is None


def test_retention_config_until_none_without_anchor() -> None:
    """``until`` returns ``None`` when no anchor timestamp is available."""
    from chorus.utils.env_cfg import RetentionConfig

    assert RetentionConfig(default_days=30).until(None) is None
