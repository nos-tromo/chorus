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
    v = os.environ.get(key)
    return v if v not in (None, "") else default


def _env_required(key: str) -> str:
    v = _env(key)
    if v is None:
        raise RuntimeError(f"Required env var not set: {key}")
    return v


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    return int(raw) if raw is not None else default


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    return float(raw) if raw is not None else default


def _env_bool(key: str, default: bool) -> bool:
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
        "chat_model": "Qwen/Qwen3.5-2B",
        "embed_model": "BAAI/bge-m3",
        "rerank_model": "BAAI/bge-reranker-v2-m3",
        "ner_model": "gliner-community/gliner_large-v2.5",
    },
    "ollama": {
        "chat_model": "gpt-oss:20b",
        "embed_model": "bge-m3",
        "rerank_model": "bge-reranker-v2-m3",
        "ner_model": "gliner",
    },
    "openai": {
        "chat_model": "gpt-4o",
        "embed_model": "text-embedding-3-small",
        "rerank_model": "bge-reranker-v2-m3",
        "ner_model": "gliner",
    },
}


@dataclass(frozen=True)
class InferenceConfig:
    provider: str
    api_base: str
    api_key: str
    chat_model: str
    embed_model: str
    rerank_model: str
    ner_model: str
    embed_dim: int
    timeout_s: float
    max_retries: int


@dataclass(frozen=True)
class Neo4jConfig:
    uri: str
    user: str
    password: str
    database: str


@dataclass(frozen=True)
class PrincipalConfig:
    header_name: str
    default_identity: str | None


@dataclass(frozen=True)
class AuditConfig:
    db_path: Path
    retention_days: int


@dataclass(frozen=True)
class RetentionConfig:
    default_days: int


@dataclass(frozen=True)
class ResolutionConfig:
    embed_cluster_threshold: float
    llm_tiebreak_enabled: bool
    case_normalize: bool


@dataclass(frozen=True)
class PathConfig:
    chorus_home: Path
    logs: Path
    queries: Path
    migrations: Path
    raw_store: Path


def load_inference_env() -> InferenceConfig:
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
        chat_model=_env("CHAT_MODEL", defaults["chat_model"]) or defaults["chat_model"],
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
    return Neo4jConfig(
        uri=_env("NEO4J_URI", "bolt://localhost:7687") or "bolt://localhost:7687",
        user=_env("NEO4J_USER", "neo4j") or "neo4j",
        password=_env("NEO4J_PASSWORD", "neo4j") or "neo4j",
        database=_env("NEO4J_DATABASE", "neo4j") or "neo4j",
    )


def load_principal_env() -> PrincipalConfig:
    return PrincipalConfig(
        header_name=_env("CHORUS_AUTH_HEADER", "X-Auth-User") or "X-Auth-User",
        default_identity=_env("CHORUS_DEFAULT_IDENTITY"),
    )


def load_audit_env() -> AuditConfig:
    paths = load_path_env()
    db_path_raw = _env("AUDIT_DB_PATH")
    db_path = Path(db_path_raw) if db_path_raw else paths.chorus_home / "audit.sqlite"
    return AuditConfig(
        db_path=db_path,
        retention_days=_env_int("AUDIT_RETENTION_DAYS", 365 * 2),
    )


def load_retention_env() -> RetentionConfig:
    return RetentionConfig(
        default_days=_env_int("RETENTION_DAYS_DEFAULT", 365),
    )


def load_resolution_env() -> ResolutionConfig:
    return ResolutionConfig(
        embed_cluster_threshold=_env_float("RES_EMBED_THRESHOLD", 0.86),
        llm_tiebreak_enabled=_env_bool("RES_LLM_TIEBREAK", True),
        case_normalize=_env_bool("RES_CASE_NORMALIZE", True),
    )


def load_path_env() -> PathConfig:
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
