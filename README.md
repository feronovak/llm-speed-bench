# LLM Speed Bench

A local CLI for smoke-testing live LLM models before you use them in
production. Run your own prompts and tests across OpenAI, Anthropic, Gemini,
xAI/Grok, OpenRouter, and arbitrary OpenAI-compatible APIs, then compare
pass/fail behavior, latency, token usage, and cost.

The goal is not to be a full LLM evaluation platform, tracing system, RAG
framework, or public leaderboard. It is a fast model test drive for engineers
who need to answer:

> Does this model actually work for my task, at acceptable speed and cost?

> [!WARNING]
> Benchmarks make paid API requests. Preview the catalog first, keep limits and
> repetitions small while configuring a run, and review the interactive
> confirmation before proceeding.

## What it measures

- Pass/fail behavior for built-in smoke tests and your custom tests
- End-to-end request latency (p50 and p95)
- Time to first generated token (TTFT)
- Output tokens per second, when the provider reports token usage
- Success/error rate and response validation failures
- Input/output token totals and estimated cost, when pricing is configured
- Reproducibility metadata: exact request settings, prompt hash, time, host, and
  raw per-request observations

## When to use it

- Before switching models or providers
- Before accepting a provider's `latest` alias in production
- When a new model appears in a provider catalog
- When you need to compare speed, cost, and output validity on your own prompts
- When you want a local, inspectable result file instead of a hosted dashboard

For deeper application evaluation, RAG metrics, tracing, annotation queues,
red-teaming, or LLM-as-judge workflows, use a dedicated eval platform. This tool
is intentionally narrower: quick live-model validation and cost/speed
comparison.

Network distance, provider load, rate limits, and concurrency affect these
numbers. Run from the same host and use at least 20 measured repetitions for
meaningful comparisons. The default concurrency of one measures interactive
latency; create a separate configuration with higher concurrency to measure load
behavior.

## Quick start

Python 3.10 or newer is required. No runtime packages are needed.

```bash
cp benchmark.example.json benchmark.json
cp .env.example .env.production
# Edit benchmark.json and add only the provider keys you use.
python3 -m llm_bench.cli benchmark.json
```

Alternatively, put the keys in `.env.production` next to the benchmark
configuration. The CLI loads that file automatically and does not overwrite
variables already present in the environment. Loaded values are never printed:

```dotenv
OPENAI_API_KEY="..."
ANTHROPIC_API_KEY="..."
GEMINI_API_KEY="..."
OPENROUTER_API_KEY="..."
XAI_API_KEY="..."
```

To avoid loading `.env.production`, pass `--no-env-file`:

```bash
python3 -m llm_bench.cli benchmark.json --no-env-file
```

To load a different file, pass `--env-file`:

```bash
python3 -m llm_bench.cli benchmark.json --env-file ./local.keys
```

Or install the local CLI:

```bash
python3 -m pip install -e ".[dev]"
llm-bench benchmark.json
```

Each run prints an aligned terminal table and writes full JSON plus a Markdown
report under `results/`. Interactive terminals use color for successful,
partial, and failed rows. Commit or archive the result files if you want durable
trend history. The report ends with an executive summary naming the fastest
model, the cheapest model, and the best value. Value equally weights
valid-output reliability, relative speed, and relative measured cost among
models with at least 80% reliable output. Models with zero reliable output are
excluded from cheapest rankings; partially reliable models are penalized by the
value score. New result files also record warmup usage and total estimated
spend for the complete run.

See [ROADMAP.md](ROADMAP.md) for the product roadmap and OSS promotion plan.

## Automatic model discovery

Use `discovery` to resolve models from provider catalogs at run time. Every
source requires a positive `limit`; this is a cost-safety control. Optional
`include` and `exclude` values are case-insensitive regular expressions.

```json
{
  "discovery": [
    {
      "provider": "openrouter",
      "sort": "newest",
      "output_modalities": "text",
      "limit": 5
    }
  ]
}
```

Inspect exactly what would run without making generation requests:

```bash
python3 -m llm_bench.cli benchmark.auto.example.json --catalog
```

Choose models, providers or provider families, tests, and repetitions
interactively. After repetitions, the CLI shows a colored run plan with the
selected models, tests, request estimate, response-retention mode, and request
breakdown before asking for confirmation. During the run it reports the current
model and request, profile/case, API status, test-validation status,
input/output tokens, and estimated cost:

