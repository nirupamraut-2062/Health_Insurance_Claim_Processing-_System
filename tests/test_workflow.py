"""Claim lifecycle, approval limits and the settlement transaction."""
from datetime import datetime, timedelta

import pytest

from claimsure import services
from claimsure.schema import ValidationError, insert_validated
from claimsure.services import WorkflowError


def _new_claim(db, user, icd="A90", total_scale=1):
    pol = db.policies.find_one({"status": "Active", "first_inception_date": {"$lt": datetime(2021, 1, 1)},
                                "available_cover": {"$gt": 500000}, "policy_type": "Family Floater"})
    hosp = db.hospitals.find_one({"network.insurer_ids": pol["insurer_id"]})
    adm = datetime.now() - timedelta(days=6)
    form = {"policy_number": pol["policy_number"], "member_id": pol["insured_members"][0]["member_id"],
            "hospital_id": hosp["hospital_id"], "primary_icd": icd, "claim_type": "Cashless",
            "admission_type": "Emergency", "admission_date": adm.strftime("%Y-%m-%d"),
            "discharge_date": (adm + timedelta(days=3)).strftime("%Y-%m-%d"), "room_category": "General Ward",
            "room_rent_per_day": "2000", "documents_complete": "1",
            "room_charges": 6000 * total_scale, "medicines": 20000 * total_scale, "investigations": 9000 * total_scale,
            "doctor_fees": 5000 * total_scale, "consumables": 2000 * total_scale}
    return services.intimate_claim(db, form, user)


def test_happy_path_with_escalation_and_settlement(db, executive, manager):
    number = _new_claim(db, executive, total_scale=4)  # ~1.7 lakh claim, above the executive's limit
    assert db.claims.find_one({"claim_number": number})["status"] == "Intimated"
    services.change_status(db, number, "Under Review", executive, "Documents verified")
    out = services.adjudicate_claim(db, number, executive)
    assert out["escalated"]
    claim = db.claims.find_one({"claim_number": number})
    assert claim["status"] == "Escalated"
    approver = services.get_user(db, claim["assigned_to"])
    out = services.adjudicate_claim(db, number, approver)
    claim = db.claims.find_one({"claim_number": number})
    assert claim["status"] in ("Approved", "Partially Approved") and claim["approved_amount"] > 0

    before = db.policies.find_one({"policy_number": claim["policy_number"]})["available_cover"]
    result = services.settle_claim(db, number, manager)
    assert result["committed"]
    after = db.policies.find_one({"policy_number": claim["policy_number"]})["available_cover"]
    assert before - after == claim["approved_amount"]
    assert db.payments.count_documents({"claim_number": number}) == 1
    assert db.claims.find_one({"claim_number": number})["status"] == "Settled"
    assert db.audit_logs.count_documents({"entity_id": number}) >= 4


def test_failed_settlement_rolls_back_everything(db, executive, manager):
    number = _new_claim(db, executive)
    services.change_status(db, number, "Under Review", manager)
    services.adjudicate_claim(db, number, manager)
    claim = db.claims.find_one({"claim_number": number})
    policy_before = db.policies.find_one({"policy_number": claim["policy_number"]})
    audit_before = db.audit_logs.count_documents({})

    result = services.settle_claim(db, number, manager, simulate_failure=True)
    assert not result["committed"] and result["consistent"]
    policy_after = db.policies.find_one({"policy_number": claim["policy_number"]})
    assert policy_after["available_cover"] == policy_before["available_cover"]
    assert policy_after["utilized_amount"] == policy_before["utilized_amount"]
    assert db.claims.find_one({"claim_number": number})["status"] == claim["status"]
    assert db.payments.count_documents({"claim_number": number}) == 0
    assert db.audit_logs.count_documents({}) == audit_before

    assert services.settle_claim(db, number, manager)["committed"]
    with pytest.raises(WorkflowError):
        services.settle_claim(db, number, manager)  # cannot pay twice


def test_invalid_transitions_are_blocked(db, executive):
    settled = db.claims.find_one({"status": "Settled"})
    with pytest.raises(WorkflowError):
        services.change_status(db, settled["claim_number"], "Under Review", executive)
    open_claim = db.claims.find_one({"status": "Under Review"})
    with pytest.raises(WorkflowError):
        services.change_status(db, open_claim["claim_number"], "Query Raised", executive, "")  # question required


def test_fraud_analyst_cannot_decide(db):
    analyst = services.get_user(db, "USR021")
    claim = db.claims.find_one({"status": "Under Review"})
    with pytest.raises(WorkflowError):
        services.adjudicate_claim(db, claim["claim_number"], analyst)


def test_intimation_rules(db, executive):
    pol = db.policies.find_one({"status": "Active"})
    with pytest.raises(WorkflowError):
        services.intimate_claim(db, {"policy_number": pol["policy_number"], "member_id": "MEM999999"}, executive)
    non_network = db.hospitals.find_one({"network.insurer_ids": {"$ne": pol["insurer_id"]}})
    form = {"policy_number": pol["policy_number"], "member_id": pol["insured_members"][0]["member_id"],
            "hospital_id": non_network["hospital_id"], "primary_icd": "A90", "claim_type": "Cashless",
            "admission_date": "2026-09-01", "discharge_date": "2026-09-03", "medicines": "1000"}
    with pytest.raises(WorkflowError, match="not in the insurer's network"):
        services.intimate_claim(db, form, executive)


def test_validator_rejects_bad_documents(db):
    with pytest.raises(ValidationError) as err:
        insert_validated(db, "claims", {"claim_number": "X", "status": "Paid", "claimed_amount": -1})
    assert any("status" in e for e in err.value.errors)
    with pytest.raises(ValidationError):
        insert_validated(db, "members", {"member_id": "MEM000001", "name": "A", "gender": "M"})


def test_unique_index_blocks_duplicate_payment(db):
    import pymongo.errors
    import mongomock
    pay = db.payments.find_one({}, {"_id": 0})
    with pytest.raises((pymongo.errors.DuplicateKeyError, mongomock.DuplicateKeyError)):
        db.payments.insert_one(dict(pay))


def test_sequence_generator_is_monotonic(db):
    a = services.next_id(db, "grievance")
    b = services.next_id(db, "grievance")
    assert b == a + 1
