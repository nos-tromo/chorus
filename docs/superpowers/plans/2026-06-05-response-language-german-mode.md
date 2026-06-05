# German mode (`RESPONSE_LANGUAGE`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an env switch `RESPONSE_LANGUAGE` (`en`|`de`, default `en`) that makes the agent answer in German with article-stripping entity sensitivity and renders the Streamlit UI captions in German.

**Architecture:** One loader (`load_language_env`) read at runtime in both the FastAPI backend (selects the agent system prompt) and the Streamlit frontend (selects UI captions from a static catalog). No code references the language except that loader; flipping languages is an env change. Mirrors docint's `RESPONSE_LANGUAGE`.

**Tech Stack:** Python 3.12, FastAPI, Streamlit, pytest, ruff, mypy, uv.

**Spec:** `docs/superpowers/specs/2026-06-05-german-mode-response-language-design.md` — holds the full German system prompt and the complete bilingual caption catalog. This plan references it for those two large blocks rather than duplicating them.

**Branch:** `feat/response-language-german-mode` (spec already committed here).

---

### Task 1: `LanguageConfig` + `load_language_env` in env_cfg

**Files:**
- Modify: `chorus/utils/env_cfg.py`
- Test: `tests/utils/test_language_config.py`

- [ ] **Step 1: Write the failing test** (`tests/utils/test_language_config.py`)

```python
"""Tests for the RESPONSE_LANGUAGE response-language switch."""

from __future__ import annotations

import pytest


def test_default_is_english(monkeypatch: pytest.MonkeyPatch) -> None:
    """With RESPONSE_LANGUAGE unset, the language is English.

    The loader reads ``os.environ`` on every call, so ``delenv`` alone —
    without a module reload, which would re-trigger ``load_dotenv`` and
    restore a value from the local ``.env`` — exercises the default.
    """
    monkeypatch.delenv("RESPONSE_LANGUAGE", raising=False)
    from chorus.utils.env_cfg import load_language_env

    assert load_language_env().code == "en"


def test_german_is_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    """RESPONSE_LANGUAGE=de selects German."""
    monkeypatch.setenv("RESPONSE_LANGUAGE", "de")
    from chorus.utils.env_cfg import load_language_env

    assert load_language_env().code == "de"


@pytest.mark.parametrize("raw", ["DE", "De", " de "])
def test_value_is_case_and_whitespace_insensitive(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """The code is normalised: case-folded and stripped."""
    monkeypatch.setenv("RESPONSE_LANGUAGE", raw)
    from chorus.utils.env_cfg import load_language_env

    assert load_language_env().code == "de"


def test_unknown_value_falls_back_to_english(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unrecognised language code falls back silently to English."""
    monkeypatch.setenv("RESPONSE_LANGUAGE", "fr")
    from chorus.utils.env_cfg import load_language_env

    assert load_language_env().code == "en"
```

- [ ] **Step 2: Run it — expect FAIL** (`ImportError: cannot import name 'load_language_env'`)

Run: `uv run pytest tests/utils/test_language_config.py -q`

- [ ] **Step 3: Implement** — three edits in `chorus/utils/env_cfg.py`:

  (a) add `from typing import Literal` after `from pathlib import Path`.

  (b) after the `AgentConfig` dataclass, add:

```python
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
```

  (c) after the `load_agent_env()` function, add:

```python
def load_language_env(default: str = "en") -> LanguageConfig:
    """Load the response language from ``RESPONSE_LANGUAGE``.

    Case-insensitive; surrounding whitespace is ignored. Unknown values
    fall back silently to ``default`` so a typo cannot break app bring-up.
    Shared by convention with docint, which reads the same variable.

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
```

- [ ] **Step 4: Run it — expect PASS**

Run: `uv run pytest tests/utils/test_language_config.py -q`

- [ ] **Step 5: Commit**

```bash
git add chorus/utils/env_cfg.py tests/utils/test_language_config.py
git commit -m "feat(i18n): RESPONSE_LANGUAGE env switch + LanguageConfig"
```

---

### Task 2: German system prompt + selector

**Files:**
- Modify: `chorus/agent/prompts.py` (rewrite)
- Test: `tests/agent/test_prompts.py`

- [ ] **Step 1: Write the failing test** (`tests/agent/test_prompts.py`)

