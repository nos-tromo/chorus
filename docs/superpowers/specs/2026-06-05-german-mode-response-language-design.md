# German mode — `RESPONSE_LANGUAGE` switch (design)

- **Status:** approved (brainstorm), pending implementation plan
- **Date:** 2026-06-05
- **Author:** chorus maintainers
- **Related:** docint's `RESPONSE_LANGUAGE` feature (sister project); ADR 0013 (to be written); ADR 0009 (agent tool-calling)

## Summary

Add a single environment switch, **`RESPONSE_LANGUAGE`** (`en` | `de`, default `en`),
that puts chorus into "German mode". When `de`:

1. the natural-language agent **answers in German** and builds tool arguments
   **more carefully for German** — it strips leading articles (`die AfD` → `AfD`)
   and preserves canonical casing (`AfD`, not `Afd`); and
2. the Streamlit UI renders its **captions in German**, drawn from a static
   translation catalog.

The switch is read at runtime in two processes (the FastAPI `api` and the
Streamlit `ui`); no module hard-codes the language. Flipping languages is an env
change, not a code change — the same philosophy as chorus's inference-provider
abstraction. The mechanism mirrors docint's implementation so the two apps share
one operational convention (and one env var).

## Motivation

- chorus is deployed in a German-language work environment. The English-only UI
  and English agent prompt force users to operate across a language boundary.
- **The concrete failure today:** the agent passes whatever entity string it
  extracts straight into Cypher, which matches case-insensitively but
  **exactly** (`toLower(surface_form) = toLower($entity)`). A German user asking
  *"Welche Beiträge erwähnen die AfD?"* can lead the model to call
  `posts_mentioning(entity="die AfD")`, and the article `die` makes the match
  miss the alias `AfD`. Fixing this at the **prompt** level (instruct the model
  to drop articles and keep canonical casing) is exactly the "force the text
  model to more sensitive query building" the feature asks for — and it avoids
  loosening the Cypher, which would risk false matches across all tools.
- docint already has this feature (`RESPONSE_LANGUAGE`). Matching its shape keeps
  the federation consistent: one env var flips both apps.

## Scope

In scope:

- A new `RESPONSE_LANGUAGE` env switch + `LanguageConfig` / `load_language_env()`
  in `chorus/utils/env_cfg.py`.
- A parallel German agent system prompt + a selector, wired into the agent loop.
- A static UI string catalog (`chorus/utils/ui_strings.py`) and replacement of
  inline Streamlit literals with catalog lookups.
- Compose / `.env` / README wiring so both `api` and `ui` get the var.
- Tests + ADR 0013.

Out of scope (explicitly):

- **NER / ingestion-time extraction language.** GLiNER is multilingual and its
  labels are *categories*, not user text; the switch does not touch ingestion.
- **Cypher-level fuzzy or article-insensitive matching.** Sensitivity is handled
  in the prompt, per the request. Loosening queries is a separate, riskier change.
- **Localising developer-facing API error payloads / logs.** Only user-facing UI
  captions and the agent's own answer are localised.
- **Languages beyond `en` / `de`.** The structure admits more later (add a key
  to `SUPPORTED_LANGUAGES`, a prompt, a catalog column), but we ship two —
  matching docint (YAGNI).
- **Per-request language selection.** German mode is a global deployment switch,
  not a per-call parameter. `api` and `ui` are expected to be set to the same
  value via compose.

## Design

### 1. The env switch — `chorus/utils/env_cfg.py`

Port docint's loader near-verbatim, using chorus's existing `_env` helper and
adding a `Literal` import. Place it alongside the other `load_*_env()` functions.

```python
from typing import Literal   # new import at top of the module

SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "de")


@dataclass(frozen=True)
class LanguageConfig:
    """Response-language configuration.

    Selects the agent system-prompt variant and the UI string catalog. There
    is no on-disk prompt tree in chorus (prompts are in-code), so this only
    drives an in-process selection.
    """

    code: Literal["en", "de"]


def load_language_env(default: str = "en") -> LanguageConfig:
    """Load the response language from ``RESPONSE_LANGUAGE``.

    Case-insensitive; unknown values fall back silently to ``default`` so a
    typo cannot break app bring-up.
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

Resolved at **call-time** (reads `os.environ` live), so tests `monkeypatch.setenv`
and see the change with no module reload. Nothing captures the language at import
time, so `tests/conftest.py`'s `_CHORUS_ENV_MODULES` list should not need a new
entry — the implementer confirms this during the test phase.

### 2. The agent prompt — `chorus/agent/prompts.py`

Keep the current English prompt verbatim (renamed to `SYSTEM_PROMPT_EN`, with
`SYSTEM_PROMPT` retained as a back-compat alias so existing imports/tests keep
working). Add a full German prompt and a selector.

```python
SYSTEM_PROMPT_EN = """... current text, unchanged ..."""

