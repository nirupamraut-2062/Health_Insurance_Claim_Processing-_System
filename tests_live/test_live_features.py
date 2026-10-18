"""Concurrency, views, change streams, benchmark, security and maintenance on a real server."""
import threading
import time
from datetime import datetime, timedelta

import pytest

from claimsure import benchmark, concurrency, livefeed, maintenance, security, services
from claimsure.db import ANALYST_URI


def test_transactions_hit_a_real_write_conflict_and_retry(db):
    results = {r["key"]: r for r in concurrency.run_lab(db)}
    assert not results["A"]["correct"]
    assert results["B"]["correct"] and results["C"]["correct"] and results["D"]["correct"]
    assert any("WriteConflict" in row["msg"] for row in results["D"]["log"]), results["D"]["log"]


def _new_approved_claim(db, manager, policy):
    hosp = db.hospitals.find_one({"network.insurer_ids": policy["insurer_id"]})
    adm = datetime.now() - timedelta(days=6)
    number = services.intimate_claim(db, {
        "policy_number": policy["policy_number"], "member_id": policy["insured_members"][0]["member_id"],
        "hospital_id": hosp["hospital_id"], "primary_icd": "A90", "claim_type": "Cashless",
        "admission_type": "Emergency", "admission_date": adm.strftime("%Y-%m-%d"),
        "discharge_date": (adm + timedelta(days=3)).strftime("%Y-%m-%d"), "room_category": "General Ward",
        "room_rent_per_day": "2000", "documents_complete": "1", "room_charges": 6000, "medicines": 20000,
        "investigations": 9000, "doctor_fees": 5000, "consumables": 2000}, manager)
    services.change_status(db, number, "Under Review", manager)
    services.adjudicate_claim(db, number, manager)
    return number


def test_two_concurrent_settlements_on_one_policy(db, manager):
    pol = db.policies.find_one({"status": "Active", "policy_type": "Family Floater", "available_cover": {"$gt": 800000},
                                "first_inception_date": {"$lt": datetime(2021, 1, 1)}, "end_date": {"$gt": datetime.now()}})
    claims = [_new_approved_claim(db, manager, pol) for _ in range(2)]
    amounts = sum(db.claims.find_one({"claim_number": c})["approved_amount"] for c in claims)
    before = db.policies.find_one({"_id": pol["_id"]})["available_cover"]
    out = {}
    threads = [threading.Thread(target=lambda c=c: out.__setitem__(c, services.settle_claim(db, c, manager)))
               for c in claims]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r["committed"] for r in out.values()), out
    assert before - db.policies.find_one({"_id": pol["_id"]})["available_cover"] == amounts


def test_views_mask_personal_data(db):
    maintenance.create_views(db)
    row = db.members_masked.find_one()
    assert "kyc" not in row and "contact" not in row and row["phone"].startswith("XXXXXX")
    open_n = db.claims.count_documents({"status": {"$in": ["Intimated", "Documents Pending", "Under Review",
                                                           "Query Raised", "Escalated"]}})
    assert db.open_claims_queue.count_documents({}) == open_n


def test_materialised_view_and_migration(db):
    assert maintenance.refresh_materialized(db)["stage"] == "$merge"
    assert maintenance.compare_materialized(db)["same_total"]
    maintenance.migrate_schema(db)
    assert maintenance.migrate_schema(db)["migrated"] == 0
    assert set(maintenance.schema_versions(db)) == {2}


def test_change_stream_delivers_updates(db, manager):
    seen = []

    def listen():
        for ev in livefeed.events(db, stop_after=6):
            if ev:
                seen.append(ev)

    t = threading.Thread(target=listen)
    t.start()
    time.sleep(1.5)
    claim = db.claims.find_one({"status": "Under Review"})
    services.change_status(db, claim["claim_number"], "Query Raised", manager, "Please send case papers")
    t.join()
    assert any(e["key"] == claim["claim_number"] and "Query Raised" in e["text"] for e in seen), seen


def test_scale_benchmark_plans(db):
    r = benchmark.run(db, n=8000)
    cases = {c["key"]: c for c in r["cases"]}
    assert "IXSCAN" in cases["member"]["after"]["plan"]
    assert cases["covered"]["after"]["docs"] == 0 and "PROJECTION_COVERED" in cases["covered"]["after"]["plan"]
    assert "COLLSCAN" in cases["prefix"]["after"]["plan"]
    assert cases["unanchored"]["after"]["keys"] == r["documents"]


@pytest.mark.skipif(not ANALYST_URI, reason="CLAIMSURE_ANALYST_URI not set")
def test_analyst_cannot_write():
    res = security.analyst_demo(db_name="claimsure")
    assert res["available"] and res["safe"], res


# --------------------------------------------------------------------------- #
# Sign-in and re-verification on the real server
# --------------------------------------------------------------------------- #
def test_auth_indexes_on_server(db, client):
    info = db.sessions.index_information()
    assert info["ttl_session_expiry"]["expireAfterSeconds"] == 0
    assert db.credentials.index_information()["uq_credential_user"]["unique"]


def test_session_lookup_is_one_aggregation(db, client):
    from claimsure import auth
    token = client.cookies.get(auth.SESSION_COOKIE)
    user = auth.session_user(db, token)
    assert user["user_id"] == "USR001" and user["must_change_password"] is False


def test_server_validator_accepts_reverification(db):
    from claimsure import services
    from claimsure.constants import REVERIFICATION
    admin = services.get_user(db, "USR001")
    claim = db.claims.find_one({"status": "Withdrawn"})
    services.reopen_claim(db, claim["claim_number"], admin, "Live test: reopen a withdrawn claim", "USR003")
    assert db.claims.find_one({"claim_number": claim["claim_number"]})["status"] == REVERIFICATION
    services.complete_reverification(db, claim["claim_number"], services.get_user(db, "USR003"), "uphold",
                                     "Live test: withdrawal confirmed by the member")
    assert db.claims.find_one({"claim_number": claim["claim_number"]})["status"] == "Withdrawn"
    with pytest.raises(Exception):  # the $jsonSchema validator on the server still rejects unknown statuses
        db.claims.update_one({"_id": claim["_id"]}, {"$set": {"status": "Reopened"}})