```bash
python3 -m llm_bench.cli benchmark.auto.example.json --interactive
```

Model selections accept numbers, a provider such as `openai`, or a provider
family such as `openrouter/qwen`. Multiple selections are comma-separated.

Progress status separates transport from validation:

- `API OK / TEST OK`: the provider returned a response and the evaluator
  accepted it.
- `API OK / TEST FAIL`: the provider returned a response, but the answer failed
  the deterministic test.
- `API FAIL`: the request failed before a usable answer was produced.

The final pass/fail dashboard is based on test validity, not just API request
success.

Interactive mode also asks when to stop:

- `any-fail`: stop after the first model with either an API error or test
  failure.
- `api-error`: stop only if the provider/request breaks; continue past weak
  model outputs.
- `test-fail`: stop after the first model output failure.
- `never`: run every selected model.

## Named custom prompts

Define reusable prompts in the configuration when you want to benchmark your
own requests rather than the built-in profiles. A prompt name is the stable
selector shown in interactive mode and accepted by the non-interactive CLI:

```json
{
  "prompts": [
    {
      "name": "csv-review",
      "system_prompt": "Return concise JSON.",
      "prompt": "Find invalid rows in this CSV:\norder_id,total\nA-1,20\nA-2,-5",
      "request": {
        "temperature": 0,
        "max_output_tokens": 500
      },
      "validation": {
        "contains": "A-2"
      }
    },
    {
      "name": "long-summary",
      "prompt": "Summarize the supplied long text in five bullets."
    }
  ],
  "models": [
    {
      "provider": "openai",
      "model": "gpt-5.4-mini"
    }
  ]
}
```

Run one custom prompt without interaction:

```bash
python3 -m llm_bench.cli benchmark.json --prompt csv-review
```

Custom prompts can also run as first-class tests beside built-in profiles. Use
their names in `--tests`, including mixed selections. The older `--profiles`
flag remains supported as a compatibility alias:

```bash
python3 -m llm_bench.cli benchmark.json \
  --tests classification,csv-review,long-summary
```

In `--interactive`, built-in profiles are numbered first and custom prompts
continue the same numeric list. A configuration may retain the legacy top-level
`prompt` as its default, or contain only `prompts` and require an explicit test
selection.

For short inputs, embed the data directly in JSON using newline escapes. For
longer text, CSV, JSON, or other fixture data, put the file next to the
benchmark config or in a subfolder and reference it with `prompt_file`:

```text
benchmarks/
  benchmark.json
  fixtures/
    orders.csv
```

```json
{
  "prompts": [
    {
      "name": "csv-review",
      "system_prompt": "Return concise JSON with invalid row IDs.",
      "prompt_file": "fixtures/orders.csv",
      "request": {
        "temperature": 0,
        "max_output_tokens": 500
      },
      "validation": {
        "contains": "A-2"
      }
    }
  ],
  "models": [
    {
      "provider": "openai",
      "model": "gpt-5.4-mini"
    }
  ]
}
```

`prompt_file` is resolved relative to the configuration file, must be a
relative path, and cannot escape the config directory with `..`. Use either
`prompt` or `prompt_file` for a custom prompt, not both. If you need
instructions plus data, put both in the referenced file or use `system_prompt`
for the instructions and the file for the user prompt body. Do not place
confidential data in a configuration or fixture file that will be committed to
source control.

### Example: source text to structured quiz

Both checked-in example configurations include a `source-to-quiz` prompt. It
asks each selected model to turn a short source passage into exactly four
questions covering multiple-choice, true-or-false, and short-answer formats.
The response must be JSON and include stable question IDs, answers, plausible
options, and source-grounded explanations. This exercises instruction
following, constrained generation, source fidelity, and structured output in a
single request.

Run the demo directly against the dynamically discovered model set:

```bash
python3 -m llm_bench.cli benchmark.auto.example.json \
  --prompt source-to-quiz
```

In interactive mode, select models first, then choose tests by number or name:

```text
Tests:
  1. chat-fast — ...
  ...
  6. source-to-quiz — Custom prompt test.
Select tests (numbers/names/all) or Enter for the config prompt: 1,6
```

The demo's prompt-level `request` controls temperature and output length for
this test. It also uses prompt-level presets for common provider quirks:

```json
{
  "name": "source-to-quiz",
  "presets": ["structured"]
}
```

The initial presets are `json`, `no-reasoning`, `low-latency`, and
`structured`. They expand into provider-specific request options for providers
that support them, such as Gemini JSON MIME settings and OpenRouter
no-reasoning flags. Explicit `request` values in your config win over preset
defaults. Presets may be placed at the top level for a whole run or on an
individual custom prompt/profile when only one test needs structured-output
settings.

Its validation regex checks that the response contains a `questions` JSON
array. That is a basic structural gate, not a full assessment of quiz
correctness or JSON Schema compliance; review saved responses when comparing
content quality. Failed summaries include common diagnosis hints, for example
reasoning/commentary before JSON, fenced Markdown, unsupported provider
parameters, rate limits, or empty billable responses. Set top-level
`"save_responses": true` temporarily if you want raw model output in the
result, and avoid doing so with sensitive source
material.

Then benchmark the dynamically selected set:

```bash
python3 -m llm_bench.cli benchmark.auto.example.json
```

Run the complete mixed benchmark suite:

```bash
python3 -m llm_bench.cli benchmark.auto.example.json --tests all
```

Or select a subset:

```bash
python3 -m llm_bench.cli benchmark.auto.example.json \
  --tests chat-fast,classification,reasoning
```

Custom prompts can be included in the same selector:

```bash
python3 -m llm_bench.cli benchmark.auto.example.json \
  --tests chat-fast,classification,source-to-quiz
```

The built-in profiles are `chat-fast`, `classification`,
`structured-extraction`, `reasoning`, and `load`. The load profile runs at
concurrency 1, 5, and 10. Coding is intentionally excluded. Set
`"profiles": "all"` in the configuration to make the built-in suite the
default, and use `"suite_repetitions"` to repeat each deterministic case.

When you select `--tests all`, the `load` profile expands its single
`load-short` case into multiple requests: one at concurrency 1, five at
concurrency 5, and ten at concurrency 10 by default. Dry runs and interactive
confirmation show this request breakdown before any paid calls are made, and
live progress labels load requests as `load/load-short@c1`,
`load/load-short@c5`, or `load/load-short@c10`.

## Fast testing workflows

Use smoke mode before a real benchmark. It forces one measured request per
model, disables warmups, and keeps concurrency at one:

```bash
python3 -m llm_bench.cli benchmark.json --smoke
```

Check a configuration without generation requests:

```bash
python3 -m llm_bench.cli benchmark.json --doctor
```

Preview the exact run plan without generation requests:

```bash
python3 -m llm_bench.cli benchmark.json --tests source-to-quiz --dry-run
```

The dry run prints resolved models, selected tests, request count, estimated
cost when pricing is available, active presets, request options, and response
retention behavior.

Run an ad hoc prompt without creating a config file:

```bash
python3 -m llm_bench.cli \
  --quick "Return only valid JSON with a status field." \
  --models openai:gpt-5.4-mini,anthropic:claude-opus-4-8
```

Quick mode loads `.env.production` from the current working directory by
default. Use `--no-env-file` or `--env-file PATH` for the same credential-file
control available to config-based runs.

Compare a new result with a previous baseline:

```bash
python3 -m llm_bench.cli --diff results/old.json results/new.json
python3 -m llm_bench.cli benchmark.json --baseline results/old.json --ci
```

Replay a saved run exactly when the result contains `source_config`:

```bash
python3 -m llm_bench.cli --replay results/run.json
```

For test-suite runs, print a model-by-test quality matrix:

```bash
python3 -m llm_bench.cli benchmark.json --tests all --matrix
```

Use `--changed-since catalog.json` with discovery configs to run only models
not present in a previous catalog snapshot. Use `"max_requests"` and
`"max_estimated_cost_usd"` in a config to stop accidental expensive runs before
requests are made.

Model aliases and environment overlays keep daily tests short:

```json
{
  "aliases": {
    "fast": {"provider": "openai", "model": "gpt-5.4-mini"}
  },
  "environments": {
    "ci": {"repetitions": 1, "warmups": 0},
    "deep": {"profiles": "all", "suite_repetitions": 20}
  },
  "models": ["fast"]
}
```

Then run:

```bash
python3 -m llm_bench.cli benchmark.json --env ci
```