```python
"""Tests for system-prompt selection by language."""

from __future__ import annotations


def test_english_is_default_and_unchanged() -> None:
    from chorus.agent.prompts import SYSTEM_PROMPT_EN, get_system_prompt

    assert get_system_prompt("en") is SYSTEM_PROMPT_EN
    assert "analytical assistant" in SYSTEM_PROMPT_EN


def test_unknown_code_falls_back_to_english() -> None:
    from chorus.agent.prompts import SYSTEM_PROMPT_EN, get_system_prompt

    assert get_system_prompt("fr") is SYSTEM_PROMPT_EN


def test_german_prompt_is_distinct_and_carries_sensitivity_rules() -> None:
    from chorus.agent.prompts import SYSTEM_PROMPT_DE, SYSTEM_PROMPT_EN, get_system_prompt

    de = get_system_prompt("de")
    assert de is SYSTEM_PROMPT_DE
    assert de != SYSTEM_PROMPT_EN
    assert "auf Deutsch" in de          # answers in German
    assert "AfD" in de                   # entity example
    assert "Artikel" in de               # article-stripping rule


def test_backcompat_alias_points_at_english() -> None:
    from chorus.agent.prompts import SYSTEM_PROMPT, SYSTEM_PROMPT_EN

    assert SYSTEM_PROMPT is SYSTEM_PROMPT_EN
```

- [ ] **Step 2: Run — expect FAIL**

Run: `uv run pytest tests/agent/test_prompts.py -q`

- [ ] **Step 3: Implement** — rewrite `chorus/agent/prompts.py`: keep the current English text verbatim as `SYSTEM_PROMPT_EN`; add `SYSTEM_PROMPT_DE` (full German text from the spec, §Design 2); add `SYSTEM_PROMPT = SYSTEM_PROMPT_EN` alias; add `_SYSTEM_PROMPTS = {"en": SYSTEM_PROMPT_EN, "de": SYSTEM_PROMPT_DE}` and:

```python
def get_system_prompt(code: str) -> str:
    """Return the system prompt for ``code``; unknown codes → English."""
    return _SYSTEM_PROMPTS.get(code, SYSTEM_PROMPT_EN)
```

- [ ] **Step 4: Run — expect PASS**
- [ ] **Step 5: Commit** — `feat(agent): German system prompt with entity-sensitivity + get_system_prompt selector`

---

### Task 3: Wire language into `run_agent`

**Files:**
- Modify: `chorus/agent/loop.py` (line 23 import; `run_agent` signature ~90; convo line 120)
- Test: `tests/agent/test_loop.py` (append)

- [ ] **Step 1: Append failing tests** to `tests/agent/test_loop.py`:

```python
def test_language_selects_system_prompt(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_agent(language='de') sends the German system prompt to the provider."""
    from chorus.agent.loop import run_agent
    from chorus.agent.prompts import get_system_prompt
    from chorus.inference import provider

    captured: list[dict[str, Any]] = []

    def _capture(messages: list[dict[str, Any]], **kwargs: Any) -> _FakeMessage:
        captured.append(messages[0])
        return _FakeMessage(content="ok")

    monkeypatch.setattr(provider, "chat_message", _capture)
    run_agent(
        migrated_driver, in_memory_audit, user="u",
        messages=[{"role": "user", "content": "hallo"}], language="de",
    )
    assert captured[0]["role"] == "system"
    assert captured[0]["content"] == get_system_prompt("de")


def test_default_language_is_english_prompt(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no language argument, the English prompt is used."""
    from chorus.agent.loop import run_agent
    from chorus.agent.prompts import get_system_prompt
    from chorus.inference import provider

    captured: list[dict[str, Any]] = []

    def _capture(messages: list[dict[str, Any]], **kwargs: Any) -> _FakeMessage:
        captured.append(messages[0])
        return _FakeMessage(content="ok")

    monkeypatch.setattr(provider, "chat_message", _capture)
    run_agent(
        migrated_driver, in_memory_audit, user="u",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert captured[0]["content"] == get_system_prompt("en")
```

- [ ] **Step 2: Run — expect FAIL** (`run_agent() got an unexpected keyword argument 'language'`)

Run: `uv run pytest tests/agent/test_loop.py -q`

- [ ] **Step 3: Implement** in `chorus/agent/loop.py`:
  - line 23: `from chorus.agent.prompts import get_system_prompt`
  - add keyword-only param to `run_agent` (after `model: str | None = None,`): `language: str = "en",` and a docstring line: `language: Response language code (\`en\`/\`de\`) selecting the system prompt.`
  - line 120: `convo: list[dict[str, Any]] = [{"role": "system", "content": get_system_prompt(language)}, *messages]`

