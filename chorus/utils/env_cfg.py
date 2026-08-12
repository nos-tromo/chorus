"""Centralized environment loading.

Every config the app reads at runtime is declared here as a frozen dataclass
with a `load_*_env()` function. Modules import the loader, not `os.environ`.
This keeps the env surface auditable and gives pyrefly a typed handle on every
knob.

Provider defaults follow the pattern from the sister project docint:
provider-aware fallbacks so a bare `OPENAI_API_KEY=x` env is enough to
boot against any of the three supported backends.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

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
    },
    "ollama": {
        "TEXT_MODEL": "gpt-oss:20b",
        "embed_model": "bge-m3",
        "rerank_model": "bge-reranker-v2-m3",
    },
    "openai": {
        "TEXT_MODEL": "gpt-4o",
        "embed_model": "text-embedding-3-small",
        "rerank_model": "bge-reranker-v2-m3",
    },
}


@dataclass(frozen=True)
class InferenceConfig:
    """Inference-provider configuration.

    Captures everything chorus needs to talk to vllm-service (or another
    OpenAI-compatible router). The provider abstraction is env-driven —
    swapping ``provider`` is the only knob callers should care about.

    NER is decoupled from this config — it speaks the GLiNER-native
    ``/gliner`` HTTP shape rather than the OpenAI protocol and so does
    not flow through this provider. See :class:`NERClientConfig`.

    Attributes:
        provider: One of ``"vllm"``, ``"ollama"``, ``"openai"``.
        api_base: OpenAI-compatible base URL (e.g. ``http://vllm-router:4000/v1``).
        api_key: Bearer token sent on each request. ``"EMPTY"`` when no
            authentication is required (typical for in-network vllm).
        TEXT_MODEL: Model id used for chat/completion calls.
        embed_model: Model id used for embedding calls.
        rerank_model: Model id used for rerank calls.
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
    embed_dim: int
    timeout_s: float
    max_retries: int


@dataclass(frozen=True)
class AgentConfig:
    """Natural-language agent loop configuration.

    Attributes:
        max_tool_iterations: Maximum model-tool rounds per turn before the
            loop gives up and returns a truncated result.
        model: Chat model id override for the agent, or ``None`` to use the
            inference provider's ``TEXT_MODEL``.
        tool_message_max_items: Maximum list items kept per tool result fed
            back to the model; longer lists are truncated to fit context.
        tool_message_max_chars: Maximum characters kept per string in a tool
            result fed back to the model; longer strings are truncated.
    """

    max_tool_iterations: int
    model: str | None
    tool_message_max_items: int
    tool_message_max_chars: int


SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "de")


@dataclass(frozen=True)
class LanguageConfig:
    """Response-language configuration.

    Selects the agent system-prompt variant and the UI string catalog.
    There is no on-disk prompt tree in chorus (prompts are in-code), so
    this only drives an in-process selection.

    Attributes:
        code: Active language code, ``"en"`` or ``"de"``.
    """

    code: Literal["en", "de"]


