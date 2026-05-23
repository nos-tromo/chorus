"""Single OpenAI-protocol client for all inference tasks.

All chat / embed / rerank / NER traffic terminates at vllm-service's LiteLLM
proxy and is routed by the `model` field. No provider branching outside this
module — switching from `vllm` to `ollama` or `openai` is an env change.

GLiNER NER is treated as a routed task: the proxy is expected to accept
`{"model": NER_MODEL, "input": text, "extra_body": {"labels": [...]}}`-shaped
requests on `/v1/chat/completions` (work-in-progress on vllm-service); the
chorus side is unaware of that routing and just calls `extract_entities`.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import httpx
from openai import OpenAI
from pydantic import BaseModel

from chorus.utils.env_cfg import InferenceConfig, load_inference_env


class EntitySpan(BaseModel):
    """One entity span extracted from a text body.

    Attributes:
        text: Surface form as it appears in the source text.
        label: Entity type assigned by the NER model (e.g. ``"PERSON"``).
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
def _config() -> InferenceConfig:
    """Return the process-wide cached inference configuration.

    The config is read from the environment exactly once; subsequent
    calls return the same object. Tests that need to override env vars
    must clear this cache via ``_config.cache_clear()``.

    Returns:
        The cached :class:`InferenceConfig`.
    """
    return load_inference_env()


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    """Return the process-wide cached OpenAI-compatible client.

    Cached for the lifetime of the process so HTTP connection pooling
    works across calls. Tests that need a fresh client must clear this
    cache via ``_client.cache_clear()``.

    Returns:
        The cached :class:`openai.OpenAI` client.
    """
    cfg = _config()
    return OpenAI(
        base_url=cfg.api_base,
        api_key=cfg.api_key,
        timeout=cfg.timeout_s,
        max_retries=cfg.max_retries,
    )


def chat(messages: list[dict[str, str]], *, model: str | None = None, **kwargs: Any) -> str:
    """Return the assistant message content for a single chat completion.

    Args:
        messages: OpenAI-style messages list (each item carries ``role``
            and ``content``).
        model: Model id to route to. Defaults to ``cfg.TEXT_MODEL``.
        **kwargs: Extra keyword arguments forwarded to
            ``client.chat.completions.create``.

    Returns:
        The assistant message text, or an empty string when the provider
        returns no content.
    """
    resp = _client().chat.completions.create(
        model=model or _config().TEXT_MODEL,
        messages=messages,  # type: ignore[arg-type]
        **kwargs,
    )
    content = resp.choices[0].message.content or ""
    return content


def embed(texts: list[str], *, model: str | None = None) -> list[list[float]]:
    """Return one embedding vector per input text.

    Args:
        texts: Texts to embed; sent as a single batched request.
        model: Model id to route to. Defaults to ``cfg.embed_model``.

    Returns:
        A list of float vectors, one per input text, in the same order.
        Each vector has the embedding model's native dimensionality
        (configured via ``EMBED_DIM`` for vector-index sizing).
    """
    resp = _client().embeddings.create(
        model=model or _config().embed_model,
        input=texts,
    )
    return [item.embedding for item in resp.data]


def rerank(
    query: str,
    docs: list[str],
    *,
    model: str | None = None,
    top_n: int | None = None,
) -> list[tuple[int, float]]:
    """Return ``(index, score)`` pairs sorted by descending relevance.

    LiteLLM rerank lives outside the OpenAI SDK surface; this function
    calls it directly over HTTP against the same base URL with the
    ``hosted_vllm`` contract.

    Args:
        query: Query string to score documents against.
        docs: Candidate documents to score.
        model: Model id to route to. Defaults to ``cfg.rerank_model``.
        top_n: If set, ask the server to return only the top ``top_n``
            results.

    Returns:
        A list of ``(index, relevance_score)`` tuples, where ``index``
        refers back into ``docs``. Sorted by descending score.

    Raises:
        httpx.HTTPStatusError: If the rerank endpoint returns a non-2xx
            response.
    """
    cfg = _config()
    base = cfg.api_base.rstrip("/")
    # LiteLLM exposes /rerank at the same /v1 prefix in proxy mode.
    url = f"{base}/rerank"
    payload: dict[str, Any] = {
        "model": model or cfg.rerank_model,
        "query": query,
        "documents": docs,
    }
    if top_n is not None:
        payload["top_n"] = top_n
    with httpx.Client(timeout=cfg.timeout_s) as h:
        r = h.post(url, json=payload, headers={"Authorization": f"Bearer {cfg.api_key}"})
    r.raise_for_status()
    data = r.json()
    results = data.get("results", [])
    return [(int(item["index"]), float(item["relevance_score"])) for item in results]


def extract_entities(
    text: str,
    *,
    labels: list[str] | None = None,
    model: str | None = None,
    threshold: float = 0.5,
) -> list[EntitySpan]:
    """Run NER over ``text`` via the LiteLLM proxy.

    The proxy is responsible for translating this chat-shaped call into
    GLiNER's ``{text, labels, threshold}`` request shape and returning
    entity spans as JSON in the assistant message.

    Args:
        text: Source text to extract entities from.
        labels: Optional whitelist of GLiNER labels to request. ``None``
            uses the proxy's default label set.
        model: Model id to route to. Defaults to ``cfg.ner_model``.
        threshold: Minimum confidence for the proxy to return a span,
            in [0, 1].

    Returns:
        Extracted entity spans in document order.

    Raises:
        json.JSONDecodeError: If the proxy returns a non-JSON body.
        pydantic.ValidationError: If a returned span is missing required
            fields.
    """
    cfg = _config()
    extra_body: dict[str, Any] = {"gliner_threshold": threshold}
    if labels is not None:
        extra_body["gliner_labels"] = labels
    resp = _client().chat.completions.create(
        model=model or cfg.ner_model,
        messages=[{"role": "user", "content": text}],
        extra_body=extra_body,
    )
    content = resp.choices[0].message.content or "[]"
    raw = json.loads(content)
    return [EntitySpan(**span) for span in raw]
