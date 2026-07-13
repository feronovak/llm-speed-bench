# Getting started

## Safe local demo

Run this first. It creates a deterministic mock config and makes no network or
paid provider request:

```bash
llm-bench --init
llm-bench benchmark.json --no-save
```

`--init` refuses to overwrite a config. Press Ctrl-C to cancel a run cleanly;
the command exits with status `130` and writes no artifact.

## First paid benchmark

Copy the example and put only the provider keys you need in the adjacent env
file:

```bash
cp benchmark.example.json benchmark.json
cp .env.example .env.production
llm-bench benchmark.json --dry-run
llm-bench benchmark.json
```

The CLI loads `.env.production` without replacing a variable already in the
process environment. Use `--no-env-file` to disable that behavior or
`--env-file PATH` to choose another file. Never commit credential files.

Before a paid run, use `--dry-run` to see resolved models and tests, nominal
and retry-expanded request counts, estimated cost, stop mode, and response
retention. `--pricing-check` exits non-zero when prices are unknown or stale.

## Read the result

Each saved run writes JSON and a Markdown report under `results/`. The summary
identifies the fastest, cheapest, and best-value model among models that pass
every selected test. A model that fails validation is not recommended merely
because it is quick or cheap.

Interactive and smoke runs retain only failed responses by default. Set
`"save_responses": true` only when raw output is needed for diagnosis and the
prompt content is safe to retain.

Next: [workflows](workflows.md), [configuration](configuration.md), and
[tests, pricing, and safety](tests-pricing-safety.md).
