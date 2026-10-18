"""Database features verified on a real MongoDB server."""
from datetime import datetime, timedelta

import pytest
from pymongo.errors import DuplicateKeyError, WriteError

from claimsure import indexes, services
from claimsure.db import server_info


def test_server_is_a_replica_set(db):
    info = server_info(db)
    assert info["mode"] == "mongodb" and info["replica_set"], info


def test_server_side_schema_validation(db):
    with pytest.raises(WriteError) as err:
        db.claims.insert_one({"claim_number": "CLAIM-42", "status": "Paid", "claimed_amount": -5000})
    assert err.value.code == 121  # DocumentValidationFailure


def test_unique_index_blocks_double_payment(db):
    pay = db.payments.find_one({}, {"_id": 0})
    with pytest.raises(DuplicateKeyError):
        db.payments.insert_one(pay)


def test_explain_uses_indexes(db):
    for row in indexes.benchmark(db):
        assert not row["simulated"]
        assert "IXSCAN" in row["after"]["stages"], row
        assert row["after"]["docs_examined"] <= row["before"]["docs_examined"]


def test_text_and_geo_queries(db):
    assert db.claims.count_documents({"$text": {"$search": "dengue"}}) > 0
    h = db.hospitals.find_one()
    near = list(db.hospitals.find({"location": {"$near": {"$geometry": h["location"], "$maxDistance": 50000}}}).limit(3))
    assert near and near[0]["hospital_id"] == h["hospital_id"]


def _approved_claim(db, manager):
    pol = db.policies.find_one({"status": "Active", "first_inception_date": {"$lt": datetime(2021, 1, 1)},
                                "available_cover": {"$gt": 500000}, "policy_type": "Family Floater"})
    hosp = db.hospitals.find_one({"network.insurer_ids": pol["insurer_id"]})
    adm = datetime.now() - timedelta(days=6)
    number = services.intimate_claim(db, {
        "policy_number": pol["policy_number"], "member_id": pol["insured_members"][0]["member_id"],
        "hospital_id": hosp["hospital_id"], "primary_icd": "A90", "claim_type": "Cashless",
        "admission_type": "Emergency", "admission_date": adm.strftime("%Y-%m-%d"),
        "discharge_date": (adm + timedelta(days=3)).strftime("%Y-%m-%d"), "room_category": "General Ward",
        "room_rent_per_day": "2000", "documents_complete": "1", "room_charges": 6000, "medicines": 20000,
        "investigations": 9000, "doctor_fees": 5000, "consumables": 2000}, manager)
    services.change_status(db, number, "Under Review", manager)
    services.adjudicate_claim(db, number, manager)
    return number


def test_native_transaction_commit_and_rollback(db, manager):
    number = _approved_claim(db, manager)
    claim = db.claims.find_one({"claim_number": number})
    before = db.policies.find_one({"policy_number": claim["policy_number"]})["available_cover"]
    failed = services.settle_claim(db, number, manager, simulate_failure=True)
    assert not failed["committed"] and failed["consistent"]
    assert "native" in failed["steps"][0][1]
    assert db.policies.find_one({"policy_number": claim["policy_number"]})["available_cover"] == before
    ok = services.settle_claim(db, number, manager)
    assert ok["committed"]
    after = db.policies.find_one({"policy_number": claim["policy_number"]})["available_cover"]
    assert before - after == claim["approved_amount"]
    with pytest.raises(services.WorkflowError):
        services.settle_claim(db, number, manager)


@pytest.mark.parametrize("url", ["/", "/claims", "/claims?q=dengue", "/claims/new", "/policies", "/members",
                                 "/hospitals", "/analytics", "/fraud", "/grievances", "/audit", "/database",
                                 "/database/benchmark", "/database/query?example=2", "/health"])
def test_pages_render_on_real_server(client, url):
    r = client.get(url)
    assert r.status_code == 200, r.text[:500]
    assert "Query failed" not in r.text


def test_detail_pages_and_reports(client, db):
    from claimsure import analytics
    claim = db.claims.find_one({"status": "Settled"})
    for url in (f"/claims/{claim['claim_number']}", f"/policies/{claim['policy_number']}",
                f"/members/{claim['member_id']}", f"/hospitals/{claim['hospital_id']}"):
        assert client.get(url).status_code == 200, url
    for key in analytics.REPORTS:
        assert client.get(f"/api/reports/{key}").status_code == 200, key