Set `"save_responses": "failures"` to retain only failed outputs for review.
Interactive and smoke runs default to this failure-only retention mode unless
you set `save_responses` explicitly. Use `--stop-on api-error`,
`--stop-on test-fail`, or `--stop-on any-fail` when you want a command to stop
early. The older `--fail-fast` flag remains as an alias for
`--stop-on any-fail`.
The deterministic evaluator supports exact matches, numeric answers, JSON
subsets, regular expressions, contains checks, and a dependency-free structural
`json_schema` subset. For no-network development checks, use the `mock`
provider:

```json
{
  "prompt": "Reply ok.",
  "models": [{"provider": "mock", "model": "local", "response": "ok"}]
}
```

## Built-in test prompts

These are the exact prompts the built-in profiles send to models, together with
the response shape each case expects:

| Profile | Case | Prompt | Expected response |
|---|---|---|---|
| `chat-fast` | `chat-capital` | `Answer in one short sentence: What is the capital of France?` | Any non-empty response |
| `chat-fast` | `chat-summary` | `Summarize in one sentence: A customer changed their email address and can no longer log in.` | Any non-empty response |
| `chat-fast` | `chat-rewrite` | `Rewrite politely in one sentence: Send the report today.` | Any non-empty response |
| `classification` | `class-billing` | `Classify as billing, technical, or account: I was charged twice.` | Exactly `billing` |
| `classification` | `class-technical` | `Classify as billing, technical, or account: The mobile app crashes on startup.` | Exactly `technical` |
| `classification` | `class-account` | `Classify as billing, technical, or account: I need to change my login email.` | Exactly `account` |
| `structured-extraction` | `extract-ticket` | `Extract product and priority as high, medium, or low; map "Urgent" to high: "Urgent: payments are failing in Checkout."` | JSON containing `{"priority":"high","product":"Checkout"}` |
| `structured-extraction` | `extract-person` | `Extract name and city: "Marta Novak lives in Bratislava."` | JSON containing `{"name":"Marta Novak","city":"Bratislava"}` |
| `structured-extraction` | `extract-order` | `Extract order_id and quantity: "Order A-104 contains 7 units."` | JSON containing `{"order_id":"A-104","quantity":7}` |
| `reasoning` | `reason-percent` | `A price of 80 increases by 25%. What is the new price? Return only the numeric answer. Do not include units, words, or explanation.` | Numeric answer `100` |
| `reasoning` | `reason-rate` | `A car travels 150 km in 3 hours. What is its average speed in km/h? Return only the numeric answer. Do not include units, words, or explanation.` | Numeric answer `50` |
| `reasoning` | `reason-sequence` | `What is the next number: 2, 6, 12, 20, 30? Return only the numeric answer. Do not include units, words, or explanation.` | Numeric answer `42` |
| `load` | `load-short` | `Reply with exactly: benchmark` | Exactly `benchmark` |

Use a config-level `profiles` field when you want to run specific test groups.
This works with a single built-in profile, a custom prompt name, or a
comma-separated mixed subset:

```json
{
  "profiles": "classification"
}
```

```json
{
  "profiles": "chat-fast,reasoning"
}
```

```json
{
  "profiles": "chat-fast,source-to-quiz"
}
```

If you want the built-in suite in a config file, set `"profiles": "all"`. If
you need exactly one legacy request instead of a test suite, keep the normal
top-level `prompt` and skip `profiles` entirely.

The catalog response is snapshotted into each result. Gemini discovery records
token limits, supported methods, and its thinking flag. OpenRouter records
pricing, context, modalities, tool/structured-output support, and reasoning
support. OpenAI and Anthropic discovery records the catalog data those APIs
actually return; missing capabilities remain `null` rather than being guessed.
Explicit `models` and discovered models can be used together.

## Development

The repository follows a strict red/green/refactor workflow:

1. Write a focused test for the next behavior.
2. Run it and confirm the expected failure.
3. Add the smallest implementation that makes it pass.
4. Run the entire suite.
5. Refactor only while the suite remains green.

```bash
# Focused red/green cycle
make test-one TEST=tests/test_catalog.py::test_openrouter_normalization_and_limit

# Complete verification
make test
make coverage
```

See `AGENTS.md` for the durable development contract. Live provider credentials
are not needed by the deterministic unit tests.

## Providers and configuration

Every entry in `models` uses the same interface:

