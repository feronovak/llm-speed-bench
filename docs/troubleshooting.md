# Troubleshooting

## `catalog` is an unrecognized command

Your shell is using an older installation. Reinstall the checkout:

```bash
python3 -m pip install --user --upgrade --no-build-isolation .
llm-preflight catalog --help
```

## Catalogue refresh says API keys are missing

The message lists every missing key and the exact `.env.production` file used
by that workspace. Add keys there, or create the workspace beside your existing
project-level `.env.production`; `catalog init` links to that file rather than
copying credentials.

## A workspace already exists

`catalog init` asks before changing its discovery settings. Enter keeps the
workspace. Confirming rewrite keeps approved models, keys, and saved results.

## A model returns HTTP 404

The model is unavailable to the account or retired. Do not approve it. Remove
it from permanent state with `models remove PROVIDER:MODEL --approved APPROVED`
when applicable.

## The catalogue says “Needs one probe”

The provider lists something that looks like a text model but does not expose
enough metadata to choose a safe request shape automatically. It is not a
failure. If you are considering that model, run:

```bash
llm-preflight catalog probe benchmarks/watch.json
```

Select it, review the confirmation, and allow one minimal request. If you are
not considering it, leave it alone. Models marked “Not a generic text benchmark
model” are visible for reference but do not belong in a normal chat/text suite.
If the probe reports a credential error, fix the named API key and probe again;
the tool does not record that as a model retirement.

## A model fails JSON or numeric validation

It passed the provider request but did not satisfy that strict contract. Do not
approve it for the current suite. Retest later with a task-appropriate prompt
or provider-native structured-output settings.

## I skipped approval or want to add a passing model later

Use the saved result:

```bash
llm-preflight models approve PROVIDER:MODEL --from RESULT.json --approved APPROVED.json
```

## I want to re-test approved models

`approved.json` is state, not a runnable benchmark config. Build the plan with
`catalog test WATCH.json --approved APPROVED.json --output approved-tests.json`,
then run `approved-tests.json` normally or with `--interactive`.

## A benchmark is already running for this results folder

Only one benchmark can use an output folder at a time. This prevents an
accidental second terminal from duplicating paid requests. Wait for the active
run to finish, or deliberately use a different `--output-dir` for an
independent run.