@dataclass(frozen=True)
class NERClientConfig:
    """Configuration for the remote GLiNER NER service HTTP client.

    NER lives on vllm-service and is reached over plain HTTP at
    ``{api_base}/gliner`` with the GLiNER-native body shape
    ``{text, labels, threshold}``. Two operator-side deployment shapes
    are supported by the same client:

    - Full vllm-service stack: ``api_base=http://vllm-router:4000`` with
      Bearer auth (``NER_API_KEY=$OPENAI_API_KEY``).
    - ner-only stack (e.g. Mac, CPU-only Linux running Ollama for chat
      and embed): ``api_base=http://gliner-ner:8000`` with no auth.

    Attributes:
        enabled: When ``False``, the orchestrator skips the extraction
            step entirely. The HTTP client itself does not consult this
            field — it is a stage-level gate so a dev environment
            without a reachable GLiNER service does not log a warning
            per post.
        api_base: Base URL of the NER service; the client appends
            ``/gliner`` itself. Trailing slashes are stripped on load.
        api_key: Bearer token sent as ``Authorization: Bearer ...`` when
            set; omitted entirely when ``None``. Not auto-coupled to
            ``OPENAI_API_KEY`` — operators set it explicitly to avoid
            hidden coupling on multi-provider hosts.
        threshold: GLiNER confidence cutoff passed per request, in [0, 1].
        timeout: Per-request HTTP timeout in seconds.
        model_version: Identifier of the GLiNER model the upstream is
            serving, stamped on each ``:MENTIONS`` edge as provenance.
            Operator-set to match what vllm-service actually loaded —
            chorus does not introspect the service for this; it just
            records whatever it was told. The ``/gliner`` endpoint
            itself takes no model parameter.
    """

    enabled: bool
    api_base: str
    api_key: str | None
    threshold: float
    timeout: float
    model_version: str


_PROJECT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


