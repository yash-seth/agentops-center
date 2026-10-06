import pytest

from scripts.e2e_observability import EXPECTED_ALERTS, prom_query, wait_for


def test_wait_for_returns_the_first_truthy_result():
    calls = iter([None, 0, "ready"])
    assert wait_for(lambda: next(calls), timeout_s=5, interval_s=0.01) == "ready"


def test_wait_for_tolerates_errors_while_services_start_then_reports_them():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("not up yet")
        return True

    assert wait_for(flaky, timeout_s=5, interval_s=0.01) is True
    with pytest.raises(TimeoutError, match="ConnectionError"):
        wait_for(lambda: (_ for _ in ()).throw(ConnectionError("down")), 0.05, 0.01)


def test_prom_query_unwraps_the_result_vector():
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"result": [{"value": [0, "7"]}]}}

    class Prom:
        def get(self, path, params=None):
            assert path == "/api/v1/query" and params == {"query": "up"}
            return Resp()

    assert prom_query(Prom(), "up")[0]["value"][1] == "7"


def test_expected_alerts_match_the_rules_in_the_repo():
    import yaml

    from aoc_runtime.config import REPO_ROOT

    rules = yaml.safe_load((REPO_ROOT / "deploy/prometheus/alerts.yml").read_text())
    names = {r["alert"] for g in rules["groups"] for r in g["rules"]}
    assert names == EXPECTED_ALERTS
