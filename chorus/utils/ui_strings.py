"""Locale-aware user-facing UI strings (Streamlit captions).

The active language follows :func:`chorus.utils.env_cfg.load_language_env`,
driven by the ``RESPONSE_LANGUAGE`` env var. Values may contain ``{name}``
placeholders; callers ``.format(...)`` them. LLM-facing prompts are not handled
here — they live in :mod:`chorus.agent.prompts`. See ADR 0013.

This mirrors docint's ``utils/ui_strings.py`` and lives under ``utils/`` (not
``ui/``) so it imports cleanly in tests without pulling in Streamlit.
"""

from __future__ import annotations

from typing import Final

from chorus.utils.env_cfg import SUPPORTED_LANGUAGES, load_language_env

UI_STRINGS: Final[dict[str, dict[str, str]]] = {
    "en": {
        # common (shared across pages)
        "common.tool_call_failed": "tool call failed: {error}",
        "common.unreachable": "unreachable: {error}",
        "common.entity_input": "Entity name or alias",
        "common.limit": "Limit",
        "common.from_ts": "From (ISO timestamp, optional)",
        "common.to_ts": "To (ISO timestamp, optional)",
        "common.search": "Search",
        "common.resolution_note": (
            "Topics cluster by canonical entity after a resolution pass; on "
            "un-resolved data they show as alias surface forms."
        ),
        # landing (streamlit_app.py)
        "landing.caption": "GraphRAG for social network analysis",
        "landing.backend_health": "backend health",
        "landing.registered_tools": "registered tools",
        "landing.pick_tool": "Pick a tool from the sidebar (left) to explore the graph.",
        "landing.ingestion_on": "Data ingestion is enabled — see the Data ingestion page in the sidebar.",
        "landing.ingestion_off": "Data ingestion is disabled.",
        # agent (00_agent.py)
        "agent.title": "chorus agent",
        "agent.caption": (
            "Ask in plain language; the agent picks the right tools. Topics "
            "cluster by canonical entity after a resolution pass; on un-resolved "
            "data they show as alias surface forms."
        ),
        "agent.clear": "Clear conversation",
        "agent.chat_input": "Ask a question about the network…",
        "agent.thinking": "Thinking…",
        "agent.tool_calls": "Tool calls ({n})",
        "agent.trace_error": "**{tool}** — error: {error}",
        "agent.trace_results": " — {count} result(s)",
        "agent.call_failed": "agent call failed: {error}",
        "agent.no_answer": "(no answer)",
        "agent.truncated": "Stopped at the tool-call limit before reaching a final answer.",
        # posts_mentioning (01)
        "posts.title": "posts mentioning an entity",
        "posts.hits": "{n} hit(s)",
        "posts.no_hits": "no hits",
        # author_activity_summary (02)
        "author_activity.title": "author activity summary",
        "author_activity.author_input": "Author handle or display name",
        "author_activity.summarize": "Summarize",
        "author_activity.matched": "{n} author(s) matched",
        "author_activity.no_topics": "no topics mentioned in range",
        "author_activity.no_author": "no matching author",
        # topic_co_occurrence (03)
        "topic_cooc.title": "topic co-occurrence",
        "topic_cooc.seed_input": "Seed topic (entity or alias)",
        "topic_cooc.find": "Find co-occurring topics",
        "topic_cooc.count": "{n} co-occurring topic(s) with '{seed}'",
        "topic_cooc.none": "no co-occurring topics",
        # authors_connected_by_topic (04)
        "authors_connected.title": "authors connected by topic",
        "authors_connected.caption": (
            "1-hop only. Topics cluster by canonical entity after a resolution "
            "pass; on un-resolved data they show as alias surface forms."
        ),
        "authors_connected.seed_author_input": "Seed author handle or display name",
        "authors_connected.min_overlap": "Minimum shared topics",
        "authors_connected.limit": "Limit (per matched seed)",
        "authors_connected.find": "Find connected authors",
        "authors_connected.no_seed": "no matching seed author",
        "authors_connected.connected_count": "{label}  ·  {n} connected",
        "authors_connected.none": "no connected authors at this overlap threshold",
        # authors_mentioning (05)
        "authors_mentioning.title": "authors mentioning an entity",
        "authors_mentioning.caption": "Authors ranked by how many of their posts mention the entity.",
        "authors_mentioning.count": "{n} author(s)",
        "authors_mentioning.none": "no authors",
        # network_around (06)
        "network.title": "network around an entity",
        "network.caption": (
            "Bipartite author-topic network. depth 1 = the authors who mention "
            "the entity; depth 2 also adds the other topics those authors "
            "mention. The view is capped by the limits below. Topics cluster by "
            "canonical entity once a resolution pass has run; on unresolved data "
            "they are raw alias surface forms."
        ),
        "network.depth": "Depth",
        "network.author_limit": "Author limit",
        "network.topic_limit": "Topic limit (depth 2)",
        "network.build": "Build network",
        "network.empty": "no network — the entity matched nothing",
        "network.counts": "{n} node(s): {authors} author(s), {topics} topic(s); {edges} edge(s)",
        "network.capped": "Capped view — raise the author/topic limits to see more of the network.",
        # social_network_around (08)
        "social.title": "social network around an author",
        "social.caption": (
            "The follows/friends ego network around an author. depth 1 = the author's direct "
            "ties (people they follow, their followers, friends); depth 2 also adds those "
            "neighbours' ties. Follows are drawn with arrowheads, friendships as plain lines. "
            "The view is capped by the limits below."
        ),
        "social.author_input": "Author handle or display name",
        "social.depth": "Depth",
        "social.limit": "Direct-tie limit",
        "social.second_ring_limit": "Second-ring limit (depth 2)",
        "social.build": "Build network",
        "social.empty": "no network — the author matched nothing",
        "social.counts": "{n} author(s); {edges} tie(s): {follows} follows, {friends} friends",
        "social.capped": "Capped view — raise the limits to see more of the network.",
        # data ingestion (07_data_ingestion.py)
        "ingest.title": "Data ingestion",
        "ingest.caption": (
            "Upload table exports and run the pipeline end-to-end: migrate, ingest, resolve. "
            "Large bulk loads still belong on the server via `make ingest`."
        ),
        "ingest.disabled": (
            "Data ingestion via the UI is disabled. Set INGESTION_UI_ENABLED=true on the backend to enable it."
        ),
        "ingest.migrations.header": "Schema migrations",
        "ingest.migrations.pending": "Pending migrations: {versions}",
        "ingest.migrations.apply": "Apply migrations",
        "ingest.migrations.applying": "Applying migrations…",
        "ingest.migrations.applied": "Applied: {versions}",
        "ingest.migrations.uptodate": "Schema is up to date.",
        "ingest.upload.header": "Upload & ingest",
        "ingest.upload.help": (
            "Filenames must match a known table: postings.csv, comments.csv, messages.csv, "
            "profiles.csv, connections.csv (or segmented like 2026-05_postings.csv)."
        ),
        "ingest.upload.label": "CSV table exports",
        "ingest.upload.since": "Only rows newer than (ISO timestamp, optional)",
        "ingest.upload.then_resolve": "Run resolution after ingestion",
        "ingest.upload.start": "Start ingestion",
        "ingest.job.running": "Ingestion running… this page refreshes automatically.",
        "ingest.job.done": "Ingestion complete.",
        "ingest.job.failed": "Ingestion failed: {error}",
        "ingest.counts.header": "Rows ingested",
        "ingest.counts.dropped": "Dropped malformed rows: {detail}",
        "ingest.counts.filtered": "Filtered rows (no in-batch parent / no edge signal): {detail}",
        "ingest.counts.skipped": "Skipped stages: {detail}",
        "ingest.resolve.header": "Entity resolution",
        "ingest.resolve.start": "Run resolution",
        "ingest.resolve.running": "Resolution running… this page refreshes automatically.",
        "ingest.resolve.done": "Resolution complete.",
        "ingest.resolve.failed": "Resolution failed: {error}",
        "ingest.resolve.summary": "Resolution summary",
        "ingest.error.request": "Request rejected: {detail}",
    },
    "de": {
        # common (shared across pages)
        "common.tool_call_failed": "Werkzeugaufruf fehlgeschlagen: {error}",
        "common.unreachable": "nicht erreichbar: {error}",
        "common.entity_input": "Name oder Alias der Entität",
        "common.limit": "Limit",
        "common.from_ts": "Von (ISO-Zeitstempel, optional)",
        "common.to_ts": "Bis (ISO-Zeitstempel, optional)",
        "common.search": "Suchen",
        "common.resolution_note": (
            "Themen werden nach einem Auflösungslauf nach kanonischer Entität "
            "gruppiert; auf nicht aufgelösten Daten erscheinen sie als "
            "Alias-Oberflächenformen."
        ),
        # landing (streamlit_app.py)
        "landing.caption": "GraphRAG für die Analyse sozialer Netzwerke",
        "landing.backend_health": "Backend-Status",
        "landing.registered_tools": "Registrierte Werkzeuge",
        "landing.pick_tool": "Wähle links in der Seitenleiste ein Werkzeug, um den Graphen zu erkunden.",
        "landing.ingestion_on": "Datenimport ist aktiviert — siehe die Seite „Datenimport“ in der Seitenleiste.",
        "landing.ingestion_off": "Datenimport ist deaktiviert.",
        # agent (00_agent.py)
        "agent.title": "chorus Agent",
        "agent.caption": (
            "Frage in natürlicher Sprache; der Agent wählt die passenden "
            "Werkzeuge. Themen werden nach einem Auflösungslauf nach kanonischer "
            "Entität gruppiert; auf nicht aufgelösten Daten erscheinen sie als "
            "Alias-Oberflächenformen."
        ),
        "agent.clear": "Unterhaltung löschen",
        "agent.chat_input": "Stelle eine Frage zum Netzwerk…",
        "agent.thinking": "Denkt nach…",
        "agent.tool_calls": "Werkzeugaufrufe ({n})",
        "agent.trace_error": "**{tool}** — Fehler: {error}",
        "agent.trace_results": " — {count} Ergebnis(se)",
        "agent.call_failed": "Agentenaufruf fehlgeschlagen: {error}",
        "agent.no_answer": "(keine Antwort)",
        "agent.truncated": "Beim Werkzeugaufruf-Limit gestoppt, bevor eine endgültige Antwort erreicht wurde.",
        # posts_mentioning (01)
        "posts.title": "Beiträge, die eine Entität erwähnen",
        "posts.hits": "{n} Treffer",
        "posts.no_hits": "keine Treffer",
        # author_activity_summary (02)
        "author_activity.title": "Zusammenfassung der Autorenaktivität",
        "author_activity.author_input": "Handle oder Anzeigename des Autors",
        "author_activity.summarize": "Zusammenfassen",
        "author_activity.matched": "{n} Autor(en) gefunden",
        "author_activity.no_topics": "keine Themen im Zeitraum erwähnt",
        "author_activity.no_author": "kein passender Autor",
        # topic_co_occurrence (03)
        "topic_cooc.title": "Themen-Kookkurrenz",
        "topic_cooc.seed_input": "Ausgangsthema (Entität oder Alias)",
        "topic_cooc.find": "Kookkurrierende Themen finden",
        "topic_cooc.count": "{n} kookkurrierende(s) Thema/Themen mit „{seed}“",
        "topic_cooc.none": "keine kookkurrierenden Themen",
        # authors_connected_by_topic (04)
        "authors_connected.title": "Über Themen verbundene Autoren",
        "authors_connected.caption": (
            "Nur 1 Hop. Themen werden nach einem Auflösungslauf nach kanonischer "
            "Entität gruppiert; auf nicht aufgelösten Daten erscheinen sie als "
            "Alias-Oberflächenformen."
        ),
        "authors_connected.seed_author_input": "Handle oder Anzeigename des Ausgangsautors",
        "authors_connected.min_overlap": "Mindestanzahl gemeinsamer Themen",
        "authors_connected.limit": "Limit (pro gefundenem Ausgangspunkt)",
        "authors_connected.find": "Verbundene Autoren finden",
        "authors_connected.no_seed": "kein passender Ausgangsautor",
        "authors_connected.connected_count": "{label}  ·  {n} verbunden",
        "authors_connected.none": "keine verbundenen Autoren bei dieser Überschneidungsschwelle",
        # authors_mentioning (05)
        "authors_mentioning.title": "Autoren, die eine Entität erwähnen",
        "authors_mentioning.caption": "Autoren, gereiht nach der Anzahl ihrer Beiträge, die die Entität erwähnen.",
        "authors_mentioning.count": "{n} Autor(en)",
        "authors_mentioning.none": "keine Autoren",
        # network_around (06)
        "network.title": "Netzwerk rund um eine Entität",
        "network.caption": (
            "Bipartites Autoren-Themen-Netzwerk. Tiefe 1 = die Autoren, die die "
            "Entität erwähnen; Tiefe 2 ergänzt zusätzlich die weiteren Themen, "
            "die diese erwähnen. Die Ansicht wird durch die untenstehenden Limits "
            "begrenzt. Themen werden nach einem Auflösungslauf nach kanonischer "
            "Entität gruppiert; auf nicht aufgelösten Daten sind es rohe "
            "Alias-Oberflächenformen."
        ),
        "network.depth": "Tiefe",
        "network.author_limit": "Autoren-Limit",
        "network.topic_limit": "Themen-Limit (Tiefe 2)",
        "network.build": "Netzwerk aufbauen",
        "network.empty": "kein Netzwerk — die Entität ergab keine Treffer",
        "network.counts": "{n} Knoten: {authors} Autor(en), {topics} Thema/Themen; {edges} Kante(n)",
        "network.capped": "Begrenzte Ansicht — erhöhe die Autoren-/Themen-Limits, um mehr des Netzwerks zu sehen.",
        # social_network_around (08)
        "social.title": "Soziales Netzwerk rund um einen Autor",
        "social.caption": (
            "Das Follows-/Freundschafts-Ego-Netzwerk rund um einen Autor. Tiefe 1 = die "
            "direkten Verbindungen des Autors (wem er folgt, seine Follower, Freunde); Tiefe 2 "
            "ergänzt zusätzlich deren Verbindungen. Follows werden mit Pfeilspitzen gezeichnet, "
            "Freundschaften als einfache Linien. Die Ansicht wird durch die untenstehenden "
            "Limits begrenzt."
        ),
        "social.author_input": "Handle oder Anzeigename des Autors",
        "social.depth": "Tiefe",
        "social.limit": "Limit direkter Verbindungen",
        "social.second_ring_limit": "Limit des zweiten Rings (Tiefe 2)",
        "social.build": "Netzwerk aufbauen",
        "social.empty": "kein Netzwerk — der Autor ergab keine Treffer",
        "social.counts": "{n} Autor(en); {edges} Verbindung(en): {follows} Follows, {friends} Freundschaften",
        "social.capped": "Begrenzte Ansicht — erhöhe die Limits, um mehr des Netzwerks zu sehen.",
        # data ingestion (07_data_ingestion.py)
        "ingest.title": "Datenimport",
        "ingest.caption": (
            "Tabellen-Exporte hochladen und die Pipeline durchgängig ausführen: migrieren, "
            "importieren, auflösen. Große Massenimporte gehören weiterhin per `make ingest` auf den Server."
        ),
        "ingest.disabled": (
            "Datenimport über die Oberfläche ist deaktiviert. "
            "Setze INGESTION_UI_ENABLED=true im Backend, um ihn zu aktivieren."
        ),
        "ingest.migrations.header": "Schema-Migrationen",
        "ingest.migrations.pending": "Ausstehende Migrationen: {versions}",
        "ingest.migrations.apply": "Migrationen anwenden",
        "ingest.migrations.applying": "Migrationen werden angewendet…",
        "ingest.migrations.applied": "Angewendet: {versions}",
        "ingest.migrations.uptodate": "Schema ist aktuell.",
        "ingest.upload.header": "Hochladen & importieren",
        "ingest.upload.help": (
            "Dateinamen müssen einer bekannten Tabelle entsprechen: postings.csv, comments.csv, "
            "messages.csv, profiles.csv, connections.csv (oder segmentiert wie 2026-05_postings.csv)."
        ),
        "ingest.upload.label": "CSV-Tabellen-Exporte",
        "ingest.upload.since": "Nur Zeilen neuer als (ISO-Zeitstempel, optional)",
        "ingest.upload.then_resolve": "Auflösung nach dem Import ausführen",
        "ingest.upload.start": "Import starten",
        "ingest.job.running": "Import läuft… diese Seite aktualisiert sich automatisch.",
        "ingest.job.done": "Import abgeschlossen.",
        "ingest.job.failed": "Import fehlgeschlagen: {error}",
        "ingest.counts.header": "Importierte Zeilen",
        "ingest.counts.dropped": "Verworfene fehlerhafte Zeilen: {detail}",
        "ingest.counts.filtered": (
            "Gefilterte Zeilen (kein übergeordneter Beitrag im Batch / kein Kantensignal): {detail}"
        ),
        "ingest.counts.skipped": "Übersprungene Stufen: {detail}",
        "ingest.resolve.header": "Entitätsauflösung",
        "ingest.resolve.start": "Auflösung ausführen",
        "ingest.resolve.running": "Auflösung läuft… diese Seite aktualisiert sich automatisch.",
        "ingest.resolve.done": "Auflösung abgeschlossen.",
        "ingest.resolve.failed": "Auflösung fehlgeschlagen: {error}",
        "ingest.resolve.summary": "Zusammenfassung der Auflösung",
        "ingest.error.request": "Anfrage abgelehnt: {detail}",
    },
}

# Sanity-check at import time so a missing translation surfaces as a clear
# startup error rather than a KeyError deep inside a page render.
_EN_KEYS = set(UI_STRINGS["en"])
for _lang in SUPPORTED_LANGUAGES:
    if set(UI_STRINGS[_lang]) != _EN_KEYS:
        missing = _EN_KEYS.symmetric_difference(UI_STRINGS[_lang])
        raise RuntimeError(f"UI_STRINGS for '{_lang}' diverges from 'en' on: {sorted(missing)}")


def ui_string(key: str) -> str:
    """Return the UI string for ``key`` in the currently configured language.

    Args:
        key: Name of the UI string (see :data:`UI_STRINGS`).

    Returns:
        The localized string, possibly containing ``{name}`` placeholders for
        the caller to ``.format(...)``.

    Raises:
        KeyError: If ``key`` is not registered in :data:`UI_STRINGS`.
    """
    return UI_STRINGS[load_language_env().code][key]