- [ ] **Step 4: Run — expect PASS** (whole loop file): `uv run pytest tests/agent/test_loop.py -q`
- [ ] **Step 5: Commit** — `feat(agent): run_agent selects system prompt by language`

---

### Task 4: Resolve + forward language in the agent router

**Files:**
- Modify: `chorus/api/routers/agent.py` (import line 17; `run_agent(...)` call ~75)
- Test: `tests/agent/test_agent_router.py` (append)

- [ ] **Step 1: Append failing tests** to `tests/agent/test_agent_router.py`:

```python
def test_response_language_de_uses_german_prompt(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RESPONSE_LANGUAGE=de makes /agent/query send the German system prompt."""
    from chorus.agent.prompts import get_system_prompt
    from chorus.api.routers import agent as agent_router
    from chorus.inference import provider

    monkeypatch.setenv("RESPONSE_LANGUAGE", "de")
    captured: list[dict[str, Any]] = []

    def _capture(messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        captured.append(messages[0])
        return _FakeMessage(content="ok")

    monkeypatch.setattr(provider, "chat_message", _capture)
    app = FastAPI()
    app.include_router(agent_router.router)
    app.state.driver = migrated_driver
    app.state.audit = in_memory_audit
    resp = TestClient(app).post(
        "/agent/query",
        json={"messages": [{"role": "user", "content": "hallo"}]},
        headers={"X-Auth-User": "analyst"},
    )
    assert resp.status_code == 200
    assert captured[0]["content"] == get_system_prompt("de")


def test_default_response_language_uses_english_prompt(
    migrated_driver: Driver, in_memory_audit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With RESPONSE_LANGUAGE unset, /agent/query sends the English prompt."""
    from chorus.agent.prompts import get_system_prompt
    from chorus.api.routers import agent as agent_router
    from chorus.inference import provider

    monkeypatch.delenv("RESPONSE_LANGUAGE", raising=False)
    captured: list[dict[str, Any]] = []

    def _capture(messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        captured.append(messages[0])
        return _FakeMessage(content="ok")

    monkeypatch.setattr(provider, "chat_message", _capture)
    app = FastAPI()
    app.include_router(agent_router.router)
    app.state.driver = migrated_driver
    app.state.audit = in_memory_audit
    resp = TestClient(app).post(
        "/agent/query",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers={"X-Auth-User": "analyst"},
    )
    assert resp.status_code == 200
    assert captured[0]["content"] == get_system_prompt("en")
```

- [ ] **Step 2: Run — expect FAIL** (English prompt sent in the de test)
- [ ] **Step 3: Implement** in `chorus/api/routers/agent.py`:
  - line 17: `from chorus.utils.env_cfg import load_agent_env, load_language_env`
  - in the `run_agent(...)` call add: `language=load_language_env().code,`
- [ ] **Step 4: Run — expect PASS**: `uv run pytest tests/agent/test_agent_router.py -q`
- [ ] **Step 5: Commit** — `feat(api): agent router forwards RESPONSE_LANGUAGE`

---

### Task 5: UI string catalog

**Files:**
- Create: `chorus/utils/ui_strings.py`
- Test: `tests/utils/test_ui_strings.py`

> **conftest note:** no `_CHORUS_ENV_MODULES` entry is needed — `ui_string` reads the language live via `load_language_env()` on every call (no import-time env snapshot), so monkeypatching `RESPONSE_LANGUAGE` works without a module reload.

- [ ] **Step 1: Write the failing test** (`tests/utils/test_ui_strings.py`)

```python
"""Tests for the locale-aware UI string catalog."""

from __future__ import annotations

import pytest


def test_keys_match_across_languages() -> None:
    from chorus.utils.env_cfg import SUPPORTED_LANGUAGES
    from chorus.utils.ui_strings import UI_STRINGS

    en_keys = set(UI_STRINGS["en"])
    for lang in SUPPORTED_LANGUAGES:
        assert set(UI_STRINGS[lang]) == en_keys


def test_ui_string_returns_german_under_de(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESPONSE_LANGUAGE", "de")
    from chorus.utils.ui_strings import ui_string

    assert ui_string("posts.no_hits") == "keine Treffer"


def test_ui_string_returns_english_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESPONSE_LANGUAGE", raising=False)
    from chorus.utils.ui_strings import ui_string

    assert ui_string("posts.no_hits") == "no hits"


def test_ui_string_placeholders_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESPONSE_LANGUAGE", "de")
    from chorus.utils.ui_strings import ui_string

    assert ui_string("posts.hits").format(n=3) == "3 Treffer"
```

