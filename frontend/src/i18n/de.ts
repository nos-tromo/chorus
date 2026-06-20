import type { Strings } from './index'

export const de: Strings = {
  // common (shared across pages)
  'common.tool_call_failed': 'Werkzeugaufruf fehlgeschlagen: {error}',
  'common.unreachable': 'nicht erreichbar: {error}',
  'common.entity_input': 'Name oder Alias der Entität',
  'common.limit': 'Limit',
  'common.from_ts': 'Von (ISO-Zeitstempel, optional)',
  'common.to_ts': 'Bis (ISO-Zeitstempel, optional)',
  'common.search': 'Suchen',
  'common.resolution_note':
    'Themen werden nach einem Auflösungslauf nach kanonischer Entität ' +
    'gruppiert; auf nicht aufgelösten Daten erscheinen sie als ' +
    'Alias-Oberflächenformen.',
  // landing (streamlit_app.py)
  'landing.caption': 'GraphRAG für die Analyse sozialer Netzwerke',
  'landing.backend_health': 'Backend-Status',
  'landing.registered_tools': 'Registrierte Werkzeuge',
  'landing.pick_tool':
    'Wähle links in der Seitenleiste ein Werkzeug, um den Graphen zu erkunden.',
  'landing.ingestion_on':
    'Datenimport ist aktiviert — siehe die Seite „Datenimport" in der Seitenleiste.',
  'landing.ingestion_off': 'Datenimport ist deaktiviert.',
  // agent (00_agent.py)
  'agent.title': 'chorus Agent',
  'agent.caption':
    'Frage in natürlicher Sprache; der Agent wählt die passenden ' +
    'Werkzeuge. Themen werden nach einem Auflösungslauf nach kanonischer ' +
    'Entität gruppiert; auf nicht aufgelösten Daten erscheinen sie als ' +
    'Alias-Oberflächenformen.',
  'agent.clear': 'Unterhaltung löschen',
  'agent.chat_input': 'Stelle eine Frage zum Netzwerk…',
  'agent.thinking': 'Denkt nach…',
  'agent.tool_calls': 'Werkzeugaufrufe ({n})',
  'agent.trace_error': '**{tool}** — Fehler: {error}',
  'agent.trace_results': ' — {count} Ergebnis(se)',
  'agent.call_failed': 'Agentenaufruf fehlgeschlagen: {error}',
  'agent.no_answer': '(keine Antwort)',
  'agent.truncated':
    'Beim Werkzeugaufruf-Limit gestoppt, bevor eine endgültige Antwort erreicht wurde.',
  // posts_mentioning (01)
  'posts.title': 'Beiträge, die eine Entität erwähnen',
  'posts.hits': '{n} Treffer',
  'posts.no_hits': 'keine Treffer',
  // author_activity_summary (02)
  'author_activity.title': 'Zusammenfassung der Autorenaktivität',
  'author_activity.author_input': 'Handle oder Anzeigename des Autors',
  'author_activity.summarize': 'Zusammenfassen',
  'author_activity.matched': '{n} Autor(en) gefunden',
  'author_activity.no_topics': 'keine Themen im Zeitraum erwähnt',
  'author_activity.no_author': 'kein passender Autor',
  // topic_co_occurrence (03)
  'topic_cooc.title': 'Themen-Kookkurrenz',
  'topic_cooc.seed_input': 'Ausgangsthema (Entität oder Alias)',
  'topic_cooc.find': 'Kookkurrierende Themen finden',
  'topic_cooc.count': '{n} kookkurrierende(s) Thema/Themen mit „{seed}"',
  'topic_cooc.none': 'keine kookkurrierenden Themen',
  // authors_connected_by_topic (04)
  'authors_connected.title': 'Über Themen verbundene Autoren',
  'authors_connected.caption':
    'Nur 1 Hop. Themen werden nach einem Auflösungslauf nach kanonischer ' +
    'Entität gruppiert; auf nicht aufgelösten Daten erscheinen sie als ' +
    'Alias-Oberflächenformen.',
  'authors_connected.seed_author_input':
    'Handle oder Anzeigename des Ausgangsautors',
  'authors_connected.min_overlap': 'Mindestanzahl gemeinsamer Themen',
  'authors_connected.limit': 'Limit (pro gefundenem Ausgangspunkt)',
  'authors_connected.find': 'Verbundene Autoren finden',
  'authors_connected.no_seed': 'kein passender Ausgangsautor',
  'authors_connected.connected_count': '{label}  ·  {n} verbunden',
  'authors_connected.none':
    'keine verbundenen Autoren bei dieser Überschneidungsschwelle',
  // authors_mentioning (05)
  'authors_mentioning.title': 'Autoren, die eine Entität erwähnen',
  'authors_mentioning.caption':
    'Autoren, gereiht nach der Anzahl ihrer Beiträge, die die Entität erwähnen.',
  'authors_mentioning.count': '{n} Autor(en)',
  'authors_mentioning.none': 'keine Autoren',
  // network_around (06)
  'network.title': 'Netzwerk rund um eine Entität',
  'network.caption':
    'Bipartites Autoren-Themen-Netzwerk. Tiefe 1 = die Autoren, die die ' +
    'Entität erwähnen; Tiefe 2 ergänzt zusätzlich die weiteren Themen, ' +
    'die diese erwähnen. Die Ansicht wird durch die untenstehenden Limits ' +
    'begrenzt. Themen werden nach einem Auflösungslauf nach kanonischer ' +
    'Entität gruppiert; auf nicht aufgelösten Daten sind es rohe ' +
    'Alias-Oberflächenformen.',
  'network.depth': 'Tiefe',
  'network.author_limit': 'Autoren-Limit',
  'network.topic_limit': 'Themen-Limit (Tiefe 2)',
  'network.build': 'Netzwerk aufbauen',
  'network.empty': 'kein Netzwerk — die Entität ergab keine Treffer',
  'network.counts':
    '{n} Knoten: {authors} Autor(en), {topics} Thema/Themen; {edges} Kante(n)',
  'network.capped':
    'Begrenzte Ansicht — erhöhe die Autoren-/Themen-Limits, um mehr des Netzwerks zu sehen.',
  // social_network_around (08)
  'social.title': 'Soziales Netzwerk rund um einen Autor',
  'social.caption':
    'Das Follows-/Freundschafts-Ego-Netzwerk rund um einen Autor. Tiefe 1 = die ' +
    'direkten Verbindungen des Autors (wem er folgt, seine Follower, Freunde); Tiefe 2 ' +
    'ergänzt zusätzlich deren Verbindungen. Follows werden mit Pfeilspitzen gezeichnet, ' +
    'Freundschaften als einfache Linien. Die Ansicht wird durch die untenstehenden ' +
    'Limits begrenzt.',
  'social.author_input': 'Handle oder Anzeigename des Autors',
  'social.depth': 'Tiefe',
  'social.limit': 'Limit direkter Verbindungen',
  'social.second_ring_limit': 'Limit des zweiten Rings (Tiefe 2)',
  'social.build': 'Netzwerk aufbauen',
  'social.empty': 'kein Netzwerk — der Autor ergab keine Treffer',
  'social.counts':
    '{n} Autor(en); {edges} Verbindung(en): {follows} Follows, {friends} Freundschaften',
  'social.capped':
    'Begrenzte Ansicht — erhöhe die Limits, um mehr des Netzwerks zu sehen.',
  // data ingestion (01_data_ingestion.py)
  'ingest.title': 'Datenimport',
  'ingest.caption':
    'Tabellen-Exporte hochladen und die Pipeline durchgängig ausführen: migrieren, ' +
    'importieren, auflösen. Große Massenimporte gehören weiterhin per `make ingest` auf den Server.',
  'ingest.disabled':
    'Datenimport über die Oberfläche ist deaktiviert. ' +
    'Setze INGESTION_UI_ENABLED=true im Backend, um ihn zu aktivieren.',
  'ingest.migrations.header': 'Schema-Migrationen',
  'ingest.migrations.pending': 'Ausstehende Migrationen: {versions}',
  'ingest.migrations.apply': 'Migrationen anwenden',
  'ingest.migrations.applying': 'Migrationen werden angewendet…',
  'ingest.migrations.applied': 'Angewendet: {versions}',
  'ingest.migrations.uptodate': 'Schema ist aktuell.',
  'ingest.upload.header': 'Hochladen & importieren',
  'ingest.upload.help':
    'Dateinamen müssen einer bekannten Tabelle entsprechen: postings.csv, comments.csv, ' +
    'messages.csv, profiles.csv, connections.csv (oder segmentiert wie 2026-05_postings.csv).',
  'ingest.upload.label': 'CSV-Tabellen-Exporte',
  'ingest.upload.since': 'Nur Zeilen neuer als (ISO-Zeitstempel, optional)',
  'ingest.upload.then_resolve': 'Auflösung nach dem Import ausführen',
  'ingest.upload.start': 'Import starten',
  'ingest.job.running':
    'Import läuft… diese Seite aktualisiert sich automatisch.',
  'ingest.job.done': 'Import abgeschlossen.',
  'ingest.job.failed': 'Import fehlgeschlagen: {error}',
  'ingest.counts.header': 'Importierte Zeilen',
  'ingest.counts.dropped': 'Verworfene fehlerhafte Zeilen: {detail}',
  'ingest.counts.filtered':
    'Gefilterte Zeilen (kein übergeordneter Beitrag im Batch / kein Kantensignal): {detail}',
  'ingest.counts.skipped': 'Übersprungene Stufen: {detail}',
  'ingest.resolve.header': 'Entitätsauflösung',
  'ingest.resolve.start': 'Auflösung ausführen',
  'ingest.resolve.running':
    'Auflösung läuft… diese Seite aktualisiert sich automatisch.',
  'ingest.resolve.done': 'Auflösung abgeschlossen.',
  'ingest.resolve.failed': 'Auflösung fehlgeschlagen: {error}',
  'ingest.resolve.summary': 'Zusammenfassung der Auflösung',
  'ingest.error.request': 'Anfrage abgelehnt: {detail}',
}
