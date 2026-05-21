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
    text: str
    label: str
    start: int
    end: int
    confidence: float


@lru_cache(maxsize=1)
def _config() -> InferenceConfig:
    return load_inference_env()


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    cfg = _config()
    return OpenAI(
        base_url=cfg.api_base,
        api_key=cfg.api_key,
        timeout=cfg.timeout_s,
        max_retries=cfg.max_retries,
    )


def chat(
    messages: list[dict[str, str]], *, model: str | None = None, **kwargs: Any
) -> str:
    """Return the assistant message content for a single completion."""
    resp = _client().chat.completions.create(
        model=model or _config().TEXT_MODEL,
        messages=messages,  # type: ignore[arg-type]
        **kwargs,
    )
    content = resp.choices[0].message.content or ""
    return content


def embed(texts: list[str], *, model: str | None = None) -> list[list[float]]:
    """Return one embedding per input text."""
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
    """Return (index, score) pairs sorted by descending relevance.

    LiteLLM rerank lives outside the OpenAI SDK surface; call it directly
    over HTTP against the same base URL with the `hosted_vllm` contract.
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
        r = h.post(
            url, json=payload, headers={"Authorization": f"Bearer {cfg.api_key}"}
        )
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
    """Run NER over `text`. Routed to NER_MODEL via the LiteLLM proxy.

    The proxy is responsible for translating the chat-style call below into
    GLiNER's `{text, labels, threshold}` shape and returning entity spans in
    the assistant message as JSON.
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
