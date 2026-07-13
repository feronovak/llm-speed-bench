# Workflows

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

Interactive mode presents a run plan before confirmation. Model selections can
be numbers, a provider such as `openai`, or a provider family such as
`openrouter/qwen`.

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

Smoke mode still makes paid requests and does not suppress the explicit load
profile. Always inspect the dry run first.

## Select, stop, and retain

Use `--tests` (or the compatibility alias `--profiles`) to select built-in and
custom tests together:

```bash
llm-bench benchmark.json --tests classification,source-to-quiz
```

Choose `--stop-on api-error`, `test-fail`, `any-fail`, or `never`. `--fail-fast`
remains an alias for `--stop-on any-fail`. Use `--no-save` when CI only needs
stdout and an exit status.

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
