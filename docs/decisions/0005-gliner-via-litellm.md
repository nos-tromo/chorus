# 0005 — GLiNER NER via LiteLLM (OpenAI-protocol)

Status: accepted
Date: 2026-05-20

## Context

GLiNER is the NER model chorus uses for entity extraction. vllm-service
historically exposed GLiNER at a non-OpenAI endpoint (`/gliner`) that
takes `{text, labels, threshold}` and returns spans directly. Work is
underway on the vllm-service side to route GLiNER as a LiteLLM task so
clients can call it with the same OpenAI-protocol HTTP shape as
chat/embed/rerank.

## Decision

Chorus assumes that work is satisfied: `chorus/inference/provider.py`
calls `extract_entities` through the same OpenAI client as the other
tasks, passing GLiNER-specific parameters via `extra_body`. There is
no `/gliner` carve-out in chorus code. `NER_MODEL` selects the routed
task.

## Alternatives considered

- **Maintain a parallel HTTP wrapper for `/gliner`.** Splits the
  inference surface in two and makes provider swaps non-uniform.
  Forces a chorus code change if the routing on the vllm-service side
  ever shifts.
- **Embed GLiNER in chorus.** Violates the airgap rule that chorus
  ships no model weights and the invariant that inference is shared,
  never embedded.

## Consequences

- Positive: one provider surface for all inference tasks, swapping
  NER vendors is an env-var change, no special-case code paths.
- Negative: chorus depends on vllm-service completing the LiteLLM
  GLiNER route. If that work slips, chorus must either temporarily
  add the `/gliner` wrapper or operate without NER.
- Reversal trigger: vllm-service abandons the LiteLLM routing for
  GLiNER, OR LiteLLM's contract for non-chat/embed routes shifts
  enough that the `extra_body` shape is unsuitable — in which case
  isolate the workaround in `chorus/inference/provider.py:extract_entities`
  only.
