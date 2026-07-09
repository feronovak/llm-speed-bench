.PHONY: audit coverage test test-one

test:
	python3 -m pytest -q

coverage:
	python3 -m coverage run -m pytest -q
	python3 -m coverage report

audit:
	ruff format --check . --exclude results
	ruff check . --exclude results
	mypy llm_bench
	bandit -q -r llm_bench
	detect-secrets-hook --baseline .secrets.baseline \
		llm_bench tests .github .env.example \
		benchmark.example.json benchmark.auto.example.json pyproject.toml

test-one:
	@test -n "$(TEST)" || (echo "Usage: make test-one TEST=path::test_name" && exit 2)
	python3 -m pytest -q "$(TEST)"
