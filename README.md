# LLM Preflight

Know whether a model change is safe before it reaches production. LLM Preflight
runs a small local preflight across providers and compares validated
output, response speed, tokens, and estimated cost.

It is a local preflight tool—not a hosted evaluation platform, tracing system,
RAG framework, or public leaderboard. Its results are evidence for your
account, network, prompts, and validation rules.

> [!WARNING]
> Live benchmarks make paid API requests. Start with the no-key demo, preview
> the plan before a live run, and keep limits and repetitions small.

## Try it in 60 seconds

Create and run a deterministic local benchmark—no API key or network request:

```bash
llm-preflight --init
llm-preflight benchmark.json --no-save
```

From a source checkout:

```bash
python3 -m llm_preflight --init
python3 -m llm_preflight benchmark.json --no-save
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
python3 -m llm_preflight benchmark.json --dry-run
python3 -m llm_preflight benchmark.json
```

The CLI reads `.env.production` beside the config without overriding environment
variables already set by your shell. Use `--no-env-file` or `--env-file PATH`
when needed. Runs print a terminal report and, unless `--no-save` is used,
write JSON and Markdown results under `results/`.

Install the command globally in a virtual environment if preferred:

```bash
python3 -m pip install llm-preflight
llm-preflight --init
```

Run `--doctor` and `--dry-run` before the final command. They make no generation
requests; the final command is the paid work.

## Change a model safely

This is the core workflow. Put your approved model and candidate model in one
config, then run the small response-and-contract preflight:

```bash
llm-preflight benchmark.json --migration-check --dry-run
llm-preflight benchmark.json --migration-check
```

It sends three short representative cases to each selected model, once each.
It answers: did the API work, did each response meet the basic contract, and
how quickly did the provider start and finish responding? It is a cheap
compatibility check, not a statistical performance conclusion.

When that passes, run the task-specific checks that match your application—for
example `exact-routing-check` or `structured-output-check`—before approving a
switch.
Use [custom contract tests](docs/custom-tests.md) to express the outputs your
own feature must preserve.

## Choose your path

**I am new and want to see the tool safely.** Start with the
[Getting started guide](docs/getting-started.md). It uses a no-key local mock
before any provider request.

**I know the current and candidate model IDs.** Edit one config, run the
[migration check](#change-a-model-safely), then add a
[custom contract test](docs/custom-tests.md) for the output your feature must
preserve. You do not need the catalogue.

**I want to find and review provider releases.** Use the local catalogue
lifecycle below. It keeps broad provider metadata separate from the small set
of models you approve for ongoing testing.

```bash
llm-preflight catalog init
llm-preflight catalog refresh benchmarks/watch.json
# If a model is shown as “Needs one probe”, review and confirm a minimal request:
llm-preflight catalog probe benchmarks/watch.json
llm-preflight catalog prepare benchmarks/watch.json \
  --against benchmarks/approved.json --output benchmarks/candidates.json
llm-preflight benchmarks/candidates.json --interactive \
  --approve-to benchmarks/approved.json
```

Refresh reads metadata only. A probe sends one minimal request only for text
candidates you select and confirm. The interactive benchmark then lets you
approve passing models explicitly. Follow the complete
[catalogue tutorial](docs/model-watch.md) for the decision points.

**I am automating an established contract.** Use
[CI and JSON output](docs/ci.md), with a saved baseline and `--ci` where a
regression should fail the pipeline.

## Useful commands once you know your path

```bash
# Inspect configuration, credentials, and model selection without generation.
llm-preflight benchmark.json --doctor
llm-preflight benchmark.json --dry-run
llm-preflight benchmark.json --pricing-check

# Run a reduced live benchmark.
llm-preflight benchmark.json --smoke

# Run a single ad hoc prompt.
llm-preflight --quick "Return only valid JSON with a status field." \
  --models openai:gpt-5.4-mini
```

For advanced discovery, interactive runs, CI, baselines, replay, and stop
modes, see [workflows](docs/workflows.md). For models, environment files,
custom prompts, and provider-specific options, see
[configuration](docs/configuration.md).

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

- [Getting started](docs/getting-started.md) — safe demo, first paid run, and
  choosing the right workflow.
- [Workflows](docs/workflows.md) — discovery, smoke mode, CI, replay, matrix,
  baselines, and request safety.
- [Configuration](docs/configuration.md) — providers, custom prompts, presets,
  aliases, and environment overlays.
- [Configuration reference](docs/config-reference.md) — every supported config
  key, default, and validation rule.
- [Custom contract tests](docs/custom-tests.md) — copyable JSON extraction,
  exact-routing, and content-rule migration tests.
- [CLI reference](docs/cli-reference.md) — every command-line option, default,
  and incompatibility.
- [Interactive mode](docs/interactive.md) — selection, paid-run preview, and
  live progress.
- [CI and JSON output](docs/ci.md) — machine-readable output and exit-code
  gates.
- [Result JSON schema](docs/result-schema.md) — fields for integrations and
  saved benchmark evidence.
- [Model watch and approval](docs/model-watch.md) — discover, compare, and
  deliberately promote, re-test, and retire provider models.
- [Troubleshooting](docs/troubleshooting.md) — installation, credentials,
  catalogue, and benchmark-result recovery.
- [Tests, pricing, and safety](docs/tests-pricing-safety.md) — built-in tests,
  validators, pricing confidence, retries, and sensitive data.
- [Contributing](CONTRIBUTING.md) — development setup and the TDD workflow.
- [Security](SECURITY.md) — reporting vulnerabilities.

## Contributing and license

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Released
under the [MIT License](LICENSE).
