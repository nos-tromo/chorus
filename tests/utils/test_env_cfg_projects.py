"""Env-loader tests for the per-project configuration (ADR 0017)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_projects_unset_yields_implicit_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """With ``CHORUS_PROJECTS`` unset, one implicit project ``default`` exists.

    This is the single-project backward-compat mode: dev and existing
    deploys keep working with the legacy flat env vars.
    """
    monkeypatch.delenv("CHORUS_PROJECTS", raising=False)

    from chorus.utils.env_cfg import load_projects_env

    cfg = load_projects_env()
    assert cfg.names == ("default",)
    assert cfg.explicit is False


def test_projects_parses_and_preserves_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """``CHORUS_PROJECTS`` is comma-split, trimmed, order-preserving."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha, beta ,gamma-two")

    from chorus.utils.env_cfg import load_projects_env

    cfg = load_projects_env()
    assert cfg.names == ("alpha", "beta", "gamma-two")
    assert cfg.explicit is True


def test_projects_drops_empty_segments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stray commas (``alpha,,beta,``) do not produce empty project names."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,,beta,")

    from chorus.utils.env_cfg import load_projects_env

    assert load_projects_env().names == ("alpha", "beta")


@pytest.mark.parametrize("bad", ["Alpha", "1alpha", "al pha", "al_pha", "-alpha", "a" * 33])
def test_projects_rejects_invalid_name(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    """Names outside ``^[a-z][a-z0-9-]{0,31}$`` fail fast at load time.

    The name feeds env-var suffixes, filesystem paths, and HTTP headers,
    so the grammar is deliberately narrow.
    """
    monkeypatch.setenv("CHORUS_PROJECTS", f"good,{bad}")

    from chorus.utils.env_cfg import load_projects_env

    with pytest.raises(RuntimeError, match="CHORUS_PROJECTS"):
        load_projects_env()


def test_projects_rejects_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate names are a configuration error, not silently deduped."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta,alpha")

    from chorus.utils.env_cfg import load_projects_env

    with pytest.raises(RuntimeError, match="duplicate"):
        load_projects_env()


def test_neo4j_env_default_project_uses_legacy_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """``load_neo4j_env()`` and ``load_neo4j_env("default")`` read the flat vars."""
    monkeypatch.delenv("CHORUS_PROJECTS", raising=False)
    monkeypatch.setenv("NEO4J_URI", "bolt://legacy:7687")
    monkeypatch.setenv("NEO4J_USER", "u")
    monkeypatch.setenv("NEO4J_PASSWORD", "p")

    from chorus.utils.env_cfg import load_neo4j_env

    for cfg in (load_neo4j_env(), load_neo4j_env("default")):
        assert cfg.uri == "bolt://legacy:7687"
        assert cfg.user == "u"
        assert cfg.password == "p"


def test_neo4j_env_per_project_uri_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-default project without ``NEO4J_URI_<SUFFIX>`` fails fast.

    Falling back to the shared URI would silently point two projects at
    the same instance — the exact cross-contamination ADR 0017 forbids.
    """
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha")
    monkeypatch.delenv("NEO4J_URI_ALPHA", raising=False)

    from chorus.utils.env_cfg import load_neo4j_env

    with pytest.raises(RuntimeError, match="NEO4J_URI_ALPHA"):
        load_neo4j_env("alpha")


def test_neo4j_env_per_project_credentials_fall_back_to_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    """User/password/database inherit the shared vars unless overridden."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha")
    monkeypatch.setenv("NEO4J_URI_ALPHA", "bolt://neo4j-alpha:7687")
    monkeypatch.setenv("NEO4J_USER", "shared-user")
    monkeypatch.setenv("NEO4J_PASSWORD", "shared-pass")
    monkeypatch.delenv("NEO4J_USER_ALPHA", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD_ALPHA", raising=False)

    from chorus.utils.env_cfg import load_neo4j_env

    cfg = load_neo4j_env("alpha")
    assert cfg.uri == "bolt://neo4j-alpha:7687"
    assert cfg.user == "shared-user"
    assert cfg.password == "shared-pass"


def test_neo4j_env_per_project_overrides_win(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-project credential vars beat the shared ones; hyphens map to underscores."""
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha-two")
    monkeypatch.setenv("NEO4J_URI_ALPHA_TWO", "bolt://neo4j-alpha-two:7687")
    monkeypatch.setenv("NEO4J_USER_ALPHA_TWO", "own-user")
    monkeypatch.setenv("NEO4J_USER", "shared-user")

    from chorus.utils.env_cfg import load_neo4j_env

    cfg = load_neo4j_env("alpha-two")
    assert cfg.uri == "bolt://neo4j-alpha-two:7687"
    assert cfg.user == "own-user"


def test_project_paths_layout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Per-project state lives under ``$CHORUS_HOME/projects/<name>/``."""
    monkeypatch.setenv("CHORUS_HOME", str(tmp_path))
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha")
    for key in ("AUDIT_DB_PATH", "RAW_STORE_PATH", "INGESTION_SOURCE_DIR"):
        monkeypatch.delenv(key, raising=False)

    from chorus.utils.env_cfg import load_project_paths_env

    paths = load_project_paths_env("alpha")
    root = tmp_path / "projects" / "alpha"
    assert paths.root == root
    assert paths.audit_db == root / "audit.sqlite"
    assert paths.raw_store == root / "raw.sqlite"
    assert paths.uploads == root / "uploads"
    assert paths.ingest_source == root / "ingest"


def test_project_paths_compat_honors_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """In compat mode the legacy path overrides still win for ``default``."""
    monkeypatch.setenv("CHORUS_HOME", str(tmp_path))
    monkeypatch.delenv("CHORUS_PROJECTS", raising=False)
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "custom-audit.sqlite"))
    monkeypatch.setenv("RAW_STORE_PATH", str(tmp_path / "custom-raw.sqlite"))
    monkeypatch.setenv("INGESTION_SOURCE_DIR", str(tmp_path / "drops"))

    from chorus.utils.env_cfg import load_project_paths_env

    paths = load_project_paths_env("default")
    assert paths.audit_db == tmp_path / "custom-audit.sqlite"
    assert paths.raw_store == tmp_path / "custom-raw.sqlite"
    assert paths.ingest_source == tmp_path / "drops"
    assert paths.uploads == tmp_path / "projects" / "default" / "uploads"


def test_project_paths_explicit_ignores_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """In explicit multi-project mode the single-path overrides are ignored.

    A single override path cannot be partitioned per project; honoring it
    would merge state across projects.
    """
    monkeypatch.setenv("CHORUS_HOME", str(tmp_path))
    monkeypatch.setenv("CHORUS_PROJECTS", "alpha,beta")
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "custom-audit.sqlite"))
    monkeypatch.setenv("RAW_STORE_PATH", str(tmp_path / "custom-raw.sqlite"))
    monkeypatch.setenv("INGESTION_SOURCE_DIR", str(tmp_path / "drops"))

    from chorus.utils.env_cfg import load_project_paths_env

    paths = load_project_paths_env("beta")
    root = tmp_path / "projects" / "beta"
    assert paths.audit_db == root / "audit.sqlite"
    assert paths.raw_store == root / "raw.sqlite"
    assert paths.ingest_source == root / "ingest"
