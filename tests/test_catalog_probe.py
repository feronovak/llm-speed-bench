from llm_bench.catalog_probe import probe_model


def test_probe_records_success_without_saving_response(monkeypatch, tmp_path):
    class Client:
        def run(self, *_args, **_kwargs):
            return {"ok": True, "failure_category": None, "response": "OK"}

    monkeypatch.setattr("llm_bench.catalog_probe.create_client", lambda *_: Client())

    probe = probe_model(
        {
            "provider": "openai",
            "model": "gpt-test",
            "capabilities": {"adapter": "openai_responses"},
        },
        tmp_path / "capabilities.json",
    )

    assert probe["outcome"] == "text-ready"
    assert "response" not in probe


def test_probe_keeps_credential_failures_indeterminate(monkeypatch, tmp_path):
    class Client:
        def run(self, *_args, **_kwargs):
            return {
                "ok": False,
                "failure_category": "credentials",
                "error": "HTTP 401",
            }

    monkeypatch.setattr("llm_bench.catalog_probe.create_client", lambda *_: Client())

    probe = probe_model(
        {"provider": "openai", "model": "gpt-test"},
        tmp_path / "capabilities.json",
    )

    assert probe["outcome"] == "indeterminate"
    assert probe["error_category"] == "credentials"


def test_probe_uses_failure_category_not_error_text_for_transient_failures(
    monkeypatch, tmp_path
):
    class Client:
        def run(self, *_args, **_kwargs):
            return {
                "ok": False,
                "failure_category": "timeout",
                "error": "endpoint parameter timed out",
            }

    monkeypatch.setattr("llm_bench.catalog_probe.create_client", lambda *_: Client())

    probe = probe_model(
        {"provider": "openai", "model": "gpt-test"},
        tmp_path / "capabilities.json",
    )

    assert probe["outcome"] == "indeterminate"
