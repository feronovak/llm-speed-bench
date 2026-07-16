# Model catalogue: discover, test, and keep models

Use the catalogue when you want a small, trusted list of models for your own
work. It is deliberately a local workflow: provider catalogues are broad;
your approved list contains only models you chose after testing.

```text
provider metadata → confirm compatibility → benchmark → approve → re-test
```

You do not need to edit the catalogue files by hand. Commands create and update
them for you. Discovery reads metadata only; a probe or benchmark is the point
where a provider may charge for a request.

## The four files, in plain English

Run this once in an empty project directory:

```bash
llm-bench catalog init
```

Press Enter to include all supported providers. The command creates:

```text
benchmarks/
├── watch.json       # what to discover and how candidate tests should run
├── approved.json    # models you deliberately keep
├── candidates.json  # temporary test plan; created later
├── results/         # saved benchmark evidence
└── .llm-bench/      # automatic catalogue snapshots and probe evidence
```

Keep API keys in `benchmarks/.env.production`, never in JSON or Git. If your
project already has a neighbouring `.env.production`, setup reuses it rather
than copying keys. `catalog init` asks before changing an existing workspace.

## First time: choose models without guessing

### 1. Refresh the provider catalogues

```bash
llm-bench catalog refresh benchmarks/watch.json
```

This makes metadata requests only. It tells you what changed since the last
refresh; `Added: 0` simply means the provider IDs did not change. It does not
mean there are no models, and it never sends your benchmark prompt.

Each discovered model is placed in one of three useful groups:

| Group | Meaning | What you do |
|---|---|---|
| **Ready to benchmark** | Provider metadata, or a previous successful probe, identifies a supported text adapter. | Select it for a candidate benchmark. |
| **Needs one probe** | It looks like a text model but metadata cannot safely prove the request shape. | Optionally make one small, provider-native request. |
| **Not a generic text benchmark model** | It is image, audio, video, realtime, agent, or another incompatible endpoint. | Leave it out of a normal text suite. |

The third group remains visible. It is not silently deleted or treated as a
failed chat model.

### 2. Probe only the text candidates you care about

If refresh reports **Needs one probe**, review those models first:

```bash
llm-bench catalog probe benchmarks/watch.json
```

Select one or a few model numbers, then confirm. The probe sends one minimal
request and may be billable. It stores no response text—only the outcome,
adapter, safe request settings, and a provider fingerprint when available.
Successful models become **Ready to benchmark** locally. A provider fingerprint
change automatically expires that evidence and asks for a new probe.

Do not probe everything merely because it is listed. Use it for models you are
actually considering. Account access, regions, and provider rollouts can make a
model available to one user and unavailable to another.

### 3. Make a temporary candidate plan

```bash
llm-bench catalog prepare benchmarks/watch.json \
  --against benchmarks/approved.json \
  --output benchmarks/candidates.json
```

The selector shows only **Ready to benchmark** models. Pick the few models you
want to compare; `all` means all displayed compatible text models, never every
ID in every provider catalogue. The resulting `candidates.json` is temporary.
It does not change your approved list.

If you intentionally want a fresh temporary plan, make replacement explicit:

```bash
llm-bench catalog prepare benchmarks/watch.json \
  --against benchmarks/approved.json \
  --output benchmarks/candidates.json --replace
```

### 4. Run the response-and-contract comparison

Start with the three-case migration check. It makes the smallest useful paid
comparison: can each model respond through your account, meet a basic response
contract, and respond quickly enough from this host?

```bash
llm-bench benchmarks/candidates.json --migration-check --dry-run
llm-bench benchmarks/candidates.json --migration-check \
  --output-dir benchmarks/results
```

Then use interactive mode for the task-specific contract that matters to your
application—for example structured output or exact routing:

```bash
llm-bench benchmarks/candidates.json --interactive \
  --approve-to benchmarks/approved.json \
  --output-dir benchmarks/results
```

Interactive mode has one clear job: select the benchmark work, preview its
maximum request count and estimated cost, then confirm the run. A compatibility
screen with one repetition answers “does this model work for this suite?” It is
not enough evidence for a stable latency ranking. See [Interactive mode](interactive.md).

### 5. Keep only passing models

At the end, the command lists passing models that are not already approved.
Choose the ones you want, confirm, and they are added to `approved.json` with
the saved result as evidence. Skipping a model is fine: it remains only in the
result history, not in your permanent set.

To promote a passing model later, use its saved result:

```bash
llm-bench models approve openai:MODEL_ID \
  --from benchmarks/results/RUN.json \
  --approved benchmarks/approved.json \
  --note "Passed our candidate review."
```

## Routine maintenance

### A provider announces a model

Repeat this short loop:

```text
refresh → probe only candidates you want → migration check → contract test → approve
```

Existing approvals do not change during discovery. If a selected model returns
404, it is not available to your account or has been retired; do not approve it.

### Re-test the models you already use

`approved.json` is a record of decisions, not a runnable benchmark. Build a
test plan from it, then run that plan:

```bash
llm-bench catalog test benchmarks/watch.json \
  --approved benchmarks/approved.json \
  --output benchmarks/approved-tests.json
llm-bench benchmarks/approved-tests.json --interactive \
  --output-dir benchmarks/results
```

Use the tests that resemble your workload. For a performance conclusion, use
several repetitions from the same host; one repetition is a cheap compatibility
check only.

### Remove a model you no longer want

```bash
llm-bench models remove PROVIDER:MODEL_ID \
  --approved benchmarks/approved.json \
  --note "Retired by provider."
```

The command confirms the change and preserves old result files as history.

## Useful checks

Before any paid run, inspect it without sending requests:

```bash
llm-bench benchmarks/candidates.json --dry-run
llm-bench benchmarks/candidates.json --pricing-check
```

Unknown pricing is shown as `n/a`, not free. Keep `max_requests` and, where
pricing is complete, `max_estimated_cost_usd` in `watch.json` to limit risk.

For the complete command forms, see the [CLI reference](cli-reference.md). For
common problems, see [Troubleshooting](troubleshooting.md).

`watch-new` and `approve-model` remain compatibility aliases for older scripts.
Use the `catalog` and `models` commands in new work.
