# 0004 — Python 3.12 in dev, ≥3.11,≤3.13 supported

Status: accepted
Date: 2026-05-20

## Context

Three pieces of metadata initially disagreed about the Python version:
`pyproject.toml` (`>=3.13`), `.python-version` (`3.12`), and CLAUDE.md
("Python 3.12+"). docint pins `>=3.11,<3.12` for transformers/torch
compatibility; chorus has no in-process ML, so that constraint does
not apply.

## Decision

- `pyproject.toml` accepts `>=3.11,<=3.13` to maximize wheel
  availability and let downstream Python versions slot in.
- `.python-version` pins dev to `3.12` for reproducible local
  environments.
- CI runs across the full `>=3.11,<=3.13` range; the Docker image is
  built against `python3.12-bookworm-slim` (matches dev).

## Alternatives considered

- **Pin to exactly 3.12.** Simpler, but unnecessarily restrictive
  given chorus has no version-sensitive ML dependencies.
- **Move to 3.13.** Wheel availability is still uneven on some
  transitive dependencies. Holds no clear win for chorus.

## Consequences

- Positive: chorus matches the broader ecosystem default, dev
  environments are reproducible, and there is room to upgrade as 3.13
  wheel coverage matures.
- Negative: CI matrix is wider than strictly needed.
- Reversal trigger: a runtime feature in 3.13+ becomes load-bearing
  (e.g., free-threaded build for ingestion).
