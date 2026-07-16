# Changelog

All notable changes to this project are documented here.

## 2.0.2 - 2026-07-16

LLM Preflight is a local, cross-provider preflight CLI for validating a model
switch before it reaches production. It runs deterministic prompt validation
alongside latency, tokens, and cost across OpenAI, Anthropic, Gemini, xAI,
OpenRouter, and OpenAI-compatible providers.

The project now ships as a single package and command: `llm_preflight` /
`llm-preflight`. All compatibility surfaces from the earlier `llm-speed-bench`
/ `llm_bench` naming — the `llm-bench` command alias, the `llm_bench` import
namespace, and the legacy PyPI compatibility shim — have been removed.

## 2.0.1 - 2026-07-16

### Fixed

- Add the public `python3 -m llm_preflight` entry point for source checkouts
  and generated guidance; retain `python3 -m llm_bench.cli` as a legacy import
  path only.

## 2.0.0 - 2026-07-16

### Changed

- Rename the project and primary PyPI distribution to **LLM Preflight**
  (`llm-preflight`): a local, cross-provider preflight for a model switch.
- Make `llm-preflight` the primary command while retaining `llm-bench` and the
  `llm_bench` Python import namespace as supported compatibility interfaces.
- Update public documentation, examples, package artifacts, and release
  automation to use the new product name and primary command.

## 1.2.2 - 2026-07-16

### Fixed

- Keep plain-prompt validation results in model summaries, quality gates, and
  recommendation ranking so an invalid output can never pass or be recommended.
- Reject unknown validation keys and support explicit exact-match validation in
  ordinary and starter configurations.
- Calculate request and retry-expanded cost limits from every profile case,
  warmup, prompt override, and output limit.
- Preserve per-model results when client setup, runtime URL validation, or a
  request worker fails instead of aborting the benchmark.
- Keep transient catalogue probe failures retryable; only structured stable
  incompatibility evidence changes a model's catalogue classification.
- Measure successful request latency and time-to-first-token per final attempt,
  excluding retry backoff, and apply the same retry policy to Responses API
  requests.
- Gate CI comparisons on configured cost regressions, reject ambiguous custom
  prompt names and empty `contains` rules, and make numeric-only checks reject
  explanatory or contradictory output.
- Keep interactive catalogue comparisons head-to-head with selected approved
  models; require a distinct paid-run confirmation even after a stray `y` at
  the stop-mode prompt; preserve discovery deltas when a candidate run fails.
- Prefer authoritative ready-text catalogue evidence over model-name heuristics
  and redact Gemini and xAI key formats from all terminal, JSON, candidate-plan,
  and result output.
- Preserve transport retries by classifying socket failures by exception type;
  bootstrap a catalogue snapshot only after a successful first candidate run.
- Gate CI comparisons on validation-rate regressions as well as latency,
  request success, and cost; reject legacy built-in test aliases as custom
  prompt names.
- Keep `all` in catalogue review to the four inexpensive functional checks,
  protect invalid-scheme URL errors from credential echoes, and harden local
  workspace, exact-model-selection, approval-file, and query-encoding edges.

## 1.2.1 - 2026-07-16

### Fixed

- Keep the credential-free `.env.production` template created by `catalog init`
  identical to the checked-in `.env.example` template.

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
