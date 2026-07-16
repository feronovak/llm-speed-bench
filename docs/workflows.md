# Workflows

Use this page after choosing a path in [Getting started](getting-started.md).
It is the command reference for repeatable work; it is not a required sequence.

- **Known models:** start with the migration check below, then use a custom
  contract for your feature.
- **New provider releases:** use the catalogue lifecycle.
- **A stable, automated check:** use the CI workflow after the contract is
  proven locally.

## Decide whether a model change is safe

Put your current approved model and candidate model in the same configuration,
then run the smallest meaningful preflight:

```bash
llm-bench benchmark.json --migration-check --dry-run
llm-bench benchmark.json --migration-check
```

This runs three short response cases once per model. It checks real account/API
compatibility, basic response validity, TTFT, end-to-end latency, token use, and
estimated cost when available. It is the right first step after a provider
release or model switch—not a statistical latency claim.

If it passes, run the contract that matters to your feature:

```bash
llm-bench benchmark.json --tests structured-output-check
llm-bench benchmark.json --tests exact-routing-check
```

See [Custom contract tests](custom-tests.md) for copyable examples and guidance
on choosing strict JSON, exact-routing, or content rules.

Use several repetitions only when you need a dependable performance comparison;
run `concurrency-health-check` separately when you need concurrency evidence.

## Maintain approved models

For a recurring provider-model review, use the catalogue lifecycle—not a
runtime `discovery` config directly. It creates an ignored local workspace,
keeps temporary candidates separate from permanent approved models, and uses
the normal interactive benchmark screen for the paid run.

```bash
llm-bench catalog init
llm-bench catalog refresh benchmarks/watch.json
# Only if the catalogue says a model "Needs one probe":
llm-bench catalog probe benchmarks/watch.json
llm-bench catalog prepare benchmarks/watch.json \
  --against benchmarks/approved.json --output benchmarks/candidates.json
llm-bench benchmarks/candidates.json --migration-check
llm-bench benchmarks/candidates.json --interactive \
  --approve-to benchmarks/approved.json
```

Refresh is metadata-only. A probe is one confirmed, minimal request for a text
candidate; it is optional and may be billable. `prepare` then offers only models
known to be compatible with the generic text benchmark.

To run your permanent baseline later, create its runnable test plan with
`catalog test WATCH --approved APPROVED --output CONFIG`, then run that config
interactively. To retire a permanent model, use `models remove PROVIDER:MODEL
--approved APPROVED`; it asks for confirmation and retains past evidence.

See [Model watch and approval](model-watch.md) for the full first-run and
re-review process. `watch-new` is retained only for compatibility with older
scripts.

## Discovery and interactive selection

Use `discovery` in a config to resolve provider catalogs at run time. Every
source requires a positive `limit` as a cost-safety control.

```json
{
  "discovery": [
    {"provider": "openrouter", "sort": "newest", "output_modalities": "text", "limit": 5}
  ]
}
```

```bash
llm-bench benchmark.auto.example.json --catalog
llm-bench benchmark.auto.example.json --interactive
```

Interactive benchmark mode presents a run plan before confirmation. Model
selections can be numbers, a provider such as `openai`, or a provider family
such as `openrouter/qwen`. It is for a benchmark run; use `catalog probe` only
for the separate one-request compatibility check described in the catalogue
tutorial.

## Fast checks

```bash
# Reduced live run: one repetition, no warmups, normal concurrency one.
llm-bench benchmark.json --smoke --dry-run
llm-bench benchmark.json --smoke

# Configuration and pricing checks without generation.
llm-bench benchmark.json --doctor
llm-bench benchmark.json --pricing-check

# One prompt without a config file.
llm-bench --quick "Reply with ok." --models mock:local --no-save
```

Smoke mode still makes paid requests. It reduces normal request settings but
does not remove an explicitly selected `concurrency-health-check`; always
inspect the dry run first.

## Select, stop, and retain

Use `--tests` (or the compatibility alias `--profiles`) to select built-in and
custom tests together:

```bash
llm-bench benchmark.json --tests exact-routing-check,source-to-quiz
```

Choose `--stop-on api-error`, `test-fail`, or `any-fail`; omit `--stop-on` to
run every selected model. `--fail-fast` remains an alias for
`--stop-on any-fail`. Use `--no-save` when CI only needs stdout and an exit
status. See [CI and JSON output](ci.md) for automation recipes.

## Compare and automate

```bash
llm-bench --diff results/old.json results/new.json
llm-bench benchmark.json --baseline results/old.json --ci
llm-bench --replay results/run.json
llm-bench benchmark.json --tests all --matrix
llm-bench benchmark.auto.json --changed-since catalog.json --dry-run
```

`--changed-since` runs only discovered models absent from a previous catalog
snapshot. Set `max_requests` and `max_estimated_cost_usd` in a config to stop
surprise spend before requests are sent; a cost ceiling requires complete
pricing for every selected model.

See [configuration](configuration.md) for aliases and environment overlays.
