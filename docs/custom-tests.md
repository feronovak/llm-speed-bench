# Custom contract tests

Built-in tests answer whether a model is basically usable. Custom contract tests
answer the migration question that actually matters:

> Will the candidate model still satisfy the rules our feature depends on?

Write a small contract before approving a model change. Start with the outputs
your code, users, or downstream systems cannot safely handle being wrong.

```text
quick migration check → custom contract → repeated comparison → approve
```

## The smallest useful contract

A custom test has a name, one representative prompt, and a deterministic rule.
Keep it short and specific. A good first contract catches a failure that would
otherwise break production.

```json
{
  "name": "ticket-extraction",
  "description": "Extract fields required by our support workflow.",
  "system_prompt": "Return only valid JSON.",
  "prompt": "Urgent: payments are failing in Checkout.",
  "validation": {"contains": "priority"}
}
```

Put one or more of these objects in the top-level `prompts` list of your
benchmark config. Run one by name:

```bash
llm-bench benchmark.json --tests ticket-extraction
```

To compare a current model and a candidate, put both models in `models`. The
same contract runs against both, so the result tells you whether the candidate
can replace the incumbent for that rule.

## Start with these runnable examples

These examples use the local `mock` provider, so they need no API key and cost
nothing. Run one to see the result shape:

```bash
llm-bench examples/custom-contracts/ticket-extraction.json --no-save
llm-bench examples/custom-contracts/intent-routing.json --no-save
llm-bench examples/custom-contracts/content-rule.json --no-save
```

Then copy an example to your project, replace `local-example` with your current
and candidate models, and replace the prompt and expected rule with a real case.

| Example | Protects | Deterministic rule |
|---|---|---|
| [`ticket-extraction.json`](../examples/custom-contracts/ticket-extraction.json) | JSON extraction for a downstream workflow | JSON shape, required fields, and allowed priority values |
| [`intent-routing.json`](../examples/custom-contracts/intent-routing.json) | Exact routing to a queue or action | A full-response regular expression |
| [`content-rule.json`](../examples/custom-contracts/content-rule.json) | A required term or statement in a response | Required text fragment |

## Choose the right rule

Use the strictest rule that expresses your requirement:

| Requirement | Validation |
|---|---|
| A fixed label or exactly one allowed answer | `exact` or `regex`, for example `"^billing$"` |
| A required phrase, identifier, or citation | `contains` |
| A stable structured payload | `json_schema` |
| A flexible user-facing response | Start with `contains`, then add a human review case if wording matters |

Do not validate an open-ended answer with exact text unless the exact text is
truly a product requirement. That creates false failures from otherwise valid
wording changes. Conversely, do not use a loose `contains` rule for an output
your code parses—validate the JSON structure instead.

## A production JSON contract

Use a JSON Schema subset when your code needs fields and types, not just valid
JSON. This is a complete prompt object:

```json
{
  "name": "ticket-contract",
  "description": "Production ticket extraction contract.",
  "system_prompt": "Return only valid JSON with no Markdown.",
  "prompt": "Urgent: payments are failing in Checkout.",
  "presets": ["structured"],
  "request": {"temperature": 0, "max_output_tokens": 200},
  "validation": {
    "json_schema": {
      "type": "object",
      "required": ["product", "priority"],
      "properties": {
        "product": {"type": "string"},
        "priority": {"type": "string", "enum": ["high", "medium", "low"]}
      }
    }
  }
}
```

Run a cheap first comparison:

```bash
llm-bench benchmark.json --tests ticket-contract --smoke --dry-run
llm-bench benchmark.json --tests ticket-contract --smoke
```

When the candidate passes, repeat the contract from the same host before making
a cost or latency decision:

```bash
llm-bench benchmark.json --tests ticket-contract --dry-run
llm-bench benchmark.json --tests ticket-contract
```

## Keep contracts useful

- Use real but non-sensitive examples drawn from production failure modes.
- Keep prompts and expected output in version control when they are safe to
  share; otherwise keep the config and result directory private.
- Add a case when a real incident reveals a missing rule.
- Do not promote a model merely because it passes `--migration-check`; that
  verifies basic response behaviour, not your feature contract.
- Use `--dry-run` before every paid test and `--pricing-check` when cost matters.

See [Configuration](configuration.md) for every supported field and
[Tests, pricing, and safety](tests-pricing-safety.md) for validation and data
handling guidance.
