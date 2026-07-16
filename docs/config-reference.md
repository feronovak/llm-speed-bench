# Configuration reference

Configurations are JSON. A config requires `models` or `discovery`, and either
one top-level `prompt` or one or more named `prompts`.

## Top-level keys

| Key | Default | Meaning |
|---|---:|---|
| `name` | `llm-benchmark` | Label stored in results. |
| `prompt` | — | Single prompt when not selecting test profiles. |
| `models` | `[]` | Explicit model objects or alias strings. |
| `discovery` | `[]` | Provider-catalog sources. |
| `prompts` | `[]` | Named reusable custom prompt tests. |
| `profiles` | — | Built-in/custom test selector; use `all` for built-ins. |
| `repetitions` | `5` | Measured runs per model for a single top-level prompt. |
| `suite_repetitions` | `1` | Repetitions of each case in selected test profiles. |
| `warmups` | `1` | Per-model warmup calls; per profile when tests are selected. |
| `concurrency` | `1` | Parallel calls for a top-level prompt. |
| `timeout_seconds` | `120` | Provider connect/read timeout, not a total streaming deadline. |
| `request` | see below | Shared request options. |
| `validation` | non-empty | Top-level response checks. |
| `presets` | `[]` | Shared provider-aware presets. |
| `save_responses` | `false` | `true`, `false`, or `failures`. |
| `stop_on` | none | `api-error`, `test-fail`, or `any-fail`. |
| `fail_fast` | `false` | Legacy boolean equivalent to `stop_on: any-fail`. |
| `max_requests` | — | Reject a run whose retry-expanded request maximum exceeds this. |
| `max_estimated_cost_usd` | — | Reject a run whose retry-expanded cost exceeds this. |
| `aliases` | `{}` | Named model definitions. |
| `environments` | `{}` | Named shallow config overlays selected with `--env`. |
| `approvals` | `[]` | Local `models approve` audit entries; ignored by benchmark runs. |

## `request`

| Key | Default | Meaning |
|---|---:|---|
| `temperature` | provider default | Sampling temperature, unless the model disables it. |
| `max_output_tokens` | `256` for planning | Maximum generated tokens. `max_tokens` is accepted for compatibility. |
| `system_prompt` | — | Shared system instructions. |
| `provider_options` | `{}` | Provider request body fields; one object or `{all, provider}` maps. |
| `retry` | two attempts | `true` or an object described below. |

For the `mock` provider only, `response` supplies the returned text when it is
not set on the model.

`retry` accepts `max_attempts` (2), `initial_delay_seconds` (0.25),
`max_delay_seconds` (4), `backoff_multiplier` (2), `jitter_seconds` (0.1),
and `retry_on` (`rate_limit`, `timeout`, `transient_provider`, `network`).

## `models` and `discovery`

A model requires `model`; `provider` defaults to `openai_compatible`. Useful
optional model fields are `name`, `base_url`, `api_key_env`, `api_version`,
`headers`, `input_cost_per_million`, `output_cost_per_million`,
`max_tokens_parameter`, and `supports_temperature`. `capabilities` is advanced
provider metadata; catalogue refresh and probes maintain it automatically, so
most users should not set it manually. Mock models also accept `response`,
`latency_seconds`, and `ttft_seconds` for deterministic local fixtures.

Supported provider names are `openai`, `anthropic`, `gemini`, `xai`,
`openrouter`, `openai_compatible`, and `mock`. A discovery object requires
`provider` and positive `limit`; it can also set case-insensitive regex
`include`/`exclude`, `sort`, `output_modalities`, `require_parameters`,
`base_url`, `api_key_env`, `api_version`, and `headers`. `output_modalities`
and `require_parameters` are OpenRouter catalog filters.

## `prompts`, validation, aliases, and environments

Each prompt requires unique `name` and either non-empty `prompt` or a relative
`prompt_file` within the config directory. Optional keys are `description`,
`system_prompt`, `request`, `validation`, and `presets`.

Validation supports `contains`, `regex`, and `json_schema`; an absent custom
validation means non-empty output. Built-in packs also use exact, numeric, and
JSON-subset evaluators. The supported JSON Schema subset handles object
`required`/`properties`, arrays and item limits, primitive `type`, and `enum`.

`aliases` maps a name to a model object; use its name in `models`. An
`environments` item is a shallow overlay—its top-level keys replace the base
config, including a complete `request` object when supplied.

See [configuration examples](configuration.md) and the checked-in example JSON
files for complete runnable examples.
