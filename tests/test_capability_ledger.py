from llm_bench.capability_ledger import apply_probe_evidence, load_ledger, record_probe


def test_probe_ledger_records_account_specific_evidence(tmp_path):
    path = tmp_path / "capabilities.json"

    record_probe(
        path,
        {
            "provider": "openai",
            "model": "gpt-test",
            "adapter": "openai_responses",
            "outcome": "text-ready",
            "fingerprint": "version-1",
            "request_options": {"max_output_tokens": 32},
        },
    )

    ledger = load_ledger(path)

    assert ledger["schema_version"] == 1
    assert ledger["probes"]["openai:gpt-test"]["outcome"] == "text-ready"
    assert ledger["probes"]["openai:gpt-test"]["request_options"] == {
        "max_output_tokens": 32
    }
    assert path.stat().st_mode & 0o077 == 0


def test_transient_probe_outcome_does_not_hide_a_model_from_retry(tmp_path):
    path = tmp_path / "capabilities.json"
    record_probe(
        path,
        {
            "provider": "openai",
            "model": "gpt-test",
            "outcome": "indeterminate",
            "error_category": "timeout",
        },
    )

    models = apply_probe_evidence(
        [{"provider": "openai", "model": "gpt-test", "catalog_type": "text-candidate"}],
        load_ledger(path),
    )

    assert models[0]["catalog_type"] == "text-candidate"