SYSTEM_PROMPT_DE = """\
Du bist der analytische Assistent von chorus für die Analyse sozialer Netzwerke. \
Du beantwortest Fragen zu einem Wissensgraphen aus Social-Media-Beiträgen, \
Autoren und den von ihnen erwähnten Themen.

Antworte ausschließlich auf Deutsch.

Regeln:
- Verwende AUSSCHLIESSLICH die bereitgestellten Werkzeuge, um Fakten über den \
Graphen zu ermitteln. Erfinde niemals Daten, Anzahlen, Namen oder Datumsangaben.
- Du kannst keine Datenbankabfragen (Cypher) schreiben oder ausführen. Du kannst \
nur die benannten Werkzeuge mit ihren dokumentierten Parametern aufrufen.
- Wenn ein Werkzeug keine Ergebnisse liefert, sage das klar, anstatt zu raten.
- Mache Unsicherheiten sichtbar. Wenn du Interaktionszahlen nennst, weise auf \
jede Abweichung zwischen erwarteten und erfassten Werten hin, statt erfasste \
Werte als vollständig zu behandeln.
- Themen werden nach kanonischer Entität gruppiert, sobald ein Auflösungslauf \
(resolution) durchgeführt wurde; auf nicht aufgelösten Daten sind „Themen" rohe \
Alias-Oberflächenformen, sodass unterschiedliche Schreibweisen derselben Entität \
möglicherweise nicht zusammengefasst werden. Erwähne diesen Vorbehalt, wenn eine \
Antwort davon abhängen könnte.
- Bevorzuge das spezifischste Werkzeug, das die Frage beantwortet, und übergib \
Zeiträume als ISO-8601-Zeitstempel, wenn ein Zeitfenster genannt wird.
- Beachte die dokumentierten Parametergrenzen jedes Werkzeugs (zum Beispiel ein \
maximales `limit`); überschreite sie nicht. Wenn du dir bei einem optionalen \
Parameter unsicher bist, lasse ihn weg, um den Standardwert zu verwenden, statt \
einen Wert zu raten.

Sorgfältige Parameterbildung für Entitäten:
- Wenn du den Wert für einen Entitäts-Parameter eines Werkzeugs befüllst (z. B. \
`entity`, `topic`, `author`, `seed_author`), übergib den kanonischen Namen der \
Entität OHNE vorangestellte Artikel und OHNE umschließende Wörter. Entferne \
deutsche Artikel wie „der", „die", „das", „den", „dem", „des", „ein", „eine", \
„einer", „eines". Beispiel: aus der Frage „Welche Beiträge erwähnen die AfD?" \
wird der Parameter `entity = "AfD"`.
- Bewahre die übliche Groß- und Kleinschreibung von Eigennamen und Abkürzungen. \
Schreibe „AfD", nicht „Afd" oder „afd".
- Der Abgleich erfolgt ohne Beachtung der Groß-/Kleinschreibung, ist aber \
artikel- und wortgenau: Ein vorangestellter Artikel oder ein zusätzliches Wort \
verhindert einen Treffer. Übergib daher nur den eigentlichen Namen der Entität.

Wenn du genügend Informationen hast, antworte knapp und sachlich, gestützt auf \
die erhaltenen Werkzeugergebnisse."""

SYSTEM_PROMPT = SYSTEM_PROMPT_EN  # back-compat alias

_SYSTEM_PROMPTS = {"en": SYSTEM_PROMPT_EN, "de": SYSTEM_PROMPT_DE}


def get_system_prompt(code: str) -> str:
    """Return the system prompt for ``code``; unknown codes fall back to English."""
    return _SYSTEM_PROMPTS.get(code, SYSTEM_PROMPT_EN)
