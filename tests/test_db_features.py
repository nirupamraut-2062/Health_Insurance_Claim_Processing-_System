"""Concurrency control, optimistic locking, maintenance jobs and the new pages (mock mode)."""
from datetime import datetime

import mongomock
import pytest
from fastapi.testclient import TestClient

from conftest import sign_in

from claimsure import concurrency, livefeed, maintenance, services
from claimsure.indexes import ensure_indexes
from claimsure.seed import seed


def test_concurrency_lab_shows_lost_update_and_three_fixes(db):
    a, b, c, d = concurrency.run_lab(db)
    assert not a["correct"] and a["final"] == concurrency.START - concurrency.AMOUNT  # one payment lost
    assert b["correct"] and c["correct"] and d["correct"]
    assert any("CONFLICT" in row["msg"] for row in c["log"])  # optimistic lock detected the clash


def test_optimistic_lock_rejects_a_stale_version(db, manager):
    claim = db.claims.find_one({"status": "Under Review"})
    seen = claim.get("version") or 0
    services.change_status(db, claim["claim_number"], "Query Raised", manager, "Send the ECG report", expected_version=seen)
    after = db.claims.find_one({"_id": claim["_id"]})
    assert after["version"] == seen + 1
    with pytest.raises(services.ConcurrencyError):  # a second user still holding the old version
        services.change_status(db, claim["claim_number"], "Under Review", manager, "reply", expected_version=seen)


@pytest.fixture(scope="module")
def fresh_db():
    database = mongomock.MongoClient(tz_aware=False)["claimsure_scratch"]
    seed(database, verbose=False)
    return database


def test_renewal_batch_is_idempotent(fresh_db):
    future = datetime(2027, 3, 1)
    due = fresh_db.policies.count_documents({"status": "Active", "end_date": {"$lt": future}})
    first = maintenance.renew_due_policies(fresh_db, today=future)
    assert first["renewed"] == due > 0
    assert maintenance.renew_due_policies(fresh_db, today=future)["renewed"] == 0
    assert fresh_db.policies.count_documents({"status": "Active", "end_date": {"$lt": future}}) == 0


def test_materialised_view_matches_live_totals(fresh_db):
    r = maintenance.refresh_materialized(fresh_db)
    assert r["rows"] > 0
    assert maintenance.compare_materialized(fresh_db)["same_total"]


def test_backup_and_restore_round_trip(fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "BACKUP_DIR", str(tmp_path))
    before = {c: fresh_db[c].count_documents({}) for c in ("claims", "policies", "payments")}
    b = maintenance.backup(fresh_db)
    fresh_db.claims.delete_many({})
    fresh_db.payments.drop()
    name = b["folder"].replace("\\", "/").rstrip("/").split("/")[-1]
    maintenance.restore(fresh_db, name)
    assert {c: fresh_db[c].count_documents({}) for c in before} == before
    ensure_indexes(fresh_db)


def test_change_event_description():
    ev = livefeed.describe({"operationType": "update", "ns": {"coll": "claims"},
                            "fullDocument": {"claim_number": "CLM-2026-000001", "status": "Settled"},
                            "updateDescription": {"updatedFields": {"status": "Settled", "version": 3}}})
    assert ev["text"] == "CLM-2026-000001 is now Settled" and ev["fields"] == ["status"]


@pytest.fixture(scope="module")
def client(db):
    from web.app import app
    with TestClient(app) as c:
        sign_in(c, "USR023")  # Head Admin: every page and action is allowed
        yield c


@pytest.mark.parametrize("url", ["/database/concurrency", "/database/scale", "/database/security",
                                 "/database/live", "/database/maintenance"])
def test_new_pages_render(client, url):
    assert client.get(url).status_code == 200


def test_lab_and_maintenance_actions(client):
    r = client.post("/database/concurrency")
    assert r.status_code == 200 and "lost update" in r.text
    assert client.post("/database/maintenance/renew").status_code == 200
    assert client.post("/database/maintenance/mv").status_code == 200
