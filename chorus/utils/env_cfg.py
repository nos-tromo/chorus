"""Centralized environment loading.

Every config the app reads at runtime is declared here as a frozen dataclass
with a `load_*_env()` function. Modules import the loader, not `os.environ`.
This keeps the env surface auditable and gives mypy a typed handle on every
knob.

Provider defaults follow the pattern from the sister project docint:
provider-aware fallbacks so a bare `OPENAI_API_KEY=x` env is enough to
boot against any of the three supported backends.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(key: str, default: str | None = None) -> str | None:
    """Read an environment variable, falling back to ``default``.

    Treats both unset variables and empty strings as missing so that an
    accidental ``KEY=`` in a ``.env`` file does not silently override the
    intended default.

    Args:
        key: Environment variable name to look up.
        default: Value to return when the variable is unset or empty.

    Returns:
        The variable's value, or ``default`` if unset/empty.
    """
    v = os.environ.get(key)
    return v if v not in (None, "") else default


def _env_required(key: str) -> str:
    """Read a required environment variable or raise.

    Args:
        key: Environment variable name to look up.

    Returns:
        The variable's value.

    Raises:
        RuntimeError: If the variable is unset or empty.
    """
    v = _env(key)
    if v is None:
        raise RuntimeError(f"Required env var not set: {key}")
    return v


def _env_int(key: str, default: int) -> int:
    """Read an environment variable and parse it as ``int``.

    Args:
        key: Environment variable name to look up.
        default: Value to return when the variable is unset or empty.

    Returns:
        The parsed integer, or ``default`` if the variable is unset.

    Raises:
        ValueError: If the variable is set but not a valid integer literal.
    """
    raw = _env(key)
    return int(raw) if raw is not None else default


def _env_float(key: str, default: float) -> float:
    """Read an environment variable and parse it as ``float``.

    Args:
        key: Environment variable name to look up.
        default: Value to return when the variable is unset or empty.

    Returns:
        The parsed float, or ``default`` if the variable is unset.

    Raises:
        ValueError: If the variable is set but not a valid float literal.
    """
    raw = _env(key)
    return float(raw) if raw is not None else default


def _env_bool(key: str, default: bool) -> bool:
    """Read an environment variable and parse it as ``bool``.

    Truthy literals are ``"1"``, ``"true"``, ``"yes"``, ``"on"`` (case
    insensitive, surrounding whitespace ignored). Anything else is treated
    as ``False``.

    Args:
        key: Environment variable name to look up.
        default: Value to return when the variable is unset or empty.

    Returns:
        The parsed boolean, or ``default`` if the variable is unset.
    """
    raw = _env(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


_PROVIDER_API_BASE = {
    "vllm": "http://vllm-router:4000/v1",
    "ollama": "http://localhost:11434/v1",
    "openai": "http://vllm-router:4000/v1",
}

_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "vllm": {
        "TEXT_MODEL": "Qwen/Qwen3.5-2B",
        "embed_model": "BAAI/bge-m3",
        "rerank_model": "BAAI/bge-reranker-v2-m3",
        "ner_model": "gliner-community/gliner_large-v2.5",
    },
    "ollama": {
        "TEXT_MODEL": "gpt-oss:20b",
        "embed_model": "bge-m3",
        "rerank_model": "bge-reranker-v2-m3",
        "ner_model": "gliner",
    },
    "openai": {
        "TEXT_MODEL": "gpt-4o",
        "embed_model": "text-embedding-3-small",
        "rerank_model": "bge-reranker-v2-m3",
        "ner_model": "gliner",
    },
}


@dataclass(frozen=True)
class InferenceConfig:
    """Inference-provider configuration.

    Captures everything chorus needs to talk to vllm-service (or another
    OpenAI-compatible router). The provider abstraction is env-driven —
    swapping ``provider`` is the only knob callers should care about.

    Attributes:
        provider: One of ``"vllm"``, ``"ollama"``, ``"openai"``.
        api_base: OpenAI-compatible base URL (e.g. ``http://vllm-router:4000/v1``).
        api_key: Bearer token sent on each request. ``"EMPTY"`` when no
            authentication is required (typical for in-network vllm).
        TEXT_MODEL: Model id used for chat/completion calls.
        embed_model: Model id used for embedding calls.
        rerank_model: Model id used for rerank calls.
        ner_model: Model id used for entity-extraction calls.
        embed_dim: Vector dimensionality of the embedding model. Used to
            size Neo4j vector indexes at migration time.
        timeout_s: Per-request timeout in seconds.
        max_retries: Maximum automatic retries the SDK should attempt.
    """

    provider: str
    api_base: str
    api_key: str
    TEXT_MODEL: str
    embed_model: str
    rerank_model: str
    ner_model: str
    embed_dim: int
    timeout_s: float
    max_retries: int


@dataclass(frozen=True)
class Neo4jConfig:
    """Neo4j connection parameters.

    Attributes:
        uri: Bolt URI of the Neo4j database (e.g. ``bolt://neo4j:7687``).
        user: Database username.
        password: Database password.
        database: Logical database name (Neo4j 5+ supports multi-database).
    """

    uri: str
    user: str
    password: str
    database: str


@dataclass(frozen=True)
class PrincipalConfig:
    """Trusted-header principal configuration.

    Chorus reads the authenticated identity from an HTTP header set by a
    trusted upstream proxy (Nginx/OIDC). This is the seam where real OIDC
    will plug in later — see ``api/auth/principal.py``.

    Attributes:
        header_name: Header to read the identity from (default
            ``X-Auth-User``).
        default_identity: Identity to use when the header is absent.
            Intended for local development; ``None`` in production forces
            the header to be present.
    """

    header_name: str
    default_identity: str | None


@dataclass(frozen=True)
class AuditConfig:
    """§76 BDSG audit log configuration.

    Attributes:
        db_path: Filesystem path to the audit SQLite database.
        retention_days: How long audit records are retained before the
            nightly sweeper deletes them. Defaults to two years.
    """

    db_path: Path
    retention_days: int


@dataclass(frozen=True)
class RetentionConfig:
    """Per-post retention configuration.

    Attributes:
        default_days: Default retention window applied to ingested posts
            when the upstream row does not specify one.
    """

    default_days: int


@dataclass(frozen=True)
class ResolutionConfig:
    """Entity-resolution thresholds and toggles.

    Attributes:
        embed_cluster_threshold: Cosine similarity above which two unresolved
            spans are clustered as the same entity.
        llm_tiebreak_enabled: Whether to call the LLM for ambiguous merges
            after embedding clustering.
        case_normalize: Whether to lowercase surface forms before comparing
            them in the alias table.
    """

    embed_cluster_threshold: float
    llm_tiebreak_enabled: bool
    case_normalize: bool


@dataclass(frozen=True)
class PathConfig:
    """Filesystem paths used by the running app.

    All paths are absolute. ``chorus_home`` is the writable root; the
    remaining paths default to subdirectories of it.

    Attributes:
        chorus_home: Writable root directory (defaults to ``<repo>/var``).
        logs: Path to the rotating operational log file.
        queries: Directory containing Cypher query templates.
        migrations: Directory containing ordered Cypher migration files.
        raw_store: Path to the raw-store SQLite database.
    """

    chorus_home: Path
    logs: Path
    queries: Path
    migrations: Path
    raw_store: Path


def load_inference_env() -> InferenceConfig:
    """Load and validate inference configuration from the environment.

    Resolves ``INFERENCE_PROVIDER`` first, then fills in per-provider
    defaults for any model id not explicitly set. The api base falls back
    to the provider's standard in-network address.

    Returns:
        A populated :class:`InferenceConfig`.

    Raises:
        RuntimeError: If ``INFERENCE_PROVIDER`` is set to an unsupported
            value.
    """
    provider = (_env("INFERENCE_PROVIDER", "vllm") or "vllm").lower()
    if provider not in _PROVIDER_DEFAULTS:
        raise RuntimeError(
            f"Unknown INFERENCE_PROVIDER={provider!r}; "
            f"expected one of {sorted(_PROVIDER_DEFAULTS)}"
        )
    defaults = _PROVIDER_DEFAULTS[provider]
    return InferenceConfig(
        provider=provider,
        api_base=_env("OPENAI_API_BASE", _PROVIDER_API_BASE[provider]) or "",
        api_key=_env("OPENAI_API_KEY", "EMPTY") or "EMPTY",
        TEXT_MODEL=_env("TEXT_MODEL", defaults["TEXT_MODEL"]) or defaults["TEXT_MODEL"],
        embed_model=_env("EMBED_MODEL", defaults["embed_model"])
        or defaults["embed_model"],
        rerank_model=_env("RERANK_MODEL", defaults["rerank_model"])
        or defaults["rerank_model"],
        ner_model=_env("NER_MODEL", defaults["ner_model"]) or defaults["ner_model"],
        embed_dim=_env_int("EMBED_DIM", 1024),
        timeout_s=_env_float("INFERENCE_TIMEOUT_S", 60.0),
        max_retries=_env_int("INFERENCE_MAX_RETRIES", 2),
    )


def load_neo4j_env() -> Neo4jConfig:
    """Load Neo4j connection parameters from the environment.

    Returns:
        A populated :class:`Neo4jConfig`. Falls back to the in-cluster
        default (``bolt://localhost:7687``, ``neo4j``/``neo4j``) suitable
        for development.
    """
    return Neo4jConfig(
        uri=_env("NEO4J_URI", "bolt://localhost:7687") or "bolt://localhost:7687",
        user=_env("NEO4J_USER", "neo4j") or "neo4j",
        password=_env("NEO4J_PASSWORD", "neo4j") or "neo4j",
        database=_env("NEO4J_DATABASE", "neo4j") or "neo4j",
    )


def load_principal_env() -> PrincipalConfig:
    """Load trusted-header principal configuration from the environment.

    Returns:
        A populated :class:`PrincipalConfig`. ``default_identity`` is
        ``None`` unless explicitly set, which forces the upstream header
        to be present in production.
    """
    return PrincipalConfig(
        header_name=_env("CHORUS_AUTH_HEADER", "X-Auth-User") or "X-Auth-User",
        default_identity=_env("CHORUS_DEFAULT_IDENTITY"),
    )


def load_audit_env() -> AuditConfig:
    """Load §76 BDSG audit-log configuration from the environment.

    The audit database path defaults to ``<chorus_home>/audit.sqlite``.

    Returns:
        A populated :class:`AuditConfig`.
    """
    paths = load_path_env()
    db_path_raw = _env("AUDIT_DB_PATH")
    db_path = Path(db_path_raw) if db_path_raw else paths.chorus_home / "audit.sqlite"
    return AuditConfig(
        db_path=db_path,
        retention_days=_env_int("AUDIT_RETENTION_DAYS", 365 * 2),
    )


def load_retention_env() -> RetentionConfig:
    """Load per-post retention configuration from the environment.

    Returns:
        A populated :class:`RetentionConfig`. Default retention is one
        year, applied to posts whose upstream row carries no explicit
        retention window.
    """
    return RetentionConfig(
        default_days=_env_int("RETENTION_DAYS_DEFAULT", 365),
    )


def load_resolution_env() -> ResolutionConfig:
    """Load entity-resolution thresholds and toggles from the environment.

    Returns:
        A populated :class:`ResolutionConfig`.
    """
    return ResolutionConfig(
        embed_cluster_threshold=_env_float("RES_EMBED_THRESHOLD", 0.86),
        llm_tiebreak_enabled=_env_bool("RES_LLM_TIEBREAK", True),
        case_normalize=_env_bool("RES_CASE_NORMALIZE", True),
    )


def load_path_env() -> PathConfig:
    """Resolve filesystem paths used by the running app.

    ``CHORUS_HOME`` controls the writable root; ``LOG_PATH`` and
    ``RAW_STORE_PATH`` may override individual children. The ``queries``
    and ``migrations`` paths are package-relative and never overridable.

    Returns:
        A populated :class:`PathConfig` with every field as an absolute path.
    """
    home_raw = _env("CHORUS_HOME")
    home = Path(home_raw).expanduser() if home_raw else _REPO_ROOT / "var"
    logs_raw = _env("LOG_PATH")
    logs = Path(logs_raw) if logs_raw else home / "logs" / "chorus.log"
    raw_store_raw = _env("RAW_STORE_PATH")
    raw_store = Path(raw_store_raw) if raw_store_raw else home / "raw.sqlite"
    pkg_root = Path(__file__).resolve().parent.parent
    return PathConfig(
        chorus_home=home,
        logs=logs,
        queries=pkg_root / "queries",
        migrations=pkg_root / "migrations",
        raw_store=raw_store,
    )
