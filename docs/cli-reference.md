# CLI reference

Run `llm-preflight --help` for the installed version. The options below match this
release. `config` is a benchmark JSON path and is required unless `--init`,
`--quick`, `--diff`, or `--replay` is used.

`llm-preflight` is the primary command. From a source checkout, use
`python3 -m llm_preflight`.

| Option | Default | Purpose |
|---|---:|---|
| `--output-dir PATH` | `results` | Directory for saved result artifacts. |
| `--no-save` | off | Do not create result artifacts. |
| `--json` | off | Print the full result, plan, doctor report, or diff as JSON. |
| `--env NAME` | — | Apply a named configuration overlay. |
| `--smoke` | off | Set one repetition, no warmups, and concurrency one. |
| `--migration-check` | off | Run the three-case `quick-migration-check` response-contract preflight once per selected model. |
| `--doctor` | off | Validate configuration, keys, and model resolution; no generation. |
| `--pricing-check` | off | Report unknown or stale prices; no generation. |
| `--baseline PATH` | — | Compare a completed run with a saved result. |
| `--ci` | off | Return exit code 1 if a requested baseline/diff regression fails. |
| `--matrix` | off | Print model-by-test quality matrix instead of the normal report. |
| `--quick TEXT` | — | Run one ad hoc prompt; requires `--models`. |
| `--init [PATH]` | `benchmark.json` | Create a no-key mock config without overwriting a file. |
| `--models LIST` | — | Comma-separated `provider:model` list for `--quick`. |
| `--diff BASELINE CURRENT` | — | Compare two saved JSON result files; no benchmark run. |
| `--replay PATH` | — | Re-run the saved source configuration in a result artifact. |
| `--changed-since PATH` | — | With discovery, run models absent from a prior catalog JSON. |
| `--catalog` | off | Discover and print selected models; no generation. |
| `--tests LIST` | — | Comma-separated built-in/custom test selector. |
| `--profiles LIST` | — | Compatibility alias for `--tests`. |
| `--dry-run` | off | Print resolved work and cost estimate; no generation. |
| `--no-env-file` | off | Do not load the adjacent `.env.production`. |
| `--env-file PATH` | — | Load this env file instead of the default adjacent file. |
| `--stop-on MODE` | — | Stop after `api-error`, `test-fail`, or `any-fail`. |
| `--fail-fast` | off | Compatibility alias for `--stop-on any-fail`. |
| `--prompt NAME` | — | Run one named custom prompt from the config. |
| `--interactive` | off | Select models, tests, repetitions, and stop mode in the terminal. |
| `--approve-to PATH` | — | After an interactive saved run, offer passing models for explicit approval into this file. |

## Compatible combinations

- `--json` works with benchmark results, `--dry-run`, `--doctor`, `--diff`,
  and `--catalog`. `--pricing-check` and `--catalog` always print JSON.
- `--ci` gates `--diff` and `--baseline`; ordinary benchmark failures already
  exit with status 1.
- `--smoke`, `--env`, `--tests`, `--dry-run`, `--json`, `--no-save`, and
  `--stop-on` can be combined with a normal config run.

## Incompatible combinations and requirements

- `--quick` requires `--models` and does not use a config file.
- `--init` cannot be combined with `config`.
- `--diff` runs alone; it compares its two positional JSON files.
- `--profiles` and `--tests` cannot be combined.
- `--migration-check` cannot be combined with `--profiles`, `--tests`,
  `--prompt`, or `--interactive`.
- `--profiles`/`--tests` cannot be combined with `--prompt`.
- `--interactive` cannot be combined with `--catalog`, `--profiles`,
  `--tests`, or `--prompt`.
- `--no-env-file` and `--env-file` are mutually exclusive.
- `--approve-to` requires `--interactive` and cannot be combined with `--no-save`.

Omit `--stop-on` to run every selected model. The interactive menu calls that
choice `never`; it is not a command-line value.

## Built-in test packs

Use the names below in `--tests`; they are intentionally named for the decision
they support, not as broad claims about model intelligence.

| Test | Validates |
|---|---|
| `quick-migration-check` | API compatibility, basic response contract, TTFT, and latency. |
| `exact-routing-check` | Exact labels required by a downstream queue or action. |
| `structured-output-check` | JSON shape, required fields, and extracted values. |
| `numeric-instruction-check` | Numeric task correctness and concise instruction following. |
| `concurrency-health-check` | Basic reliability and latency at increasing concurrency. |

`chat-fast`, `classification`, `structured-extraction`, `reasoning`, and `load`
remain accepted as compatibility aliases for existing configurations.

## Model lifecycle commands

Use the catalogue lifecycle below for all new work. It keeps discovery, the
temporary candidate plan, benchmark execution, and permanent approval separate.

| Command | Purpose |
|---|---|
| `catalog init [DIRECTORY] [--providers LIST] [--replace]` | Create an ignored local workspace with `watch.json`, `approved.json`, `.env.production`, and `results/`. Without `--providers`, it asks once and Enter means all supported providers. For an existing workspace, it asks before rewriting only `watch.json`; `--replace` is the scripted equivalent and preserves approvals, keys, and results. |
| `catalog refresh WATCH_CONFIG` | Fetch provider metadata, classify catalogue entries, and update the local snapshot. It makes no generation requests. Optional legacy watch flags such as `--json`, `--snapshot`, and `--env-file` remain available. |
| `catalog prepare WATCH_CONFIG --against APPROVED --output CANDIDATES` | Group unapproved `text-ready` candidates by provider, require an explicit model selection, then write a temporary benchmark plan. `text-candidate` models first use `catalog probe`; non-text types are not offered to the generic chat benchmark. Use `--replace` only when deliberately rebuilding that plan. |
| `catalog probe WATCH_CONFIG [--models LIST]` | Review `text-candidate` models, then make one explicitly confirmed, provider-native minimal request per selection. Results are saved locally in `.llm-preflight/capabilities.json`; response text and keys are never stored. |
| `catalog test WATCH_CONFIG --approved APPROVED --output CONFIG` | Write a runnable benchmark plan for permanent approved models, using the test settings in the watch config. |
| `CANDIDATES --interactive --approve-to APPROVED` | Run the single interactive benchmark flow, then offer passing models for approval. |
| `models approve PROVIDER:MODEL --from RESULT --approved APPROVED` | Explicitly approve one passing model from a saved result, optionally with `--note TEXT`. |
| `models remove PROVIDER:MODEL --approved APPROVED` | Confirm and remove a permanent model while recording a removal timestamp and optional `--note TEXT`. |

Example:

```bash
llm-preflight catalog init
llm-preflight catalog refresh benchmarks/watch.json
# Run only when a selected text model is labelled "Needs one probe".
llm-preflight catalog probe benchmarks/watch.json
llm-preflight catalog prepare benchmarks/watch.json \
  --against benchmarks/approved.json \
  --output benchmarks/candidates.json
llm-preflight benchmarks/candidates.json --interactive \
  --approve-to benchmarks/approved.json
```

`watch-new` and `approve-model` are compatibility aliases for existing scripts.
They expose legacy options and are not the recommended workflow. See
[Model watch and approval](model-watch.md) for the complete tutorial.
