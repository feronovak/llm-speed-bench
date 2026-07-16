# Tests, pricing, and safety

## Built-in tests and validation

Built-in packs are `quick-migration-check`, `exact-routing-check`,
`structured-output-check`, `numeric-instruction-check`, and
`concurrency-health-check`. The concurrency pack intentionally expands work at
concurrency 1, 5, and 10; keep it separate from normal interactive-latency
comparisons.

Use `--migration-check` for the smallest real response check. It runs the three
`quick-migration-check` cases once per selected model, with no warmups and
concurrency one.
It answers “does this candidate respond and meet a basic contract here?” before
a model switch. It is not a reliable latency ranking; use several repetitions
for that, and run `concurrency-health-check` separately for concurrency behaviour.

Use `"profiles": "all"` for the full built-in suite or select a mixed subset
with `--tests`. The evaluator supports exact matches, numeric answers, JSON
subsets, regular expressions, contains checks, and a structural `json_schema`
subset. Validation failures are test failures even when the API responded.

## Fair comparisons and retries

Keep the prompt, system prompt, temperature, and maximum output fixed. Run from
the same host, pin dated model IDs where possible, and use at least 20 measured
repetitions for meaningful latency comparison.

Retryable rate limits, selected 5xx responses, temporary network failures, and
timeouts retry once by default. Configure `request.retry` to change attempts,
backoff, and bounded jitter. Plans include every selected profile case, warmup,
request override, and retry-expanded cost ceiling; results record retry counts
and final failure categories. Latency and TTFT for a successful request measure
its final network attempt, not prior retry backoff; retry counts remain visible
so a fast recovered request is not mistaken for an uninterrupted one. Malformed
provider responses are deterministic failures and are not retried.
Socket-level connection failures are retried according to `retry_on: network`;
the classification uses the transport exception rather than provider-specific
error wording.

A catalogue probe is different from a benchmark: it sends one minimal request
only after explicit confirmation, to establish whether a selected text candidate
has a usable provider adapter. It may be charged. The local capability ledger
stores the outcome and safe request shape, not response text; a changed provider
fingerprint expires the prior probe result.

## Pricing confidence

OpenRouter pricing comes from its live catalog and is labelled `openrouter
routed` with authoritative confidence: it applies when the benchmark is routed
through OpenRouter. Selected direct OpenAI, Gemini, Anthropic, and xAI prices
are maintained as timestamped `official snapshot` records. Unknown prices stay
unknown; the tool never silently treats an OpenRouter route as a direct-provider
price.
Explicit per-model prices override the registry. Estimated spend excludes taxes,
account-specific discounts, cache discounts, tool fees, and similar adjustments.

Run `--dry-run` or `--pricing-check` after pricing edits. Treat unknown or
stale prices as a reason not to compare cost rankings.

## Sensitive data

Secrets are read from environment variables or the selected env file. The CLI
redacts common secret names, custom headers, dry-run output, saved results, and
provider errors. Prompts, model metadata, and retained failed responses can
still contain business data: review result artifacts before sharing them.

Never commit `.env.production`, raw results, private prompts, or provider
responses. See [SECURITY.md](../SECURITY.md) to report a vulnerability.
