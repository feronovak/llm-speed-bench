# Changelog

All notable changes to this project are documented here.

## 1.2.0 - 2026-07-16

### Added

- Add the local model lifecycle: `catalog init`, `catalog refresh`, and
  `catalog prepare`, followed by the normal interactive benchmark flow and
  explicit `models approve` promotion.
- Add local catalogue snapshots and model-change diffs; retain `watch-new` and
  `approve-model` as compatibility aliases.
- Add interactive `--approve-to` promotion, explicit retry-risk acceptance,
  and candidate-plan `--replace` protection.
- Classify catalogue entries as ready text benchmarks, text candidates needing
  one explicit probe, or incompatible generic-text endpoints using provider and
  OpenRouter capability evidence.
- Add `catalog probe` and a local, permission-restricted capability ledger that
  records only safe compatibility evidence, never response text or credentials.
- Add `--migration-check`: a one-repetition, no-warmup three-case response and
  basic-contract preflight for comparing a candidate model with an incumbent.
- Add a custom-contract tutorial and runnable mock examples for JSON extraction,
  exact intent routing, and required-content validation.
- Rename default test packs around their user value: quick migration, exact
  routing, structured output, numeric instruction, and concurrency health.
  Keep the former selectors as compatibility aliases.

### Fixed

- Refuse a concurrent benchmark targeting the same results directory before it
  can issue duplicate paid requests.
- Migrate legacy catalogue snapshots without reporting every model as changed
  solely because richer capability metadata was introduced.

## 1.0.3 - 2026-07-13

### Changed

- Keep internal agent, roadmap, launch, and release-runbook material out of
  public source distributions.
- Restrict source artifacts to runtime code, package metadata, user-facing
  documentation, and example configuration files.

## 1.0.2 - 2026-07-13

### Added

- Add `llm-bench --init` to create a safe, deterministic no-key mock benchmark
  without overwriting an existing configuration.
- Visually separate interactive setup stages and final terminal results,
  quality-gate, and decision sections.
- Render `--dry-run` as a readable terminal plan by default; retain JSON output
  with `--json` for automation.
- State the qualified recommendation explicitly and show the interactive
  command after `--init` creates a mock configuration.

### Fixed

- Exclude models that fail any selected test from fastest, cheapest, and
  best-value recommendations; list them with their failed test instead.
- Correct smoke-mode documentation: it reduces repetitions and warmups, but
  does not suppress selected profile-case or load-test expansion.

## 1.0.1 - 2026-07-13

### Fixed

- Handle Ctrl-C cleanly with exit code `130` and without writing artifacts.

## 1.0.0 - 2026-07-13

### Added

- Cross-provider smoke testing, discovery, deterministic validators, reports,
  pricing checks, retry diagnostics, and CI-oriented controls.
- A mock-provider quickstart and `--no-save` for no-key and CI workflows.
- Retry jitter plus nominal and retry-expanded request/cost planning.

### Security

- Redact all custom request-header values from result artifacts and output.
- Enforce configured cost ceilings only when complete pricing is available.

### Fixed

- Apply CLI `--tests` selections to budget enforcement.
- Keep the static type-check security gate green.
