"""HTTP client for the remote GLiNER NER service hosted by vllm-service.

NER is the one task chorus does **not** route through the OpenAI-compatible
``provider`` module: GLiNER on vllm-service is a Ray Serve pass-through with
its own ``{text, labels, threshold}`` request shape, served at ``/gliner``.
This module owns that endpoint so chorus can mix-and-match providers — e.g.
Ollama for chat/embed/rerank on a dev Mac, while NER still reaches
vllm-service's ner-only stack.

Two operator-side deployment shapes are supported by the same code, switched
via env vars (see :func:`chorus.utils.env_cfg.load_ner_client_env`):

- Full vllm-service stack: ``NER_API_BASE=http://vllm-router:4000`` with
  Bearer auth (``NER_API_KEY=$OPENAI_API_KEY``).
- ner-only stack: ``NER_API_BASE=http://gliner-ner:8000``, no auth.

On any network, HTTP, or parse failure the client logs a warning and
returns an empty span list rather than raising — a missing NER service
must not block ingestion.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import httpx
from loguru import logger
from pydantic import BaseModel

from chorus.utils.env_cfg import NERClientConfig, load_ner_client_env

DEFAULT_NER_LABELS: list[str] = [
    "bank_account",  # bank account numbers
    "date",  # absolute or relative dates / periods
    "event",  # named hurricanes, battles, wars, sports events, etc.
    "fac",  # buildings, airports, highways, bridges, etc.
    "group",  # nationalities or religious / political groups
    "lang",  # any named language
    "loc",  # locations: countries, cities, states, regions
    "mail",  # email addresses
    "money",  # monetary values, including unit
    "org",  # companies, agencies, institutions, etc.
    "person",  # people, including fictional
    "phone",  # phone numbers
    "time",  # times smaller than a day
    "weapon",  # named vehicles, weapons, or products
]


class EntitySpan(BaseModel):
    """One entity span extracted from a text body.

    Attributes:
        text: Surface form as it appears in the source text.
        label: Entity type assigned by GLiNER (e.g. ``"person"``).
        start: UTF-16 code-unit start offset into the source text.
        end: UTF-16 code-unit end offset into the source text (exclusive).
        confidence: Model-reported confidence in the prediction, in [0, 1].
    """

    text: str
    label: str
    start: int
    end: int
    confidence: float


@lru_cache(maxsize=1)
def _config() -> NERClientConfig:
    """Return the process-wide cached NER-client configuration.

    The config is read from the environment exactly once; subsequent
    calls return the same object. Tests that need to override env vars
    must clear this cache via ``_config.cache_clear()``.

    Returns:
        The cached :class:`NERClientConfig`.
    """
    return load_ner_client_env()


@lru_cache(maxsize=1)
def _client() -> httpx.Client:
    """Return the process-wide cached HTTP client.

    Cached for the lifetime of the process so HTTP connection pooling
    works across calls. Tests that need a fresh client must clear this
    cache via ``_client.cache_clear()``.

    Returns:
        An :class:`httpx.Client` bound to ``cfg.api_base`` with the
        Bearer header pre-set when ``cfg.api_key`` is non-empty.
    """
    cfg = _config()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    return httpx.Client(
        base_url=cfg.api_base,
        timeout=cfg.timeout,
        headers=headers,
    )


def extract_entities(
    text: str,
    *,
    labels: list[str] | None = None,
    threshold: float | None = None,
) -> list[EntitySpan]:
    """Run NER over ``text`` against the remote GLiNER service.

    POSTs ``{text, labels, threshold}`` to ``{NER_API_BASE}/gliner`` and
    maps each returned entity into an :class:`EntitySpan`. Empty or
    whitespace-only input short-circuits to an empty list without
    calling the service. Network, HTTP, or malformed-payload errors are
    swallowed (logged at WARNING) and yield an empty list — NER is
    advisory; an unavailable service must not block ingestion. Per-entity
    validation drops items missing ``text``, ``label``, ``start``,
    ``end``, or ``score``.

    Args:
        text: Source text to extract entities from.
        labels: Whitelist of GLiNER labels to request. When ``None`` or
            empty, :data:`DEFAULT_NER_LABELS` is sent — the upstream
            server requires the field and does not synthesize a default.
        threshold: Per-call confidence cutoff. When ``None``, uses
            ``cfg.threshold``.

    Returns:
        Extracted entity spans in document order.
    """
    if not text.strip():
        return []

    cfg = _config()
    payload: dict[str, Any] = {
        "text": text,
        "labels": list(labels) if labels else list(DEFAULT_NER_LABELS),
        "threshold": threshold if threshold is not None else cfg.threshold,
    }

    try:
        response = _client().post("/gliner", json=payload)
        response.raise_for_status()
        body = response.json()
    except Exception as exc:
        logger.warning("Remote NER call failed: {}", exc)
        return []

    raw_entities = body.get("entities") if isinstance(body, dict) else None
    if not isinstance(raw_entities, list):
        return []

    spans: list[EntitySpan] = []
    for item in raw_entities:
        if not isinstance(item, dict):
            continue
        try:
            spans.append(
                EntitySpan(
                    text=item["text"],
                    label=item["label"],
                    start=int(item["start"]),
                    end=int(item["end"]),
                    confidence=float(item["score"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return spans
