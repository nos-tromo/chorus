"""Single OpenAI-protocol client for chat / embed / rerank.

All traffic from this module terminates at an OpenAI-compatible endpoint —
in production, vllm-service's LiteLLM proxy, routed by the ``model`` field.
Switching from ``vllm`` to ``ollama`` or ``openai`` is an env change.

NER is **not** handled here: GLiNER on vllm-service speaks its own
``/gliner`` HTTP shape rather than the OpenAI protocol, so it lives in
:mod:`chorus.inference.ner_client`. Keeping it separate lets chorus mix
providers — e.g. Ollama for chat/embed/rerank, vllm-service ner-only for
NER — without coupling the two configs.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import httpx
from openai import OpenAI
from openai.types.chat import ChatCompletionMessage

from chorus.utils.env_cfg import InferenceConfig, load_inference_env


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


def api_base() -> str:
    """Return the configured OpenAI-compatible base URL.

    Exposed for diagnostics (e.g. error messages) so callers can report
    *where* a failed inference request was sent without reaching into the
    private config cache.

    Returns:
        The base URL from the active :class:`InferenceConfig`.
    """
    return _config().api_base


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
    # OpenAI SDK typing rejects plain-dict messages + **kwargs; the call is runtime-correct.
    resp = _client().chat.completions.create(  # pyrefly: ignore[no-matching-overload]
        model=model or _config().TEXT_MODEL,
        messages=messages,
        **kwargs,
    )
    content = resp.choices[0].message.content or ""
    return content


def chat_message(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
    **kwargs: Any,
) -> ChatCompletionMessage:
    """Return the full assistant message for a single chat completion.

    Unlike :func:`chat`, this returns the message object itself so callers
    can inspect ``message.tool_calls`` for OpenAI-style tool calling. All
    traffic still terminates at the configured OpenAI-compatible endpoint
    (the LiteLLM proxy in production), so this stays airgap-safe.

    Args:
        messages: OpenAI-style messages list — role/content items plus, in
            a tool-calling loop, assistant tool-call turns and ``tool``
            result messages.
        model: Model id to route to. Defaults to ``cfg.TEXT_MODEL``.
        tools: OpenAI tool definitions to advertise. Omitted from the
            request when ``None``.
        tool_choice: OpenAI ``tool_choice`` directive (e.g. ``"auto"``).
            Omitted from the request when ``None``.
        **kwargs: Extra keyword arguments forwarded to
            ``client.chat.completions.create``.

    Returns:
        The assistant :class:`ChatCompletionMessage`, which may carry
        ``content`` and/or ``tool_calls``.
    """
    extra: dict[str, Any] = dict(kwargs)
    if tools is not None:
        extra["tools"] = tools
    if tool_choice is not None:
        extra["tool_choice"] = tool_choice
    # OpenAI SDK typing rejects plain-dict messages + **extra; the call is runtime-correct.
    resp = _client().chat.completions.create(  # pyrefly: ignore[no-matching-overload]
        model=model or _config().TEXT_MODEL,
        messages=messages,
        **extra,
    )
    return resp.choices[0].message


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
