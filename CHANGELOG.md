# Changelog

All notable changes to this project are documented here.

## 1.0.2 - Unreleased

### Added

- Add `llm-bench --init` to create a safe, deterministic no-key mock benchmark
  without overwriting an existing configuration.
- Visually separate interactive setup stages and final terminal results,
  quality-gate, and decision sections.

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
