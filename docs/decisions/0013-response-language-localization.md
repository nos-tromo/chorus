# 0013 — Response-language localization (`RESPONSE_LANGUAGE`)

Status: accepted
Date: 2026-06-05

## Context

chorus is deployed in a German-language work environment, but the agent prompt
and the Streamlit UI were English-only. Two distinct problems followed:

- **Language.** The natural-language agent answered in English, and the UI
  captions were English literals scattered across `streamlit_app.py` and the
  seven `ui/pages/*.py` files.
- **German entity sensitivity.** The agent passes the entity string it extracts
  straight into a tool, and the query Cypher matches case-insensitively but
  **exactly** (`toLower(surface_form) = toLower($entity)`). A user asking
  *"Welche Beiträge erwähnen die AfD?"* can lead the model to call
  `posts_mentioning(entity="die AfD")`, and the leading article `die` makes the
  match miss the alias `AfD`. The article — not the casing — is what breaks it.

docint already solves the language half with a `RESPONSE_LANGUAGE` env var
(`en`/`de`), a parallel prompt tree, and a static `ui_strings.py` catalog.
Matching its shape keeps the federation consistent and lets one setting flip
both apps in the same environment.

## Decision

A single env switch, **`RESPONSE_LANGUAGE`** (`en`|`de`, default `en`,
case-insensitive, unknown values fall back to `en`), read at runtime in both
processes. No module references the language except one loader.

1. **`load_language_env()` → `LanguageConfig(code)`** in `utils/env_cfg.py`, a
   near-verbatim port of docint's loader using chorus's `_env` helper. Resolved
   at call-time (no import-time snapshot), so it needs no `_CHORUS_ENV_MODULES`
   entry and is monkeypatch-friendly in tests.
2. **Parallel in-code prompts.** `agent/prompts.py` keeps the English prompt
   (now `SYSTEM_PROMPT_EN`, with `SYSTEM_PROMPT` retained as a back-compat
   alias) and adds `SYSTEM_PROMPT_DE`; `get_system_prompt(code)` selects, with
   English as the fallback. The German prompt is a full translation **plus** an
   entity-sensitivity block: strip leading German articles
   (`der/die/das/den/dem/des/ein/…`), preserve canonical casing (`AfD`), and a
   note that matching is case-insensitive but article-sensitive. It also
   instructs the model to answer in German. The English prompt is unchanged.
3. **Selection threaded through the request.** `run_agent(..., language=...)`
   selects the prompt; the agent router resolves `load_language_env().code` and
   forwards it — mirroring how it already loads and passes `AgentConfig`. The
   loop stays pure (no env read), so it is unit-testable with a plain argument.
4. **Static UI catalog.** `utils/ui_strings.py` holds
   `UI_STRINGS = {"en": {...}, "de": {...}}` and `ui_string(key)`; the pages
   replace inline literals with lookups, `.format(...)`-ing dynamic values. An
   import-time parity check raises `RuntimeError` if the key sets diverge. It
   lives under `utils/` (like docint) so it imports without Streamlit; a
   key-usage test scans the pages so a typo'd key fails CI, not a render.
5. **Both compose services get the var.** `RESPONSE_LANGUAGE` is added to the
   `backend` and `frontend` `environment:` blocks; the frontend has no
   `env_file`, so the explicit entry is load-bearing there.

## Consequences

- Setting `RESPONSE_LANGUAGE=de` once in the repo-root `.env` switches the agent
  answer language, the entity-argument sensitivity, and every UI caption.
- New captions must be added in **both** languages — the import-time parity
  check and the key-usage test enforce it.
- `backend` and `frontend` must be set to the same value (one var, set once via
  compose interpolation); documented as "must match".
- Adding a third language is one entry in `SUPPORTED_LANGUAGES`, one prompt, and
  one catalog column.
- **Residual (English in `de` mode):** the agent's actual answer language is the
  model's behavior, not asserted in tests (tests check that the German prompt is
  delivered, not that the model obeys). Developer-facing surfaces stay English by
  design — API `HTTPException` `detail` strings (which `00_agent.py` shows when
  present) and the browser-tab `page_title`s.

## Alternatives considered

- **Runtime LLM translation of captions.** Rejected: nondeterministic wording
  run-to-run, latency on every render, extra load on the inference path, and a
  divergence from docint. Still in-network, so not an airgap violation — just
  wasteful for static UI chrome.
- **gettext / `.po` files.** Rejected: framework overhead for ~50 strings; the
  static dict is simpler and deterministic.
- **Cypher-level article-insensitive matching.** Rejected as the fix for the
  `die AfD` problem: loosening the match risks false positives across every
  tool, and the request was explicitly to "force the text model" — i.e. fix it
  in the prompt. The prompt approach is also reversible and has no query-side
  blast radius.
- **Per-request language parameter** (header/body). Rejected: German mode is a
  global deployment property, not a per-call choice; a global env switch is
  simpler and matches both the requirement and docint.
- **One English prompt + an appended German directive block.** Rejected: a
  mixed-language prompt is followed less reliably than a coherent single-language
  one, and it diverges from docint's parallel-translation pattern.
