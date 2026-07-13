# LLM Speed Bench

Smoke-test live LLM models before putting them in production: run your prompts
across providers and compare validity, latency, tokens, and estimated cost.

It is a local preflight tool, not a hosted eval platform, tracing system, RAG
framework, or public leaderboard.

> [!WARNING]
> Live benchmarks make paid API requests. Start with the no-key demo, preview
> the plan before a live run, and keep limits and repetitions small.

## Try it in 60 seconds

Create and run a deterministic local benchmark—no API key or network request:

```bash
llm-bench --init
llm-bench benchmark.json --no-save
```

From a source checkout:

```bash
python3 -m llm_bench.cli --init
python3 -m llm_bench.cli benchmark.json --no-save
```

`--init` never overwrites an existing config. It creates a mock benchmark so
you can see the report and exit behavior before making a paid request.

## Use it when

- You are switching models or providers.
- A provider publishes a new model or changes a `latest` alias.
- You need to compare your own prompt's validity, latency, and cost.
- You want local result artifacts instead of a hosted dashboard.

It measures deterministic test validity, end-to-end latency (p50/p95), time to
first token, throughput when usage is available, token totals, and estimated
cost. Result files retain request metadata and per-request observations for
reproducibility.

## First live run

Python 3.10+ is required; there are no runtime dependencies.

```bash
cp benchmark.example.json benchmark.json
cp .env.example .env.production
# Edit benchmark.json and add only the provider keys you use.
python3 -m llm_bench.cli benchmark.json --dry-run
python3 -m llm_bench.cli benchmark.json
```

The CLI reads `.env.production` beside the config without overriding environment
variables already set by your shell. Use `--no-env-file` or `--env-file PATH`
when needed. Runs print a terminal report and, unless `--no-save` is used,
write JSON and Markdown results under `results/`.

Install the command globally in a virtual environment if preferred:

```bash
python3 -m pip install llm-speed-bench
llm-bench --init
```

## Core workflows

```bash
# Inspect configuration, credentials, and model selection without generation.
llm-bench benchmark.json --doctor
llm-bench benchmark.json --dry-run
llm-bench benchmark.json --pricing-check

# Run a reduced live benchmark.
llm-bench benchmark.json --smoke

# Test discovered provider models without sending generation requests first.
llm-bench benchmark.auto.example.json --catalog

# Run a single ad hoc prompt.
llm-bench --quick "Return only valid JSON with a status field." \
  --models openai:gpt-5.4-mini
```

For discovery, interactive runs, CI, baselines, replay, and stop modes, see
[workflows](docs/workflows.md). For models, environment files, custom prompts,
and provider-specific options, see [configuration](docs/configuration.md).

## What makes a comparison useful

- Keep prompts, system instructions, temperature, and output limits fixed.
- Validate outputs: a fast malformed response is a failed result.
- Run from the same host; network distance and provider load affect latency.
- Treat single-user latency and load testing as separate experiments.
- Prefer dated model IDs over moving aliases.

The CLI distinguishes `API FAIL` (transport, credentials, provider, or request
failure) from `API OK / TEST FAIL` (a response that fails your validator).
Recommendations only consider models that pass every selected test.

## Documentation

- [Getting started](docs/getting-started.md) — mock demo, first paid run, and
  result artifacts.
- [Workflows](docs/workflows.md) — discovery, smoke mode, CI, replay, matrix,
  baselines, and request safety.
- [Configuration](docs/configuration.md) — providers, custom prompts, presets,
  aliases, and environment overlays.
- [Tests, pricing, and safety](docs/tests-pricing-safety.md) — built-in tests,
  validators, pricing confidence, retries, and sensitive data.
- [Contributing](CONTRIBUTING.md) — development setup and the TDD workflow.
- [Security](SECURITY.md) — reporting vulnerabilities.

## Contributing and license

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Released
under the [MIT License](LICENSE).