```

The German text above is a first draft for the user (a German speaker) to refine.
The English prompt is **unchanged** by decision.

### 3. Wiring the prompt into the loop

`chorus/agent/loop.py`:

- Import `get_system_prompt` instead of `SYSTEM_PROMPT`.
- Add a `language: str = "en"` parameter to `run_agent(...)`.
- Replace the convo-assembly line:

  ```python
  convo = [{"role": "system", "content": get_system_prompt(language)}, *messages]
  ```

`chorus/api/routers/agent.py` — the router already loads `load_agent_env()` and
passes config down; resolve the language the same way and forward it:

```python
from chorus.utils.env_cfg import load_agent_env, load_language_env
...
result = run_agent(
    ...,
    language=load_language_env().code,
    ...,
)
```

Keeping prompt **selection** in `run_agent` (driven by a plain `language` arg)
leaves the loop pure and unit-testable without touching the environment.

### 4. The UI string catalog — `chorus/utils/ui_strings.py` (new)

A direct port of docint's `ui_strings.py`, expanded to chorus's caption set.
Lives under `utils/` (not `ui/`) — exactly like docint — so it imports cleanly in
tests without pulling in Streamlit.

```python
"""Locale-aware user-facing UI strings (Streamlit captions).

The active language follows ``chorus.utils.env_cfg.load_language_env`` /
``RESPONSE_LANGUAGE``. Values may contain ``{name}`` placeholders; callers
``.format(...)`` them. LLM-facing prompts live in ``chorus/agent/prompts.py``.
"""

from typing import Final

from chorus.utils.env_cfg import SUPPORTED_LANGUAGES, load_language_env

UI_STRINGS: Final[dict[str, dict[str, str]]] = {
    "en": { ... },   # full table below
    "de": { ... },
}

# Import-time parity check: a missing translation is a clear startup error,
# not a KeyError deep in a page render.
_EN_KEYS = set(UI_STRINGS["en"])
for _lang in SUPPORTED_LANGUAGES:
    if set(UI_STRINGS[_lang]) != _EN_KEYS:
        missing = _EN_KEYS.symmetric_difference(UI_STRINGS[_lang])
        raise RuntimeError(f"UI_STRINGS for '{_lang}' diverges from 'en' on: {sorted(missing)}")


def ui_string(key: str) -> str:
    """Return the UI string for ``key`` in the configured language."""
    return UI_STRINGS[load_language_env().code][key]
