# 0001 — Package-style layout (`chorus/` package)

Status: accepted
Date: 2026-05-20

## Context

The early CLAUDE.md sketch showed flat top-level directories
(`api/`, `queries/`, `ui/`, `migrations/`, …). The sister project docint
uses a package-style layout instead (`docint/core/`, `docint/utils/`),
which preserves importability and matches Python packaging conventions.

## Decision

Use a package-style layout. The repo root contains exactly one
importable package — `chorus/` — with subpackages
(`api/`, `audit/`, `db/`, `inference/`, `ingestion/`, `migrations/`,
`queries/`, `tools/`, `ui/`, `utils/`). Infrastructure (`docker/`,
`Makefile`, `pyproject.toml`, `.github/`) and prose (`docs/`, `tests/`)
sit alongside the package at repo root.

## Alternatives considered

- **Flat dirs at repo root.** Visually compact and easier to skim in
  `ls`, but Python imports get ugly (`from api.main import app` vs.
  `from chorus.api.main import app`) and packaging needs a custom
  `[tool.hatch.build.targets.wheel].sources` mapping. Also diverges
  from docint, raising the cognitive cost of switching between repos.

- **`src/chorus/` layout.** A common Python pattern that prevents
  accidental imports from the project root. Adds one level of nesting
  without giving us a benefit chorus needs — tests already live in
  `tests/`, not adjacent to the package, so there's no import-shadowing
  risk to guard against.

## Consequences

- Positive: clean imports, hatchling builds the wheel from one
  `packages = ["chorus"]` line, mirrors docint exactly.
- Negative: `cd chorus/` ambiguity between repo root and package root
  in conversation. We address it by always writing the second one as
  `chorus/chorus/`.
- Reversal trigger: would only revisit if we adopt a workspace
  (multi-package) layout, which would itself be a larger architectural
  shift.
