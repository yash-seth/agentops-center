import pytest

from scripts import benchmark_resilience as bench


def test_retries_raise_success_close_to_theory():
    rows = bench.retry_benchmark(calls=600)
    for row in rows:
        assert row["success_two_retries"] >= row["success_no_retries"]
        assert abs(row["success_two_retries"] - row["theoretical_two_retries"]) < 0.05
        assert abs(row["success_no_retries"] - (1 - row["injected_failure_rate"])) < 0.06
    assert rows[0]["success_no_retries"] == 1.0  # no faults, no failures
    half = next(r for r in rows if r["injected_failure_rate"] == 0.5)
    assert half["success_two_retries"] > 0.8


def test_circuit_breaker_shields_a_failing_dependency():
    result = bench.breaker_benchmark(calls=100)
    assert result["dependency_calls_without_breaker"] == 100 * 3  # 1 try + 2 retries each
    # threshold 5: call 1 makes 3 real attempts; call 2 makes 2 more before the breaker opens
    assert result["dependency_calls_with_breaker"] == 5
    assert result["dependency_calls_avoided"] == pytest.approx(1 - 5 / 300)


def test_every_forced_loop_is_stopped_after_two_executions():
    rows = bench.loop_benchmark()
    assert len(rows) == 3
    assert all(r["stopped"] and r["reason"] == "repeat" for r in rows)
    assert all(r["tool_executions"] == 2 for r in rows)  # the third identical call is blocked


def test_benchmarks_are_reproducible():
    assert bench.retry_benchmark(calls=200) == bench.retry_benchmark(calls=200)


def test_markdown_report_lists_all_three_sections():
    md = bench.to_markdown(
        {"retries": bench.retry_benchmark(calls=50), "breaker": bench.breaker_benchmark(calls=20),
         "loops": bench.loop_benchmark()}
    )
    assert md.count("###") == 3 and "avoided" in md and "| yes |" in md


@pytest.fixture(autouse=True)
def _reset():
    yield
    from aoc_runtime import faults, resilience

    faults.clear_faults()
    resilience.reset_breakers()