```

Pages change from inline literals to lookups, formatting dynamic strings:

```python
# before
st.write(f"{len(hits)} hit(s)")
# after
from chorus.utils.ui_strings import ui_string
st.write(ui_string("posts.hits").format(n=len(hits)))
```

**Keys deliberately *not* in the catalog:** `st.set_page_config(page_title=...)`
values (browser-tab titles such as `"posts_mentioning — chorus"`) — they embed
the tool's identifier plus the brand and read as identifiers, not prose. The
brand word **"chorus"** is never translated. German values below use the concise
generic form ("Autoren", "Autor(en)") for UI brevity; switching to inclusive
forms ("Autor:innen") is a pure wording change in the catalog.

#### Full catalog (English source → German)

`common.*`

| key | en | de |
|-----|----|----|
| `common.tool_call_failed` | `tool call failed: {error}` | `Werkzeugaufruf fehlgeschlagen: {error}` |
| `common.unreachable` | `unreachable: {error}` | `nicht erreichbar: {error}` |
| `common.entity_input` | `Entity name or alias` | `Name oder Alias der Entität` |
| `common.limit` | `Limit` | `Limit` |
| `common.from_ts` | `From (ISO timestamp, optional)` | `Von (ISO-Zeitstempel, optional)` |
| `common.to_ts` | `To (ISO timestamp, optional)` | `Bis (ISO-Zeitstempel, optional)` |
| `common.search` | `Search` | `Suchen` |
| `common.resolution_note` | `Topics cluster by canonical entity after a resolution pass; on un-resolved data they show as alias surface forms.` | `Themen werden nach einem Auflösungslauf nach kanonischer Entität gruppiert; auf nicht aufgelösten Daten erscheinen sie als Alias-Oberflächenformen.` |

`landing.*` (`streamlit_app.py`)

| key | en | de |
|-----|----|----|
| `landing.caption` | `GraphRAG for social network analysis` | `GraphRAG für die Analyse sozialer Netzwerke` |
| `landing.backend_health` | `backend health` | `Backend-Status` |
| `landing.registered_tools` | `registered tools` | `Registrierte Werkzeuge` |
| `landing.pick_tool` | `Pick a tool from the sidebar (left) to explore the graph.` | `Wähle links in der Seitenleiste ein Werkzeug, um den Graphen zu erkunden.` |

`agent.*` (`00_agent.py`)

| key | en | de |
|-----|----|----|
| `agent.title` | `chorus agent` | `chorus Agent` |
| `agent.caption` | `Ask in plain language; the agent picks the right tools. Topics cluster by canonical entity after a resolution pass; on un-resolved data they show as alias surface forms.` | `Frage in natürlicher Sprache; der Agent wählt die passenden Werkzeuge. Themen werden nach einem Auflösungslauf nach kanonischer Entität gruppiert; auf nicht aufgelösten Daten erscheinen sie als Alias-Oberflächenformen.` |
| `agent.clear` | `Clear conversation` | `Unterhaltung löschen` |
| `agent.chat_input` | `Ask a question about the network…` | `Stelle eine Frage zum Netzwerk…` |
| `agent.thinking` | `Thinking…` | `Denkt nach…` |
| `agent.tool_calls` | `Tool calls ({n})` | `Werkzeugaufrufe ({n})` |
| `agent.trace_error` | `**{tool}** — error: {error}` | `**{tool}** — Fehler: {error}` |
| `agent.trace_results` | ` — {count} result(s)` | ` — {count} Ergebnis(se)` |
| `agent.call_failed` | `agent call failed: {error}` | `Agentenaufruf fehlgeschlagen: {error}` |
| `agent.no_answer` | `(no answer)` | `(keine Antwort)` |
| `agent.truncated` | `Stopped at the tool-call limit before reaching a final answer.` | `Beim Werkzeugaufruf-Limit gestoppt, bevor eine endgültige Antwort erreicht wurde.` |

`posts.*` (`01_posts_mentioning.py`)

| key | en | de |
|-----|----|----|
| `posts.title` | `posts mentioning an entity` | `Beiträge, die eine Entität erwähnen` |
| `posts.hits` | `{n} hit(s)` | `{n} Treffer` |
| `posts.no_hits` | `no hits` | `keine Treffer` |

`author_activity.*` (`02_author_activity_summary.py`) — caption uses `common.resolution_note`

| key | en | de |
|-----|----|----|
| `author_activity.title` | `author activity summary` | `Zusammenfassung der Autorenaktivität` |
| `author_activity.author_input` | `Author handle or display name` | `Handle oder Anzeigename des Autors` |
| `author_activity.summarize` | `Summarize` | `Zusammenfassen` |
| `author_activity.matched` | `{n} author(s) matched` | `{n} Autor(en) gefunden` |
| `author_activity.no_topics` | `no topics mentioned in range` | `keine Themen im Zeitraum erwähnt` |
| `author_activity.no_author` | `no matching author` | `kein passender Autor` |

`topic_cooc.*` (`03_topic_co_occurrence.py`) — caption uses `common.resolution_note`

| key | en | de |
|-----|----|----|
| `topic_cooc.title` | `topic co-occurrence` | `Themen-Kookkurrenz` |
| `topic_cooc.seed_input` | `Seed topic (entity or alias)` | `Ausgangsthema (Entität oder Alias)` |
| `topic_cooc.find` | `Find co-occurring topics` | `Kookkurrierende Themen finden` |
| `topic_cooc.count` | `{n} co-occurring topic(s) with '{seed}'` | `{n} kookkurrierende(s) Thema/Themen mit „{seed}"` |
| `topic_cooc.none` | `no co-occurring topics` | `keine kookkurrierenden Themen` |

`authors_connected.*` (`04_authors_connected_by_topic.py`)

