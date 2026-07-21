import pytest

from llm_preflight.metrics import percentile, stats, summarize


def test_percentile_interpolates():
    assert percentile([1, 2, 3, 4, 5], 0.95) == 4.8
    assert percentile([], 0.5) is None


def test_summary_excludes_failed_samples_and_calculates_cost():
    samples = [
        {
            "ok": True,
            "latency_seconds": 2,
            "ttft_seconds": 0.5,
            "output_tokens_per_second": 10,
            "input_tokens": 100,
            "output_tokens": 20,
        },
        {
            "ok": False,
            "latency_seconds": 1,
            "ttft_seconds": None,
            "output_tokens_per_second": None,
            "input_tokens": None,
            "output_tokens": None,
        },
    ]
    result = summarize(
        samples, {"input_cost_per_million": 1, "output_cost_per_million": 2}
    )
    assert result["success_rate"] == 0.5
    assert result["latency_seconds"]["mean"] == 2
    assert result["estimated_cost_usd"] == pytest.approx(0.00014)


def test_summary_counts_billable_tokens_for_validation_failures():
    samples = [
        {
            "ok": False,
            "latency_seconds": 1,
            "ttft_seconds": 0.1,
            "output_tokens_per_second": 2,
            "input_tokens": 10,
            "output_tokens": 5,
            "error": "response did not match regex",
        }
    ]

    result = summarize(
        samples, {"input_cost_per_million": 1, "output_cost_per_million": 2}
    )

    assert result["successful"] == 0
    assert result["input_tokens"] == 10
    assert result["output_tokens"] == 5
    assert result["estimated_cost_usd"] == pytest.approx(0.00002)


def test_summary_uses_cached_input_and_per_request_pricing_tiers():
    samples = [
        {
            "ok": True,
            "latency_seconds": 1,
            "ttft_seconds": 0.1,
            "output_tokens_per_second": 2,
            "input_tokens": 100,
            "cached_input_tokens": 80,
            "output_tokens": 10,
        },
        {
            "ok": True,
            "latency_seconds": 1,
            "ttft_seconds": 0.1,
            "output_tokens_per_second": 2,
            "input_tokens": 201,
            "output_tokens": 10,
        },
    ]
    model = {
        "input_cost_per_million": 1,
        "output_cost_per_million": 2,
        "cached_input_cost_per_million": 0.25,
        "pricing_tiers": [
            {
                "up_to_input_tokens": 200,
                "input_cost_per_million": 1,
                "output_cost_per_million": 2,
                "cached_input_cost_per_million": 0.25,
            },
            {
                "input_cost_per_million": 4,
                "output_cost_per_million": 8,
                "cached_input_cost_per_million": 1,
            },
        ],
    }

    result = summarize(samples, model)

    assert result["cached_input_tokens"] == 80
    assert result["estimated_cost_usd"] == pytest.approx(0.000944)


def test_summary_records_retry_accounting_and_failure_categories():
    samples = [
        {
            "ok": True,
            "latency_seconds": 1,
            "ttft_seconds": 0.1,
            "output_tokens_per_second": 2,
            "input_tokens": 10,
            "output_tokens": 5,
            "retry_count": 1,
            "retry_reasons": ["rate_limit"],
        },
        {
            "ok": False,
            "latency_seconds": 1,
            "ttft_seconds": None,
            "output_tokens_per_second": None,
            "input_tokens": None,
            "output_tokens": None,
            "retry_count": 2,
            "retry_reasons": ["timeout", "timeout"],
            "failure_category": "timeout",
            "error": "timed out",
        },
    ]

    result = summarize(samples, {})

    assert result["retry_count"] == 3
    assert result["retry_reasons"] == {"rate_limit": 1, "timeout": 2}
    assert result["failure_categories"] == {"timeout": 1}


def test_summary_adds_failure_diagnosis_hints():
    samples = [
        {
            "ok": False,
            "latency_seconds": 1,
            "ttft_seconds": 0.1,
            "output_tokens_per_second": 2,
            "input_tokens": 10,
            "output_tokens": 5,
            "error": "response did not match regex",
            "response_preview": "No Markdown fences or commentary? Yes, I must output only JSON.",
        },
        {
            "ok": False,
            "latency_seconds": 1,
            "ttft_seconds": 0.1,
            "output_tokens_per_second": 2,
            "input_tokens": 10,
            "output_tokens": 0,
            "error": "unsupported parameter: response_format",
        },
    ]

    result = summarize(samples, {})

    assert result["failure_hints"] == [
        "reasoning or commentary appeared before the expected answer",
        "provider rejected an unsupported request parameter",
    ]


def test_stats():
    assert stats([1, 3])["mean"] == 2
