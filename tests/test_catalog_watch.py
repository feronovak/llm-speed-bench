from llm_bench.catalog_watch import (
    build_candidate_config,
    catalog_diff,
    snapshot_catalog,
)


def _model(provider, model, **extra):
    return {"provider": provider, "model": model, "name": model, **extra}


def test_catalog_diff_groups_added_removed_renamed_and_changed_entries():
    previous = snapshot_catalog(
        [
            _model("openai", "old"),
            _model("openai", "same", input_cost_per_million=1),
            _model("openai", "named", name="Old display name"),
        ]
    )
    current = snapshot_catalog(
        [
            _model("openai", "same", input_cost_per_million=2),
            _model("openai", "named", name="New display name"),
            _model("openai", "new"),
        ]
    )

    diff = catalog_diff(previous, current)

    assert [(item["provider"], item["model"]) for item in diff["added"]] == [
        ("openai", "new")
    ]
    assert [(item["provider"], item["model"]) for item in diff["removed"]] == [
        ("openai", "old")
    ]
    assert diff["renamed"] == [
        {
            "provider": "openai",
            "model": "named",
            "before": "Old display name",
            "after": "New display name",
        }
    ]
    assert diff["changed"][0]["fields"] == ["input_cost_per_million"]


def test_catalog_diff_ignores_first_time_capability_classification():
    diff = catalog_diff(
        [_model("openai", "model")],
        [
            _model(
                "openai",
                "model",
                catalog_type="text-chat",
                catalog_confidence="aggregator",
            )
        ],
    )

    assert diff["changed"] == []


def test_catalog_diff_ignores_capability_metadata_when_migrating_legacy_snapshot():
    previous = [
        _model(
            "openai",
            "model",
            input_cost_per_million=1,
            capabilities={"text_generation": "candidate"},
        )
    ]
    current = snapshot_catalog(
        [
            _model(
                "openai",
                "model",
                input_cost_per_million=2,
                capabilities={"text_generation": "ready", "adapter": "responses"},
                catalog_type="text-ready",
                catalog_confidence="official",
            )
        ]
    )

    diff = catalog_diff(previous, current)

    assert diff["changed"] == [
        {
            "provider": "openai",
            "model": "model",
            "fields": ["input_cost_per_million"],
        }
    ]


def test_candidate_config_combines_new_candidates_with_approved_models():
    config = {
        "prompt": "hello",
        "models": [{"provider": "openai", "model": "candidate"}],
        "discovery": [{"provider": "openai", "limit": 10}],
    }
    approved = {
        "models": [{"provider": "openai", "model": "incumbent"}],
    }

    candidate = build_candidate_config(
        config,
        approved,
        [{"provider": "openai", "model": "candidate"}],
    )

    assert candidate["models"] == [
        {"provider": "openai", "model": "incumbent"},
        {"provider": "openai", "model": "candidate"},
    ]
    assert candidate["discovery"] == []