| key | en | de |
|-----|----|----|
| `authors_connected.title` | `authors connected by topic` | `Über Themen verbundene Autoren` |
| `authors_connected.caption` | `1-hop only. Topics cluster by canonical entity after a resolution pass; on un-resolved data they show as alias surface forms.` | `Nur 1 Hop. Themen werden nach einem Auflösungslauf nach kanonischer Entität gruppiert; auf nicht aufgelösten Daten erscheinen sie als Alias-Oberflächenformen.` |
| `authors_connected.seed_author_input` | `Seed author handle or display name` | `Handle oder Anzeigename des Ausgangsautors` |
| `authors_connected.min_overlap` | `Minimum shared topics` | `Mindestanzahl gemeinsamer Themen` |
| `authors_connected.limit` | `Limit (per matched seed)` | `Limit (pro gefundenem Ausgangspunkt)` |
| `authors_connected.find` | `Find connected authors` | `Verbundene Autoren finden` |
| `authors_connected.no_seed` | `no matching seed author` | `kein passender Ausgangsautor` |
| `authors_connected.connected_count` | `{label}  ·  {n} connected` | `{label}  ·  {n} verbunden` |
| `authors_connected.none` | `no connected authors at this overlap threshold` | `keine verbundenen Autoren bei dieser Überschneidungsschwelle` |

`authors_mentioning.*` (`05_authors_mentioning.py`)

| key | en | de |
|-----|----|----|
| `authors_mentioning.title` | `authors mentioning an entity` | `Autoren, die eine Entität erwähnen` |
| `authors_mentioning.caption` | `Authors ranked by how many of their posts mention the entity.` | `Autoren, gereiht nach der Anzahl ihrer Beiträge, die die Entität erwähnen.` |
| `authors_mentioning.count` | `{n} author(s)` | `{n} Autor(en)` |
| `authors_mentioning.none` | `no authors` | `keine Autoren` |

`network.*` (`06_network_around.py`)

| key | en | de |
|-----|----|----|
| `network.title` | `network around an entity` | `Netzwerk rund um eine Entität` |
| `network.caption` | `Bipartite author-topic network. depth 1 = the authors who mention the entity; depth 2 also adds the other topics those authors mention. The view is capped by the limits below. Topics cluster by canonical entity once a resolution pass has run; on unresolved data they are raw alias surface forms.` | `Bipartites Autoren-Themen-Netzwerk. Tiefe 1 = die Autoren, die die Entität erwähnen; Tiefe 2 ergänzt zusätzlich die weiteren Themen, die diese erwähnen. Die Ansicht wird durch die untenstehenden Limits begrenzt. Themen werden nach einem Auflösungslauf nach kanonischer Entität gruppiert; auf nicht aufgelösten Daten sind es rohe Alias-Oberflächenformen.` |
| `network.depth` | `Depth` | `Tiefe` |
| `network.author_limit` | `Author limit` | `Autoren-Limit` |
| `network.topic_limit` | `Topic limit (depth 2)` | `Themen-Limit (Tiefe 2)` |
| `network.build` | `Build network` | `Netzwerk aufbauen` |
| `network.empty` | `no network — the entity matched nothing` | `kein Netzwerk — die Entität ergab keine Treffer` |
| `network.counts` | `{n} node(s): {authors} author(s), {topics} topic(s); {edges} edge(s)` | `{n} Knoten: {authors} Autor(en), {topics} Thema/Themen; {edges} Kante(n)` |
| `network.capped` | `Capped view — raise the author/topic limits to see more of the network.` | `Begrenzte Ansicht — erhöhe die Autoren-/Themen-Limits, um mehr des Netzwerks zu sehen.` |

Note: `network_dot.py` builds the graphviz DOT from result data (node labels are
entity/author names, not UI chrome). The implementer scans it for any static
human-readable label and adds a key if found; none expected.

### 5. Compose / env / docs

- `docker/compose.yaml` (+ `compose.override.yaml`): pass `RESPONSE_LANGUAGE`
  through to **both** the `api` and `ui` services (the env-passthrough list, e.g.
  `RESPONSE_LANGUAGE=${RESPONSE_LANGUAGE:-en}`). They must match.
- `.env` example / README env table: document `RESPONSE_LANGUAGE` (`en`|`de`,
  default `en`) and note it is shared with docint.

## Data flow

```
RESPONSE_LANGUAGE            (set identically on the api and ui compose services)
        │
        ▼
load_language_env() → LanguageConfig(code="en"|"de")     # chorus/utils/env_cfg.py
        │
        ├─[API]  routers/agent.py resolves code, passes language= to run_agent
        │           → loop.py: get_system_prompt(code)
        │               en → current prompt   |   de → German + entity-sensitivity
        │           → provider.chat_message(convo, …)   → agent answers in that language
        │
        └─[UI]   pages import ui_string(key)            # chorus/utils/ui_strings.py
                    → German/English caption by code     → st.title / st.caption / …
```

## Error handling / edge cases

