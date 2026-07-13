# Configuration

## Models and providers

Every `models` entry has the same basic shape:

```json
{
  "name": "label-in-reports",
  "provider": "openai",
  "model": "provider-model-id",
  "input_cost_per_million": 0,
  "output_cost_per_million": 0
}
```

| Provider | Default API key variable |
|---|---|
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `gemini` | `GEMINI_API_KEY` |
| `xai` | `XAI_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |
| `openai_compatible` | configured with `api_key_env` |

Override endpoint and authentication details with `base_url`, `api_key_env`,
and `headers`. Use `provider_options` inside `request` only when normalized
settings such as temperature, system prompt, and output limit are insufficient.

## Custom prompts

Custom prompts are named, reusable tests:

```json
{
  "prompts": [{
    "name": "csv-review",
    "system_prompt": "Return concise JSON.",
    "prompt": "Find invalid rows in this CSV:\\norder_id,total\\nA-1,20\\nA-2,-5",
    "request": {"temperature": 0, "max_output_tokens": 500},
    "validation": {"contains": "A-2"}
  }]
}
```

```bash
llm-bench benchmark.json --prompt csv-review
llm-bench benchmark.json --tests classification,csv-review
```

For long content, use `prompt_file` relative to the config file. It must remain
inside that directory; `..` paths are rejected. Use `prompt` or `prompt_file`,
not both, and do not commit sensitive fixture data.

## Presets, aliases, and environments

Presets translate intent into provider-aware options. Available presets are
`json`, `no-reasoning`, `low-latency`, and `structured`. Explicit request
values always win over preset defaults.

```json
{
  "aliases": {"fast": {"provider": "openai", "model": "gpt-5.4-mini"}},
  "environments": {
    "ci": {"repetitions": 1, "warmups": 0},
    "deep": {"profiles": "all", "suite_repetitions": 20}
  },
  "models": ["fast"]
}
```

Run an overlay with `llm-bench benchmark.json --env ci`. See
[tests, pricing, and safety](tests-pricing-safety.md) for validation choices and
data handling.