@dataclass(frozen=True)
class ProjectsConfig:
    """The set of configured projects (ADR 0017).

    Each project maps to its own Neo4j instance and its own state
    subtree under ``$CHORUS_HOME/projects/<name>``. When
    ``CHORUS_PROJECTS`` is unset, chorus runs in single-project
    backward-compat mode with one implicit project ``"default"`` that
    uses the legacy flat env vars (``NEO4J_URI`` etc.).

    Attributes:
        names: Configured project names, order preserved from
            ``CHORUS_PROJECTS``; ``("default",)`` in compat mode.
        explicit: ``False`` when ``CHORUS_PROJECTS`` is unset/empty
            (compat mode), ``True`` when projects were configured.
    """

    names: tuple[str, ...]
    explicit: bool


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
        display_name_header: Header carrying the gateway's decorative
            display name (default ``X-Auth-Name``, Authelia's
            ``displayname``). Never an identity key — UI display only.
        projects_header: Header carrying the gateway-asserted,
            comma-separated list of projects the principal may access
            (default ``X-Auth-Projects``). Like the identity header, it
            is trusted — the edge gateway must strip client-supplied
            values (ADR 0017).
        project_header: Header carrying the caller's active-project
            selection (default ``X-Chorus-Project``), set by the SPA and
            validated against the asserted claim.
        default_project: Project claim + selection to use when the
            headers are absent. Dev-only, mirrors ``default_identity``;
            ``None`` in production forces the claim header.
    """

    header_name: str
    default_identity: str | None
    display_name_header: str
    projects_header: str
    project_header: str
    default_project: str | None


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
        enabled: When ``False``, retention is fully bypassed: :meth:`until`
            returns ``None`` for every row, so no post is stamped with a
            ``retention_until`` deadline and the nightly sweep never targets
            it. Toggled by ``RETENTION_ENABLED`` (default on).
    """

    default_days: int
    enabled: bool = True

    def until(self, basis: datetime | None) -> datetime | None:
        """Compute a post's retention deadline from an anchor timestamp.

        Args:
            basis: The timestamp retention is measured from — ingestion
                (``crawled_at``) time for postings and comments, the
                message's own ``timestamp`` for chat messages (which carry
                no crawl time). ``None`` when the row has no usable anchor.

        Returns:
            ``basis + default_days`` when retention is enabled and an anchor
            is present; ``None`` when retention is disabled or no anchor is
            available, in which case the post is kept indefinitely.
        """
        if not self.enabled or basis is None:
            return None
        return basis + timedelta(days=self.default_days)


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
        vector_k: Candidate fan-out for the entity vector search.
    """

    embed_cluster_threshold: float
    llm_tiebreak_enabled: bool
    case_normalize: bool
    vector_k: int


@dataclass(frozen=True)
class IngestionConfig:
    """Ingestion source-directory configuration.

    Attributes:
        source_dir: Directory containing per-table CSV dumps the
            file-backed upstream adapter reads from. One file per
            table (``postings.csv``, ``comments.csv``,
            ``messages.csv``, ``profiles.csv``).
    """

    source_dir: Path


@dataclass(frozen=True)
class IngestionUIConfig:
    """Toggle for the frontend-triggered ingestion path (ADR 0014).

    Gates the ``/ingestion/*`` upload-and-run endpoints and the matching
    React SPA ingestion screen. Default off so the data-mutating upload
    surface is never exposed by accident; an operator enables it explicitly.
    The CLI/bind-mount ingestion path (``make ingest``) is unaffected by
    this flag.

    Attributes:
        enabled: Whether the UI ingestion endpoints and screen are active.
    """

    enabled: bool


@dataclass(frozen=True)
class MetricsConfig:
    """Toggle for the Prometheus ``/metrics`` endpoint.

    Gates ``prometheus-fastapi-instrumentator``, which exposes aggregate
    request counters and latency histograms for the obs-plane scraper.
    Default on — the endpoint reports no user data, only request-shape
    metrics, so unlike :class:`IngestionUIConfig` there is no reason to
    default it off.

    Attributes:
        enabled: Whether ``GET /metrics`` is registered on the app.
    """

    enabled: bool


@dataclass(frozen=True)
class PathConfig:
    """Filesystem paths used by the running app.

    All paths are absolute. ``chorus_home`` is the writable root; the
    remaining paths default to subdirectories of it.

    Attributes:
        chorus_home: Writable root directory (defaults to ``<repo>/var``).
        queries: Directory containing Cypher query templates.
        migrations: Directory containing ordered Cypher migration files.
        raw_store: Path to the raw-store SQLite database.
    """

    chorus_home: Path
    queries: Path
    migrations: Path
    raw_store: Path


@dataclass(frozen=True)
class ProjectPaths:
    """Per-project state layout under ``$CHORUS_HOME/projects/<name>`` (ADR 0017).

    Each project keeps its own audit log, raw store, upload staging, and
    ingest drop point, so project state can be backed up, retained, and
    erased independently — mirroring the per-project Neo4j instances.

    Attributes:
        root: The project's state root, ``$CHORUS_HOME/projects/<name>``.
        audit_db: §76 BDSG audit SQLite database for this project.
        raw_store: Raw-store SQLite database for this project.
        uploads: Upload staging directory for the UI ingestion path.
        ingest_source: Default CSV drop directory for CLI ingestion.
    """

    root: Path
    audit_db: Path
    raw_store: Path
    uploads: Path
    ingest_source: Path


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
        raise RuntimeError(f"Unknown INFERENCE_PROVIDER={provider!r}; expected one of {sorted(_PROVIDER_DEFAULTS)}")
    defaults = _PROVIDER_DEFAULTS[provider]
    return InferenceConfig(
        provider=provider,
        api_base=_env("OPENAI_API_BASE", _PROVIDER_API_BASE[provider]) or "",
        api_key=_env("OPENAI_API_KEY", "EMPTY") or "EMPTY",
        TEXT_MODEL=_env("TEXT_MODEL", defaults["TEXT_MODEL"]) or defaults["TEXT_MODEL"],
        embed_model=_env("EMBED_MODEL", defaults["embed_model"]) or defaults["embed_model"],
        rerank_model=_env("RERANK_MODEL", defaults["rerank_model"]) or defaults["rerank_model"],
        embed_dim=_env_int("EMBED_DIM", 1024),
        timeout_s=_env_float("INFERENCE_TIMEOUT_S", 60.0),
        max_retries=_env_int("INFERENCE_MAX_RETRIES", 2),
    )


def load_agent_env() -> AgentConfig:
    """Load natural-language agent configuration from the environment.

    Returns:
        A populated :class:`AgentConfig`. ``AGENT_MODEL`` is optional; when
        unset the agent uses the inference provider's ``TEXT_MODEL``.
    """
    return AgentConfig(
        max_tool_iterations=_env_int("AGENT_MAX_ITERATIONS", 6),
        model=_env("AGENT_MODEL"),
        tool_message_max_items=_env_int("AGENT_TOOL_MESSAGE_MAX_ITEMS", 8),
        tool_message_max_chars=_env_int("AGENT_TOOL_MESSAGE_MAX_CHARS", 280),
    )


def load_language_env(default: str = "en") -> LanguageConfig:
    """Load the response language from ``RESPONSE_LANGUAGE``.

    Case-insensitive; surrounding whitespace is ignored. Unknown values
    fall back silently to ``default`` so a typo cannot break app bring-up.
    Shared by convention with docint, which reads the same variable.

    Args:
        default: Language code used when ``RESPONSE_LANGUAGE`` is unset or
            unrecognised. Defaults to ``"en"``.

    Returns:
        A populated :class:`LanguageConfig`.
    """
    raw = _env("RESPONSE_LANGUAGE")
    candidate = (raw if raw is not None else default).strip().lower()
    if candidate not in SUPPORTED_LANGUAGES:
        candidate = default.strip().lower()
        if candidate not in SUPPORTED_LANGUAGES:
            candidate = "en"
    code: Literal["en", "de"] = "de" if candidate == "de" else "en"
    return LanguageConfig(code=code)


def load_ner_client_env(
    default_enabled: bool = True,
    default_api_base: str = "http://vllm-router:4000",
    default_threshold: float = 0.3,
    default_timeout: float = 30.0,
    default_model_version: str = "gliner-community/gliner_large-v2.5",
) -> NERClientConfig:
    """Load the remote NER client configuration from the environment.

    The client POSTs to ``{api_base}/gliner`` with the GLiNER-native body
    shape ``{text, labels, threshold}``. The default ``api_base`` matches
    the LiteLLM router alias used by the full vllm-service stack; for the
    ner-only deployment shape, override with
    ``NER_API_BASE=http://gliner-ner:8000``.

    ``NER_API_KEY`` is sent as a Bearer token when set and omitted
    otherwise. It does **not** auto-fall-back to ``OPENAI_API_KEY``:
    when a multi-provider host runs Ollama for chat/embed and
    vllm-service for NER, the two keys are genuinely different and an
    implicit alias would hide that.

    Args:
        default_enabled: Whether NER extraction runs by default.
        default_api_base: Default base URL of the NER service.
        default_threshold: Default GLiNER confidence threshold, in [0, 1].
        default_timeout: Default per-request HTTP timeout in seconds.
        default_model_version: Default GLiNER model identifier stamped
            on ``:MENTIONS`` edges for provenance.

    Returns:
        A populated :class:`NERClientConfig` with trailing slashes
        stripped from ``api_base``.
    """
    raw_key = _env("NER_API_KEY")
    api_key = raw_key.strip() if raw_key and raw_key.strip() else None
    api_base = (_env("NER_API_BASE", default_api_base) or default_api_base).rstrip("/")
    return NERClientConfig(
        enabled=_env_bool("NER_ENABLED", default_enabled),
        api_base=api_base,
        api_key=api_key,
        threshold=_env_float("NER_THRESHOLD", default_threshold),
        timeout=_env_float("NER_TIMEOUT", default_timeout),
        model_version=_env("NER_MODEL_VERSION", default_model_version) or default_model_version,
    )


def load_projects_env() -> ProjectsConfig:
    """Load the configured project set from ``CHORUS_PROJECTS``.

    The value is a comma-separated list of project names matching
    ``^[a-z][a-z0-9-]{0,31}$`` (names feed env-var suffixes, filesystem
    paths, and HTTP headers). Empty segments are dropped; order is
    preserved. Unset/empty means single-project compat mode.

    Returns:
        A populated :class:`ProjectsConfig`.

    Raises:
        RuntimeError: If a name violates the grammar or appears twice —
            both are deployment mistakes that must fail at startup, not
            surface later as a wrong-instance query.
    """
    raw = _env("CHORUS_PROJECTS")
    if raw is None:
        return ProjectsConfig(names=("default",), explicit=False)
    names = [part.strip() for part in raw.split(",") if part.strip()]
    if not names:
        return ProjectsConfig(names=("default",), explicit=False)
    for name in names:
        if not _PROJECT_NAME_RE.match(name):
            raise RuntimeError(f"Invalid project name {name!r} in CHORUS_PROJECTS; expected ^[a-z][a-z0-9-]{{0,31}}$")
    if len(set(names)) != len(names):
        raise RuntimeError(f"CHORUS_PROJECTS contains duplicate project names: {raw!r}")
    return ProjectsConfig(names=tuple(names), explicit=True)


def _project_env_suffix(project: str) -> str:
    """Map a project name to its env-var suffix (``alpha-two`` → ``ALPHA_TWO``)."""
    return project.replace("-", "_").upper()


def load_neo4j_env(project: str | None = None) -> Neo4jConfig:
    """Load Neo4j connection parameters for one project's instance.

    With ``project`` omitted or ``"default"``, the legacy flat vars apply
    (``NEO4J_URI`` etc.) — single-project compat mode. For any other
    project, ``NEO4J_URI_<SUFFIX>`` is **required**: falling back to the
    shared URI would silently point two projects at the same instance,
    the cross-contamination ADR 0017 exists to prevent. Credentials and
    database name fall back to the shared vars unless a per-project
    override is set.

    Args:
        project: Project name from :func:`load_projects_env`, or ``None``
            for the implicit default project.

    Returns:
        A populated :class:`Neo4jConfig` for that project's instance.

    Raises:
        RuntimeError: If a non-default project has no ``NEO4J_URI_<SUFFIX>``.
    """
    if project is None or project == "default":
        return Neo4jConfig(
            uri=_env("NEO4J_URI", "bolt://localhost:7687") or "bolt://localhost:7687",
            user=_env("NEO4J_USER", "neo4j") or "neo4j",
            password=_env("NEO4J_PASSWORD", "neo4j") or "neo4j",
            database=_env("NEO4J_DATABASE", "neo4j") or "neo4j",
        )
    suffix = _project_env_suffix(project)
    uri = _env(f"NEO4J_URI_{suffix}")
    if uri is None:
        raise RuntimeError(f"Project {project!r} is configured but NEO4J_URI_{suffix} is not set")
    shared_user = _env("NEO4J_USER", "neo4j") or "neo4j"
    shared_password = _env("NEO4J_PASSWORD", "neo4j") or "neo4j"
    shared_database = _env("NEO4J_DATABASE", "neo4j") or "neo4j"
    return Neo4jConfig(
        uri=uri,
        user=_env(f"NEO4J_USER_{suffix}", shared_user) or shared_user,
        password=_env(f"NEO4J_PASSWORD_{suffix}", shared_password) or shared_password,
        database=_env(f"NEO4J_DATABASE_{suffix}", shared_database) or shared_database,
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
        display_name_header=_env("CHORUS_DISPLAY_NAME_HEADER", "X-Auth-Name") or "X-Auth-Name",
        projects_header=_env("CHORUS_PROJECTS_HEADER", "X-Auth-Projects") or "X-Auth-Projects",
        project_header=_env("CHORUS_PROJECT_HEADER", "X-Chorus-Project") or "X-Chorus-Project",
        default_project=_env("CHORUS_DEFAULT_PROJECT"),
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

    ``RETENTION_ENABLED`` (default ``true``) toggles retention globally —
    setting it false makes every ingested post non-expiring.
    ``RETENTION_DAYS_DEFAULT`` sets the window in days.

    Returns:
        A populated :class:`RetentionConfig`. Default retention is one
        year and enabled.
    """
    return RetentionConfig(
        default_days=_env_int("RETENTION_DAYS_DEFAULT", 365),
        enabled=_env_bool("RETENTION_ENABLED", True),
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
        vector_k=_env_int("RES_VECTOR_K", 5),
    )


def load_ingestion_env() -> IngestionConfig:
    """Load ingestion source-directory configuration from the environment.

    Defaults to ``<CHORUS_HOME>/ingest`` so a fresh install has a
    predictable drop point. ``INGESTION_SOURCE_DIR`` overrides; a
    ``~`` prefix is expanded against ``$HOME``.

    Returns:
        A populated :class:`IngestionConfig`.
    """
    raw = _env("INGESTION_SOURCE_DIR")
    src = Path(raw).expanduser() if raw else load_path_env().chorus_home / "ingest"
    return IngestionConfig(source_dir=src)


def load_ingestion_ui_env() -> IngestionUIConfig:
    """Load the frontend-ingestion toggle from the environment.

    ``INGESTION_UI_ENABLED`` (default ``false``) gates the
    ``/ingestion/*`` upload-and-run endpoints and the React SPA ingestion
    screen. See ADR 0014.

    Returns:
        A populated :class:`IngestionUIConfig`.
    """
    return IngestionUIConfig(enabled=_env_bool("INGESTION_UI_ENABLED", False))


def load_metrics_env() -> MetricsConfig:
    """Load the Prometheus ``/metrics`` toggle from the environment.

    ``METRICS_ENABLED`` (default ``true``) gates whether the
    Prometheus instrumentator registers ``GET /metrics`` on the app.

    Returns:
        A populated :class:`MetricsConfig`.
    """
    return MetricsConfig(enabled=_env_bool("METRICS_ENABLED", True))


def load_path_env() -> PathConfig:
    """Resolve filesystem paths used by the running app.

    ``CHORUS_HOME`` controls the writable root; ``RAW_STORE_PATH`` may
    override its child. The ``queries`` and ``migrations`` paths are
    package-relative and never overridable.

    Returns:
        A populated :class:`PathConfig` with every field as an absolute path.
    """
    home_raw = _env("CHORUS_HOME")
    home = Path(home_raw).expanduser() if home_raw else _REPO_ROOT / "var"
    raw_store_raw = _env("RAW_STORE_PATH")
    raw_store = Path(raw_store_raw) if raw_store_raw else home / "raw.sqlite"
    pkg_root = Path(__file__).resolve().parent.parent
    return PathConfig(
        chorus_home=home,
        queries=pkg_root / "queries",
        migrations=pkg_root / "migrations",
        raw_store=raw_store,
    )


def load_project_paths_env(project: str) -> ProjectPaths:
    """Resolve one project's state paths under ``$CHORUS_HOME/projects/<name>``.

    The legacy single-path overrides (``AUDIT_DB_PATH``, ``RAW_STORE_PATH``,
    ``INGESTION_SOURCE_DIR``) are honored only in single-project compat
    mode (``CHORUS_PROJECTS`` unset, project ``"default"``) — a single
    override path cannot be partitioned, and honoring it in explicit
    multi-project mode would merge state across projects.

    Args:
        project: Project name from :func:`load_projects_env`.

    Returns:
        A populated :class:`ProjectPaths` with absolute paths.
    """
    home = load_path_env().chorus_home
    root = home / "projects" / project
    compat = project == "default" and not load_projects_env().explicit

    audit_raw = _env("AUDIT_DB_PATH") if compat else None
    raw_store_raw = _env("RAW_STORE_PATH") if compat else None
    ingest_raw = _env("INGESTION_SOURCE_DIR") if compat else None
    return ProjectPaths(
        root=root,
        audit_db=Path(audit_raw) if audit_raw else root / "audit.sqlite",
        raw_store=Path(raw_store_raw) if raw_store_raw else root / "raw.sqlite",
        uploads=root / "uploads",
        ingest_source=Path(ingest_raw).expanduser() if ingest_raw else root / "ingest",
    )
