# LLM Preflight

![llm-preflight running the no-key demo: init, benchmark run, results table, quality gate, and decision block](https://raw.githubusercontent.com/feronovak/llm-preflight/main/docs/images/readme-demo.gif)

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
first token, throughput when the stream is incremental and usage is available,
token totals, and estimated cost. Result files retain request metadata and per-request observations for
reproducibility.

"Deterministic" describes the validator, not the model: every response is
checked against explicit structural rules — a regular expression, a JSON shape,
an exact routing label — so the same response always produces the same verdict.
The tool does not score semantic quality; that is your task-specific
evaluation, and it stays out of scope on purpose.

## What a live run reports

Real output from a cross-provider run (2026-07-16, one short support prompt,
three repetitions per model, total spend under $0.05):

| Model | Success | Latency p50 | Latency p95 | TTFT p50 | Tokens/s p50 | Cost |
|---|---:|---:|---:|---:|---:|---:|
| gpt-5.6-luna | 100% | 1.379s | 1.579s | 0.726s | 139.0 | $0.001686 |
| gpt-5.4-mini | 100% | 1.779s | 3.628s | 1.061s | 126.6 | $0.001143 |
| claude-fable-5 | 100% | 6.497s | 7.573s | 2.908s | 56.5 | $0.033610 |
| claude-opus-4-8 | 100% | 3.859s | 4.267s | 1.350s | 54.3 | $0.010205 |
| gemini-3.5-flash | 100% | 2.924s | 2.955s | 2.895s | n/a | $0.001084 |
| minimax-m3 | 100% | 3.356s | 3.631s | 1.719s | 90.4 | n/a |

Tokens/s reads `n/a` when a provider delivers the response as a terminal
burst instead of an incremental stream — the observable window measures
transport, not generation, so no rate is reported. Cost reads `n/a` when
pricing for the model is unknown.

The report ends with a decision block:

```
- Fastest: gpt-5.6-luna — 1.423s mean latency.
- Cheapest: gemini-3.5-flash — $0.001084 total.
- Best value: gpt-5.6-luna — 88% composite score.
- Recommended: gpt-5.6-luna — passed every selected test and led the
  qualified value ranking.
```

Numbers like these are evidence for one environment at one time, not a
leaderboard. Latency depends on your network and region; run the preflight
from the host that will serve production traffic.

The same comparison can be driven interactively — pick models and tests at
the terminal, read the cost ceiling before anything is sent, watch each
request report its own cost, and end on the decision. This capture is a real
two-model paid run that cost half a cent
([config](https://github.com/feronovak/llm-preflight/blob/main/examples/flagship-comparison.json),
[details](https://github.com/feronovak/llm-preflight/blob/main/docs/interactive.md)):

![Interactive comparison of two commercial models on two custom chat prompts, from selection through cost preview to the results table and decision](https://raw.githubusercontent.com/feronovak/llm-preflight/main/docs/images/interactive-demo.gif)

## First live run

Python 3.10+ is required. There are no third-party runtime dependencies:
`pip install llm-preflight` installs this package and nothing else, and the
CLI runs on the Python standard library alone. Development tools (pytest,
ruff, mypy) are optional extras that never reach a production install.

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
Use [custom contract tests](https://github.com/feronovak/llm-preflight/blob/main/docs/custom-tests.md) to express the outputs your
own feature must preserve.

## Choose your path

**I am new and want to see the tool safely.** Start with the
[Getting started guide](https://github.com/feronovak/llm-preflight/blob/main/docs/getting-started.md). It uses a no-key local mock
before any provider request.

**I know the current and candidate model IDs.** Edit one config, run the
[migration check](#change-a-model-safely), then add a
[custom contract test](https://github.com/feronovak/llm-preflight/blob/main/docs/custom-tests.md) for the output your feature must
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
[catalogue tutorial](https://github.com/feronovak/llm-preflight/blob/main/docs/model-watch.md) for the decision points.

**I am automating an established contract.** Use
[CI and JSON output](https://github.com/feronovak/llm-preflight/blob/main/docs/ci.md), with a saved baseline and `--ci` where a
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
modes, see [workflows](https://github.com/feronovak/llm-preflight/blob/main/docs/workflows.md). For models, environment files,
custom prompts, and provider-specific options, see
[configuration](https://github.com/feronovak/llm-preflight/blob/main/docs/configuration.md).

## What makes a comparison useful

- Keep prompts, system instructions, temperature, and output limits fixed.
- Validate outputs: a fast malformed response is a failed result.
- Run from the same host; network distance and provider load affect latency.
- Treat single-user latency and load testing as separate experiments.
- Prefer dated model IDs over moving aliases.

The CLI distinguishes `API FAIL` (transport, credentials, provider, or request
failure) from `API OK / TEST FAIL` (a response that fails your validator).
Recommendations only consider models that pass every selected test.

## How it compares

Several good tools live near this space. Use them when their job is your job:

- **promptfoo, deepeval** — full evaluation suites: scored quality metrics,
  red-teaming, large ongoing test matrices in CI. Use them to grade prompt and
  model quality over time.
- **llmci** — CI merge gates and prompt migration; it rewrites prompts for a
  new model. Use it when the prompt should adapt to the model.
- **Braintrust, LangSmith** — hosted platforms: tracing, dashboards, team
  collaboration, production observability.
- **`llm` (Simon Willison)** — a general multi-provider CLI for running
  prompts, not a comparison harness.

LLM Preflight does one narrower job: the local go/no-go check in the moment
before a model switch. Your prompt, candidate models, structural validation,
latency, and cost — one command, one report, no hosted service, no telemetry,
and no vendor between you and the verdict.

## Documentation

- [Getting started](https://github.com/feronovak/llm-preflight/blob/main/docs/getting-started.md) — safe demo, first paid run, and
  choosing the right workflow.
- [Workflows](https://github.com/feronovak/llm-preflight/blob/main/docs/workflows.md) — discovery, smoke mode, CI, replay, matrix,
  baselines, and request safety.
- [Configuration](https://github.com/feronovak/llm-preflight/blob/main/docs/configuration.md) — providers, custom prompts, presets,
  aliases, and environment overlays.
- [Configuration reference](https://github.com/feronovak/llm-preflight/blob/main/docs/config-reference.md) — every supported config
  key, default, and validation rule.
- [Custom contract tests](https://github.com/feronovak/llm-preflight/blob/main/docs/custom-tests.md) — copyable JSON extraction,
  exact-routing, and content-rule migration tests.
- [CLI reference](https://github.com/feronovak/llm-preflight/blob/main/docs/cli-reference.md) — every command-line option, default,
  and incompatibility.
- [Interactive mode](https://github.com/feronovak/llm-preflight/blob/main/docs/interactive.md) — selection, paid-run preview, and
  live progress.
- [CI and JSON output](https://github.com/feronovak/llm-preflight/blob/main/docs/ci.md) — machine-readable output and exit-code
  gates.
- [Result JSON schema](https://github.com/feronovak/llm-preflight/blob/main/docs/result-schema.md) — fields for integrations and
  saved benchmark evidence.
- [Model watch and approval](https://github.com/feronovak/llm-preflight/blob/main/docs/model-watch.md) — discover, compare, and
  deliberately promote, re-test, and retire provider models.
- [Troubleshooting](https://github.com/feronovak/llm-preflight/blob/main/docs/troubleshooting.md) — installation, credentials,
  catalogue, and benchmark-result recovery.
- [Tests, pricing, and safety](https://github.com/feronovak/llm-preflight/blob/main/docs/tests-pricing-safety.md) — built-in tests,
  validators, pricing confidence, retries, and sensitive data.
- [Contributing](https://github.com/feronovak/llm-preflight/blob/main/CONTRIBUTING.md) — development setup and the TDD workflow.
- [Security](https://github.com/feronovak/llm-preflight/blob/main/SECURITY.md) — reporting vulnerabilities.

## Contributing and license

Contributions are welcome; see [CONTRIBUTING.md](https://github.com/feronovak/llm-preflight/blob/main/CONTRIBUTING.md). Released
under the [MIT License](https://github.com/feronovak/llm-preflight/blob/main/LICENSE).
