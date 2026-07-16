# CI and JSON output

## JSON output and exit status

`--json` writes a machine-readable result to stdout. Result saving notices go to
stderr, so redirecting stdout is safe. A benchmark exits 0 only when every
selected model completes and passes validation; an API error or failed test
exits 1. Cancellation exits 130.

```bash
llm-bench benchmark.json --json --no-save > current.json
```

Use `--dry-run --json` to inspect the exact paid-work plan in CI without making
requests. `--doctor --json`, `--pricing-check`, and `--catalog` are also safe
preflight steps.

Keep catalogue discovery separate from the benchmark gate. `catalog refresh`
fetches provider metadata and can be scheduled as an informational job, but
`catalog probe` deliberately sends a billable request and is best reviewed by a
person before it changes local compatibility evidence. CI should benchmark only
reviewed models in a known configuration or approved-test plan.

## Baseline gate

Keep a reviewed baseline result, write the current run to a file, then compare
them in a separate command:

```bash
llm-bench benchmark.json --json --no-save > current.json
llm-bench --diff baseline.json current.json --json --ci > comparison.json
```

The second command exits 1 when configured baseline thresholds regress and
otherwise exits 0. This two-command form keeps each stdout stream valid JSON.

`--baseline baseline.json --ci` also gates a live run, but its final diff is
human-readable after the result JSON. Prefer the two-command form when another
CI step parses stdout.

The CI comparison fails for a latency increase, a request-success or validation
rate drop, or a cost increase beyond its configured threshold. Costs are
compared only when both result files contain a known estimate; retain
`max_estimated_cost_usd` as the separate hard spend ceiling before a run.

## Safe CI starter

```bash
llm-bench benchmark.json --doctor --json
llm-bench benchmark.json --pricing-check
llm-bench benchmark.json --smoke --dry-run --json
llm-bench benchmark.json --smoke --json --no-save > current.json
```

Set `max_requests` and `max_estimated_cost_usd` in the config to prevent
unexpected spend. Unknown pricing prevents cost-ceiling enforcement, so treat a
failed `--pricing-check` as a failed preflight.
