"""Unit tests for the adjudication engine (no database needed)."""
from datetime import datetime

from claimsure.adjudication import adjudicate

PLAN = {
    "features": {"room_rent_limit": {"type": "percent_si", "value": 1}, "copay_percent": 0, "ambulance_limit": 2000,
                 "maternity_cover": False, "maternity_limit": 0},
    "waiting_periods": {"initial_days": 30, "pre_existing_months": 48, "specific_illness_months": 24, "maternity_months": 0},
    "sub_limits": [{"label": "Cataract", "icd_codes": ["H25.9"], "limit": 40000}],
    "excluded_icd_codes": ["Z41.1"],
}
DENGUE = {"code": "A90", "description": "Dengue fever", "avg_cost": 45000, "avg_los": 4}


def policy(inception=datetime(2020, 1, 1), cover=500000, ped=()):
    return {"first_inception_date": inception, "end_date": datetime(2026, 12, 31), "status": "Active",
            "sum_insured": 500000, "available_cover": cover,
            "insured_members": [{"member_id": "M1", "name": "Test", "relation": "Self", "pre_existing_conditions": list(ped)}]}


def claim(admitted=datetime(2026, 5, 1), rent=5000, los=4, **bill):
    base = {"room_charges": rent * los, "icu_charges": 0, "nursing_charges": 4000, "doctor_fees": 6000,
            "surgeon_ot_charges": 0, "investigations": 10000, "medicines": 15000, "consumables": 5000,
            "implants": 0, "ambulance": 0, "admin_charges": 1000}
    base.update(bill)
    base["total"] = sum(v for k, v in base.items() if k != "total")
    return {"member_id": "M1", "admission_date": admitted, "length_of_stay": los, "icu_days": 0,
            "room_category": "Twin Sharing", "room_rent_per_day": rent, "bill": base,
            "snapshot": {"hospital_tier": 1}}


def test_clean_claim_only_loses_non_payables():
    r = adjudicate(claim(), policy(), PLAN, DENGUE)
    assert r["decision"] == "Approved"
    assert r["payable_amount"] == r["claimed_amount"] - 3000 - 1000  # 60% of consumables + admin charges


def test_initial_waiting_period_rejects():
    r = adjudicate(claim(admitted=datetime(2020, 1, 15)), policy(), PLAN, DENGUE)
    assert r["decision"] == "Rejected" and r["rejection_code"] == "R03"


def test_accident_is_exempt_from_initial_waiting():
    fracture = {**DENGUE, "code": "S82.20", "is_accident": True}
    r = adjudicate(claim(admitted=datetime(2020, 1, 15)), policy(), PLAN, fracture)
    assert r["eligible"]


def test_pre_existing_disease_waiting_period():
    diabetes = {**DENGUE, "code": "E11.9", "ped_group": "Diabetes"}
    r = adjudicate(claim(admitted=datetime(2022, 6, 1)), policy(ped=["Diabetes"]), PLAN, diabetes)
    assert r["rejection_code"] == "R06"
    r = adjudicate(claim(admitted=datetime(2024, 6, 1)), policy(ped=["Diabetes"]), PLAN, diabetes)
    assert r["eligible"]


def test_room_rent_excess_and_proportionate_deduction():
    # Eligible rent = 1% of 5,00,000 = 5,000/day; member stayed at 10,000/day
    r = adjudicate(claim(rent=10000), policy(), PLAN, DENGUE)
    ded = {d["category"]: d["amount"] for d in r["deductions"]}
    assert ded["Room Rent Excess"] == (10000 - 5000) * 4
    # Associated charges (nursing + doctor + investigations = 20,000) are paid at 50%
    assert ded["Proportionate Deduction"] == 10000
    assert r["decision"] == "Partially Approved"


def test_copay_and_sum_insured_cap():
    plan = {**PLAN, "features": {**PLAN["features"], "copay_percent": 20}}
    r = adjudicate(claim(), policy(cover=10000), plan, DENGUE)
    cats = [d["category"] for d in r["deductions"]]
    assert "Co-Payment" in cats and "Sum Insured Limit" in cats
    assert r["payable_amount"] == 10000


def test_sub_limit_caps_cataract():
    cataract = {"code": "H25.9", "description": "Cataract", "avg_cost": 48000, "avg_los": 1}
    r = adjudicate(claim(los=1, surgeon_ot_charges=60000), policy(), PLAN, cataract)
    assert r["payable_amount"] == 40000


def test_exclusion_and_manual_rejection():
    assert adjudicate(claim(), policy(), PLAN, {**DENGUE, "code": "Z41.1"})["rejection_code"] == "R07"
    assert adjudicate(claim(), policy(), PLAN, DENGUE, manual_rejection="R09")["rejection_code"] == "R09"