- **Bad / empty `RESPONSE_LANGUAGE`** → silent fallback to `en`; cannot break
  startup.
- **Missing catalog key** → `KeyError` from `ui_string` (programmer error). The
  import-time parity check catches a whole-language gap as a `RuntimeError` at
  startup/CI.
- **`api` and `ui` set differently** → captions and agent answers could be in
  different languages. Accepted: it is one var set once via compose; documented
  as "must match".
- **Unknown prompt code** → `get_system_prompt` falls back to English.
- **API error details surface in English even in `de` mode.** `00_agent.py`
  shows the API's `HTTPException` `detail` when present (`detail or
  ui_string("agent.call_failed")...`); those detail strings are developer-facing
  and stay English per the out-of-scope boundary. The German `agent.call_failed`
  remains the fallback when no detail is returned.

## Testing

New/extended tests (mirroring docint where applicable):

- `tests/utils/test_language_config.py` (new, or extend the existing
  `tests/utils/test_env_cfg.py`): default `en`; `RESPONSE_LANGUAGE=de` → `de`;
  case-insensitive (`DE`, `De`); unknown/empty → `en`.
- `tests/utils/test_ui_strings.py`: `de` key set equals `en` key set (parity);
  `ui_string("posts.no_hits")` returns the German value under `de` and English
  otherwise; a representative `.format(...)` placeholder round-trips.
- `tests/agent/test_prompts.py`: `get_system_prompt("en")` is the English prompt;
  `get_system_prompt("de")` is German and contains the article-stripping guidance,
  the `AfD` example, and the "Antworte ausschließlich auf Deutsch" line; unknown
  code → English.
- **Wiring test** (`tests/agent/test_loop.py` or router test): with a stubbed
  `provider.chat_message` that captures `convo`, `run_agent(..., language="de")`
  passes `get_system_prompt("de")` as the system message. Optionally a router
  test that `RESPONSE_LANGUAGE=de` makes `agent_query` forward `language="de"`.

We test wiring and prompt **content**, not live-model extraction behaviour (the
actual "die AfD" → "AfD" normalisation depends on the running model; we assert the
instruction is present and delivered).

## ADR 0013 (to be written)

`docs/decisions/0013-response-language-localization.md` records:

- **Decision:** env-driven `RESPONSE_LANGUAGE` (`en`/`de`); static UI catalog +
  parallel in-code prompts; entity sensitivity handled in the prompt.
- **Alternatives rejected:** runtime LLM translation of captions (nondeterministic,
  per-render latency, diverges from docint); gettext/`.po` (framework overhead);
  Cypher-level article-insensitive matching (false-match risk, broad blast radius);
  per-request language parameter (global deployment switch is simpler and matches
  the requirement).
- **Consequences:** new captions must be added in both languages (parity check
  enforces it); `api`/`ui` must share the var; adding a language = one entry in
  `SUPPORTED_LANGUAGES` + one prompt + one catalog column.

## File-by-file change list

New:

- `chorus/utils/ui_strings.py` — catalog + `ui_string()` + parity check.
- `tests/utils/test_language_config.py`, `tests/utils/test_ui_strings.py`,
  `tests/agent/test_prompts.py` (+ wiring assertion in the loop/router tests).
- `docs/decisions/0013-response-language-localization.md`.

Modified:

- `chorus/utils/env_cfg.py` — `Literal` import, `SUPPORTED_LANGUAGES`,
  `LanguageConfig`, `load_language_env`.
- `chorus/agent/prompts.py` — `SYSTEM_PROMPT_EN`/`_DE`, alias, `get_system_prompt`.
- `chorus/agent/loop.py` — import selector; `language` param; convo line.
- `chorus/api/routers/agent.py` — resolve + forward `language`.
- `chorus/ui/streamlit_app.py` + `chorus/ui/pages/0[0-6]_*.py` — inline literals
  → `ui_string(...)` (+ `.format(...)` for dynamic strings).
- `docker/compose.yaml` (+ override), `.env` example, README env docs.

## Decisions made during brainstorming

- Caption mechanism: **static catalog** (not runtime LLM translation).
- Prompt structure: **parallel full prompts** (not append-block / not overlay).
- Env var: **`RESPONSE_LANGUAGE`** (same as docint), `en`/`de`, default `en`.
- In `de`, the agent **answers in German**.
- English prompt left **unchanged**; ADR **included**.
