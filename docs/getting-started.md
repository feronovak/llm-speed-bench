# Getting started

LLM Preflight answers a practical question before you change production:

> Can this candidate model replace our current model without breaking the
> response contract, latency expectations, or budget?

It runs locally against the providers you choose. It is not a hosted dashboard
or a public leaderboard. Results describe your prompt, account, host, and test
settings.

## First, see a safe local run

This creates a deterministic mock benchmark. It needs no API key, network, or
paid request:

```bash
llm-preflight --init
llm-preflight benchmark.json --no-save
```

The terminal report separates provider failures from failed output validation.
`--init` refuses to overwrite an existing config. Press Ctrl-C at any prompt or
run to cancel; the command exits 130 and saves no partial artifact.

## Then run a quick migration check

Use the checked-in example as your starting point:

```bash
cp benchmark.example.json benchmark.json
cp .env.example .env.production
```

Open `benchmark.json` and keep only the model entries you want. Put the matching
provider key—such as `OPENAI_API_KEY`—in `.env.production`. Never put keys in
the JSON file or commit the env file.

Always inspect a live plan before you run it:

```bash
llm-preflight benchmark.json --doctor
llm-preflight benchmark.json --dry-run
llm-preflight benchmark.json --migration-check
```

`--doctor` checks configuration and required credentials without generation.
`--dry-run` shows models, tests, nominal and retry-expanded request counts,
estimated cost, stop behaviour, and response retention. The final command sends
three short response-contract cases to every selected model, once each.

Use `--migration-check` when comparing a current model with a candidate. It
sets the quick `quick-migration-check` test, one repetition, no warmups, and
concurrency one.
It tells you whether the provider worked, whether responses were valid, and
their first-token/end-to-end latency. It does not prove a stable performance
ranking; run a repeated task-specific test after it passes.

For an easier first paid run, use the guided terminal screen instead:

```bash
llm-preflight benchmark.json --interactive
```

It asks what to test, previews the paid plan, and requires confirmation before
requests are made.

## Read the result

Unless `--no-save` is used, each run creates JSON evidence and a readable
Markdown report under `results/`. The report compares:

- **Quality/reliability** — did the output satisfy the chosen rule?
- **Latency** — how long requests took from your host.
- **Tokens and estimated cost** — only when providers expose usable usage and
  pricing data.

A fast response that fails JSON, exact-label, or numeric validation is a failed
result. A model with `n/a` cost has unknown pricing; it is not free.

### A first report, explained

The mock demo produces the same kind of evidence as a paid run. Its Markdown
report has a results table, a quality gate, and a decision section:

```text
Model  | Valid | Latency p95 | Cost
local  | 100%  | 0.010s      | $0.000000

QUALITY GATE
local  PASS  -

DECISION
Recommended: local — passed every selected test and led the qualified value ranking.
```

`API OK` means the provider returned a response. `TEST OK` means that response
also passed your rule; `API OK / TEST FAIL` is usable API connectivity but an
invalid application result. `Recommended` compares only models that passed
every selected test. It says `unavailable` when no model qualified, pricing is
unknown for every qualified model, or there is no comparable latency evidence.
Open the saved `.md` report for a readable record and the matching `.json` file
when a script needs the detailed evidence.

## Choose your next path

**You already know the models you want to compare.** Continue with
[Configuration](configuration.md), then write one [custom contract test](custom-tests.md)
that represents the output your feature must preserve.

**You want help finding newly released models.** Follow [Model catalogue](model-watch.md).
It creates a local list, classifies provider models, asks before any small
compatibility probe, and lets you approve only models that pass your tests.

**You are automating a known benchmark.** Start with [CI and JSON output](ci.md).

The [Workflows](workflows.md) page collects the common commands, and
[Tests, pricing, and safety](tests-pricing-safety.md) explains how to interpret
the numbers safely.
