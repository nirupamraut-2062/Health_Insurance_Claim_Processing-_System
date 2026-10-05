import pytest
from fastapi.testclient import TestClient

from claimsure import analytics, indexes


@pytest.mark.parametrize("key", list(analytics.REPORTS))
def test_every_report_runs(db, key):
    out = analytics.run_report(db, key)
    assert out["rows"], key
    assert out["pipelines"]


def test_kpis_are_sane(db):
    k = analytics.kpis(db)
    assert 70 <= k["settlement_ratio"] <= 95
    assert k["settled"] <= k["approved"] <= k["claimed"]
    assert k["lives_covered"] > k["active_policies"]


def test_benchmark_uses_indexes(db):
    for row in indexes.benchmark(db):
        assert row["after"]["docs_examined"] <= row["before"]["docs_examined"]
        assert row["after"]["index"]


@pytest.fixture(scope="module")
def client(db):
    from web.app import app
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("url", ["/", "/claims", "/claims?status=open", "/claims?q=dengue", "/claims/new",
                                 "/policies", "/members", "/hospitals", "/analytics", "/fraud", "/grievances",
                                 "/audit", "/database", "/database/benchmark", "/database/query?example=3",
                                 "/api/reports/insurer_performance", "/health"])
def test_pages_render(client, url):
    assert client.get(url).status_code == 200


def test_detail_pages_render(client, db):
    claim = db.claims.find_one({"status": "Settled"})
    assert client.get(f"/claims/{claim['claim_number']}").status_code == 200
    assert client.get(f"/policies/{claim['policy_number']}").status_code == 200
    assert client.get(f"/members/{claim['member_id']}").status_code == 200
    assert client.get(f"/hospitals/{claim['hospital_id']}").status_code == 200


def test_playground_is_read_only(client):
    r = client.post("/database/query", data={"collection": "claims", "operation": "aggregate",
                                              "query": '[{"$out": "hacked"}]'})
    assert "read-only" in r.text
