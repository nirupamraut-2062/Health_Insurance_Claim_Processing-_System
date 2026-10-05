"""The generated data set must be valid and internally consistent."""
import pytest

from claimsure.schema import validate
from claimsure.seed import COLLECTIONS, load


@pytest.mark.parametrize("name", COLLECTIONS)
def test_every_document_passes_its_validator(name):
    docs = load(name)
    assert docs, f"{name} is empty"
    bad = [(d.get("_id"), validate(name, d)[:2]) for d in docs if validate(name, d)]
    assert not bad, bad[:3]


def test_volume(db):
    assert db.claims.count_documents({}) >= 2000
    assert db.members.count_documents({}) >= 5000
    assert db.policies.count_documents({}) >= 2000
    assert sum(db[c].estimated_document_count() for c in COLLECTIONS) > 15000


def test_referential_integrity(db):
    policies = {p["policy_number"]: {m["member_id"] for m in p["insured_members"]}
                for p in db.policies.find({}, {"policy_number": 1, "insured_members.member_id": 1})}
    hospitals = set(db.hospitals.distinct("hospital_id"))
    for c in db.claims.find({}, {"policy_number": 1, "member_id": 1, "hospital_id": 1}):
        assert c["member_id"] in policies[c["policy_number"]]
        assert c["hospital_id"] in hospitals
    claim_numbers = set(db.claims.distinct("claim_number"))
    assert set(db.payments.distinct("claim_number")) <= claim_numbers
    assert set(db.grievances.distinct("claim_number")) <= claim_numbers
    assert set(db.doctors.distinct("hospital_id")) <= hospitals


def test_every_settled_claim_has_exactly_one_payment(db):
    settled = db.claims.count_documents({"status": "Settled"})
    assert settled == db.payments.count_documents({})
    for c in db.claims.find({"status": "Settled"}, {"claim_number": 1, "settled_amount": 1, "approved_amount": 1}).limit(300):
        assert c["settled_amount"] == c["approved_amount"] > 0
        assert db.payments.find_one({"claim_number": c["claim_number"]})["amount"] == c["settled_amount"]


def test_policy_cover_balances(db):
    for p in db.policies.find({}, {"total_cover": 1, "utilized_amount": 1, "available_cover": 1}):
        assert p["available_cover"] == max(p["total_cover"] - p["utilized_amount"], 0)


def test_status_history_is_chronological(db):
    for c in db.claims.find({}, {"status": 1, "status_history": 1}).limit(500):
        times = [h["at"] for h in c["status_history"]]
        assert times == sorted(times)
        assert c["status_history"][-1]["status"] == c["status"]