- [ ] **Step 2: Run — expect FAIL**
- [ ] **Step 3: Implement** `chorus/utils/ui_strings.py` — the module skeleton (parity check + `ui_string`) from the spec §Design 4, populated with the **complete bilingual `UI_STRINGS` table** from the spec's "Full catalog" section (all `common.*`, `landing.*`, `agent.*`, `posts.*`, `author_activity.*`, `topic_cooc.*`, `authors_connected.*`, `authors_mentioning.*`, `network.*` keys). f-string dynamic parts become `{named}` placeholders (`{n}`, `{error}`, `{seed}`, `{label}`, `{count}`, `{authors}`, `{topics}`, `{edges}`, `{tool}`).
- [ ] **Step 4: Run — expect PASS**: `uv run pytest tests/utils/test_ui_strings.py -q`
- [ ] **Step 5: Commit** — `feat(ui): static bilingual caption catalog (ui_strings)`

---

### Task 6: Migrate landing + agent pages

**Files:**
- Modify: `chorus/ui/streamlit_app.py`, `chorus/ui/pages/00_agent.py`

- [ ] **Step 1:** Add `from chorus.utils.ui_strings import ui_string` to each file (after `from chorus.ui.client import ChorusClient`).
- [ ] **Step 2:** Replace each user-facing literal with the catalog key per the mapping in the spec (e.g. `st.caption(ui_string("landing.caption"))`, `st.error(ui_string("common.unreachable").format(error=exc))` (replace both occurrences), `st.info(ui_string("landing.pick_tool"))`; agent: `agent.title`, `agent.caption`, `agent.clear`, `agent.chat_input`, `agent.thinking`, `agent.tool_calls`.format(n=...), `agent.trace_error`.format(tool=..., error=...), `agent.trace_results`.format(count=...), `agent.call_failed`.format(error=...), `agent.no_answer`, `agent.truncated`). Keep brand `st.title("chorus")` and all `set_page_config(page_title=...)` literals.
- [ ] **Step 3: Verify lint + types**

Run: `uv run ruff check chorus/ui && uv run ruff format chorus/ui && uv run mypy chorus`
Expected: clean.

- [ ] **Step 4: Commit** — `feat(ui): localise landing + agent captions`

---

### Task 7: Migrate the 6 tool pages

**Files:**
- Modify: `chorus/ui/pages/01_posts_mentioning.py`, `02_author_activity_summary.py`, `03_topic_co_occurrence.py`, `04_authors_connected_by_topic.py`, `05_authors_mentioning.py`, `06_network_around.py`

- [ ] **Step 1:** Add the `ui_string` import to each page.
- [ ] **Step 2:** Replace literals per the spec mapping. Shared keys: `common.entity_input`, `common.limit`, `common.from_ts`, `common.to_ts`, `common.search`, `common.tool_call_failed` (`.format(error=exc)`), `common.resolution_note` (captions on pages 02/03). Page-specific titles/buttons/empties/counts use their namespaced keys (`posts.*`, `author_activity.*`, `topic_cooc.*`, `authors_connected.*`, `authors_mentioning.*`, `network.*`). Dynamic writes use `.format(...)` (`posts.hits`.format(n=…), `topic_cooc.count`.format(n=…, seed=…), `authors_connected.connected_count`.format(label=…, n=…), `network.counts`.format(n=…, authors=…, topics=…, edges=…), etc.). Data-only subheaders like `f"{label}  ·  {author_id}"` stay as-is.
- [ ] **Step 3: Verify lint + types**: `uv run ruff check chorus/ui && uv run ruff format chorus/ui && uv run mypy chorus`
- [ ] **Step 4: Add the key-usage safety-net test** (`tests/ui/test_ui_strings_usage.py`):

```python
"""Every ui_string('…') key referenced by a UI page must exist in the catalog."""

from __future__ import annotations

import re
from pathlib import Path


def test_every_ui_string_key_used_in_the_ui_exists() -> None:
    from chorus.utils.ui_strings import UI_STRINGS

    ui_dir = Path(__file__).resolve().parents[2] / "chorus" / "ui"
    sources = [ui_dir / "streamlit_app.py", *sorted((ui_dir / "pages").glob("*.py"))]
    pattern = re.compile(r'ui_string\(\s*"([^"]+)"\s*\)')
    used: set[str] = set()
    for src in sources:
        used |= set(pattern.findall(src.read_text(encoding="utf-8")))

    assert used, "expected ui_string(...) usages in the UI"
    missing = sorted(k for k in used if k not in UI_STRINGS["en"])
    assert not missing, f"UI references unknown ui_string keys: {missing}"
```

