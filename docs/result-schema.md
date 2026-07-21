# Result JSON schema

`llm-preflight CONFIG --json` writes one result object to standard output. Saved
`results/*.json` files use the same schema. The current `schema_version` is
`1`; integrations should reject an unknown major schema version rather than
guessing its meaning.

## Top-level fields

| Field | Meaning |
|---|---|
| `run_id`, `timestamp`, `benchmark` | Unique run identifier, UTC timestamp, and config name. |
| `prompt_name`, `prompt_sha256`, `prompt_chars` | Selected custom-prompt name when applicable, plus safe prompt identity metadata. |
| `settings` | Effective repetitions, warmups, concurrency, timeout, request options, and selected profiles. |
| `environment` | Hostname and Python version that produced the evidence. |
| `models` | One result object per attempted model, in run order. |
| `total_input_tokens`, `total_output_tokens`, `total_estimated_cost_usd` | Totals including warmups. Cost is `null` if any model has unknown pricing. |
| `pricing_warnings` | Pricing freshness or availability warnings. |
| `source_config` | Redacted input configuration, retained for replay and audit. |
| `source_config_path` | Absolute source configuration path when available; `--replay` uses its adjacent `.env.production` by default. |

## Model result

Each `models[]` object identifies the resolved provider/model and includes:

| Field | Meaning |
|---|---|
| `name`, `provider`, `model` | Human label and resolved model identity. |
| `summary` | Measured requests, validation, timing, usage, cost, retries, and failures. |
| `warmup_summary` | The same metrics for warmup requests; exclude it from your quality gate. |
| `samples` | Per-request observations for a plain prompt, subject to `save_responses`. |
| `profiles` | Per-test-pack results instead of top-level samples when using `--tests`. |

`summary` fields include `requests`, `successful`, `failed`, `success_rate`,
and `valid_output_rate`. The last field is the application-quality gate: a
response can be successful at HTTP/API level yet invalid for its evaluator.
`latency_seconds`, `ttft_seconds`, and `output_tokens_per_second` are objects
with `mean`, `min`, `p50`, `p95`, and `max` (except where a metric only has a
meaningful subset). They are `null` when unavailable. `estimated_cost_usd` is
`null` for unknown pricing. Summaries also include `cached_input_tokens` when
the provider reports cache hits; output usage includes billable reasoning tokens.

`output_tokens_per_second` is also `null` when the stream was not observably
incremental — fewer than two text chunks, or a generation window under 100 ms.
Some providers buffer the whole response server-side and burst it at the end;
the post-TTFT window then measures transport, not generation, and reporting a
rate would inflate throughput by orders of magnitude.

Failure and retry diagnostics are safe aggregates: `failure_reasons`,
`failure_categories`, `retry_count`, `retry_reasons`, and `failure_hints`.

## Samples and profile cases

A plain-prompt `samples[]` entry includes `ok`, `valid_output`,
`quality_score`, `evaluation_error`, `failure_category`, `attempts`,
`retry_count`, `retry_reasons`, timing, token counts, cost inputs, and an
optional response or response preview according to `save_responses`.

For a profile run, `profiles[]` contains `name`, `description`, `summary`, and
per-case `samples`. Each sample has `case_id`; concurrency-health samples also
have `concurrency`.

Do not parse terminal tables. Prefer `valid_output_rate` for application
quality, `success_rate` for transport reliability, and `failure_category` for
automation decisions. Treat absent optional fields and `null` metrics as
unknown rather than zero.

## Baseline comparison JSON

`llm-preflight --diff BASELINE CURRENT --json --ci` writes a separate comparison
object: `ok` plus `models[]`. A compared row exposes
`latency_p95_delta_seconds`, `success_rate_delta`, `valid_output_rate_delta`,
`cost_delta_usd`, and `regressions`. An added model has `status: "added"` and
no regression by itself. A non-empty `regressions` list makes `--ci` exit 1.