```json
{
  "name": "label-in-reports",
  "provider": "openai",
  "model": "provider-model-id",
  "input_cost_per_million": 0,
  "output_cost_per_million": 0
}
```

Supported provider values and default credentials:

| Provider | Native interface | Default API key variable |
|---|---|---|
| `openai` | OpenAI chat completions | `OPENAI_API_KEY` |
| `anthropic` | Anthropic messages | `ANTHROPIC_API_KEY` |
| `gemini` | Gemini generate content | `GEMINI_API_KEY` |
| `xai` | xAI OpenAI-compatible chat completions | `XAI_API_KEY` |
| `openrouter` | OpenAI-compatible chat completions | `OPENROUTER_API_KEY` |
| `openai_compatible` | Configurable chat completions URL | configured with `api_key_env` |

Provider defaults can be overridden per model using `base_url`, `api_key_env`,
and `headers`. This supports proxies, regional gateways, local inference servers,
and OpenRouter attribution headers. `provider_options` inside the shared
`request` object passes provider-specific body fields when the normalized
`temperature`, `system_prompt`, and `max_output_tokens` settings are insufficient.
If a provider/model rejects temperature, set `"supports_temperature": false` or
`"capabilities": {"temperature": false}` on that model so the normalized request
omits it.

Native Grok configuration:

```json
{
  "provider": "xai",
  "model": "grok-4.3"
}
```

Native xAI discovery uses the same filtering and mandatory limit controls:

```json
{
  "provider": "xai",
  "include": "^grok-",
  "limit": 5
}
```

Secrets are read only from environment variables or the selected env file.
Catalog output removes custom headers. Dry-run output, saved JSON results,
Markdown reports, source config snapshots, and common provider error strings are
redacted for keys and values such as `API_KEY`, `TOKEN`, `SECRET`, `PASSWORD`,
`Authorization`, `X-API-Key`, and custom headers. Prompts, model metadata, and
saved failed responses can still contain business-sensitive content, so review
result files before sharing them.

To add a protocol that is not OpenAI-compatible, implement `ProviderClient` and
register it in `create_client` in `llm_bench/client.py`. The benchmark runner,
validation, metrics, and output format need no changes.

## Fair comparison checklist

- Keep the prompt, system instructions, temperature, and maximum output fixed.
- Compare the prompt hash and request settings in result files.
- Use validation so fast but empty or malformed responses count as failures.
- Separate cold/warm and single-user/load tests; do not mix their histories.
- Pin dated model IDs when providers offer them. Aliases may silently change.
- Treat `timeout_seconds` as the provider socket timeout used for connect/read
  operations, not a strict total wall-clock deadline for slowly streaming
  responses.
- Treat provider token counts as authoritative; character-based approximations
  are deliberately not used.

## Pricing

OpenRouter prices are taken from its live model catalog. Public standard API
rates for selected OpenAI, Gemini, and Anthropic models are maintained in
`llm_bench/pricing.py` with an `as_of` date. Provider pricing changes over time;
verify rates before relying on cost comparisons. Explicit
`input_cost_per_million` and `output_cost_per_million` values in a model
configuration override the registry.

Estimated spend includes measured requests and warmups. It does not include
provider-specific taxes, volume agreements, data-residency premiums, tool-call
fees, cache discounts, or other account-specific adjustments.
Use `--dry-run` after pricing edits to confirm the current request estimate
before running a live benchmark.

## Security

- Never commit `.env.production`, backup environment files, raw results, or
  debug logs.
- Use `--no-env-file` in environments where credentials must only come from the
  parent process.
- Use `--env-file PATH` when a benchmark needs an explicit non-default
  credential file.
- Use synthetic prompts for public benchmarks.
- Treat `results/` as sensitive when `save_responses` is enabled.
- Rotate any credential that appears in Git history or logs.
- CI runs `detect-secrets-hook` against publishable files using
  `.secrets.baseline`. Review new findings before updating the baseline; do not
  baseline real credentials.
- See [SECURITY.md](SECURITY.md) for private vulnerability reporting.

## Project structure

```text
llm_bench/                  CLI, providers, discovery, profiles, metrics
tests/                      deterministic unit tests
benchmark.example.json      explicit-model configuration example
benchmark.auto.example.json discovery and profile configuration example
.env.example                credential variable template
```

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md) for the test-driven workflow. This
project is available under the [MIT License](LICENSE).
