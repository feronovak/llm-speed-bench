# Migrating to LLM Preflight

LLM Speed Bench is now **LLM Preflight**. The change gives the tool a name that
describes its job: check a model switch against your own contracts before it
reaches production.

## Existing users

Install the new distribution at your next upgrade:

```bash
python3 -m pip install --upgrade llm-preflight
```

Use the new primary command in new scripts and documentation:

```bash
llm-preflight benchmark.json --migration-check
```

Your existing command continues to work unchanged:

```bash
llm-bench benchmark.json --migration-check
```

Both commands execute the same installed program with the same flags and
results. Existing JSON configurations, saved results, and the local
`.llm-bench/` catalogue workspace do not need to move or change.

## Python integrations

The Python module remains `llm_bench` for compatibility:

```python
from llm_bench.runner import run_benchmark
```

No code change is required for that import path.

## Package and repository transition

New installs use `llm-preflight`. The former `llm-speed-bench` PyPI project is
being retained as a compatibility shim that depends on LLM Preflight; it is not
a second codebase. Repository links from the former GitHub name redirect after
the repository rename. Keep the old repository name unclaimed so those links
continue to redirect.

## For CI owners

Change installation commands first, then change command invocations when you
touch the workflow. This is safe to do in separate pull requests because both
commands remain supported during the transition:

```yaml
- run: python -m pip install --upgrade llm-preflight
- run: llm-preflight benchmark.json --json --no-save > current.json
```

No result-schema migration is required.
