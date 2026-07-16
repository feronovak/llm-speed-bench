# Contributing

## Development setup

Python 3.10 or newer is required.

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e ".[dev]"
make test
make coverage
```

Unit tests use deterministic mocked provider responses and must not require API
keys or network access.

## Development workflow

This project follows a strict red/green/refactor loop:

1. Add the smallest test describing the externally observable change.
2. Run the focused test and confirm it fails for the expected reason.
3. Implement the smallest production change that makes it pass.
4. Run `make test`.
5. Refactor only while the complete suite remains green.

Provider changes must include mocked protocol fixtures. Live requests may be
used as optional verification, but never as part of the unit suite.

## Code map

- `llm_preflight/cli.py` owns commands, interactive prompts, and output boundaries.
- `runner.py` owns benchmark orchestration, validation application, reports, and
  saved artifacts; `metrics.py` summarizes samples.
- `client.py` owns provider adapters, request translation, retries, and timing.
- `catalog.py`, `catalog_probe.py`, `catalog_watch.py`, and
  `capability_ledger.py` own discovery and compatibility evidence.
- `features.py` owns planning, budgets, baselines, CI comparison, and replay.
- `profiles.py` defines built-in tests and deterministic evaluators;
  `pricing.py`, `presets.py`, `env.py`, `redaction.py`, and `security.py` are
  focused supporting modules.
- Matching `tests/test_*.py` files are the executable behavior contract.

## Pull requests

- Keep changes focused and explain user-visible behavior.
- Include tests for bug fixes and behavior changes.
- Do not commit credentials, `.env.production`, benchmark results, prompts
  containing private data, or provider response payloads.
- Update public pricing with an official source and an `as_of` date.
- Run `make audit` with the `audit` optional dependencies before submitting
  security-sensitive changes.