- [ ] **Step 5: Run — expect PASS**: `uv run pytest tests/ui/test_ui_strings_usage.py -q`
- [ ] **Step 6: Commit** — `feat(ui): localise the six tool-page captions (+ key-usage test)`

---

### Task 8: Compose + `.env.example` + README wiring

**Files:**
- Modify: `docker/compose.yaml` (backend + frontend `environment:`), `.env.example`, `README.md`

- [ ] **Step 1:** In `docker/compose.yaml`, add to **both** services' `environment:` blocks:
  `RESPONSE_LANGUAGE: ${RESPONSE_LANGUAGE:-en}` (backend after `CHORUS_HOME`; frontend after `CHORUS_UI_IDENTITY`). The frontend has no `env_file`, so this explicit entry is required for captions to switch.
- [ ] **Step 2:** Add a commented block to `.env.example` (after the inference model block):

```env
# --- Response language / localisation ---
# RESPONSE_LANGUAGE switches the whole app between English ("en", default)
# and German ("de"). In "de" the agent answers in German and builds entity
# arguments more carefully ("die AfD" -> "AfD"; keeps casing like AfD), and
# the Streamlit UI renders its captions in German. Shared with docint, which
# reads the same variable. Unknown values fall back to "en". Set it here in
# the repo-root .env so docker compose interpolates it into BOTH services.
# RESPONSE_LANGUAGE=de
```

- [ ] **Step 3:** Add a paragraph to `README.md` after the `CHORUS_DEFAULT_IDENTITY` paragraph (before `### 3. Apply migrations`):

```markdown
chorus defaults to English. Set `RESPONSE_LANGUAGE=de` in `.env` to switch
the whole app to German: the agent answers in German and strips leading
articles when building entity queries (`die AfD` → `AfD`), and the Streamlit
UI renders its captions in German. This is the same variable docint reads, so
one setting flips both apps; it must live in this repo-root `.env` because
`docker compose` interpolates it into both the backend and frontend services.
Unknown values fall back to English.
```

- [ ] **Step 4: Verify compose parses**: `docker compose -f docker/compose.yaml config >/dev/null && echo OK`
- [ ] **Step 5: Commit** — `feat(ops): plumb RESPONSE_LANGUAGE to backend + frontend; document it`

---

### Task 9: ADR 0013

**Files:**
- Create: `docs/decisions/0013-response-language-localization.md`

- [ ] **Step 1:** Write the ADR (Status/Date/Context/Decision/Consequences/Alternatives — matching the house style of `0012-durable-alias-norm-key.md`), capturing: env-driven `RESPONSE_LANGUAGE`; static catalog + parallel in-code prompts; prompt-level entity sensitivity. Alternatives rejected: runtime LLM translation, gettext/`.po`, Cypher-level article-insensitive matching, per-request language param. Consequences: caption parity enforced at import; backend+frontend must share the var; API error details / page-tab titles stay English.
- [ ] **Step 2: Commit** — `docs(adr): 0013 response-language localization`

---

### Task 10: Full verification + finish

- [ ] **Step 1:** `uv run ruff check . && uv run ruff format --check .`
- [ ] **Step 2:** `uv run mypy .`
- [ ] **Step 3:** `uv run pytest -q` (full suite, Neo4j testcontainer included)
- [ ] **Step 4 (best-effort manual smoke):** with `RESPONSE_LANGUAGE=de`, start backend + `streamlit run chorus/ui/streamlit_app.py` and eyeball German captions; agent answer language depends on the live model and is not unit-tested.
- [ ] **Step 5:** Push branch; open PR referencing the spec, plan, and ADR 0013. Use superpowers:finishing-a-development-branch.

## Self-review

- **Spec coverage:** env switch (T1), German prompt + sensitivity (T2), loop wiring (T3), router wiring (T4), catalog (T5), UI migration (T6–T7), compose/env/README (T8), ADR (T9), tests throughout, verification (T10). All spec sections map to a task.
- **Type consistency:** `load_language_env().code`, `get_system_prompt(code)`, `ui_string(key)`, and `run_agent(language=...)` names are used identically across tasks.
- **Placeholders:** large reused blocks (German prompt, full catalog) are deferred to the spec by explicit reference, not left as "TODO"; every test and signature is given in full.
