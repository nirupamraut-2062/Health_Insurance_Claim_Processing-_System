from datetime import datetime, timedelta

from claimsure.fraud import score_claim

ICD = {"avg_cost": 45000, "avg_los": 4, "day_care": False}


def make(total=45000, admitted=datetime(2026, 5, 1), los=4, planned=False, network=True, state="Karnataka"):
    return {"claim_number": "C1", "admission_date": admitted, "discharge_date": admitted + timedelta(days=los),
            "length_of_stay": los, "admission_type": "Planned" if planned else "Emergency", "bill": {"total": total},
            "snapshot": {"hospital_state": state, "network_hospital": network, "hospital_name": "H"}}


def score(claim, history=(), inception=datetime(2020, 1, 1), watch=False):
    return score_claim(claim, icd=ICD, member_state="Karnataka", inception=inception, history=list(history),
                       hospital_watchlisted=watch)


def test_clean_claim_is_low_risk():
    r = score(make())
    assert r["score"] == 0 and r["risk_level"] == "Low"


def test_duplicate_overlapping_stay():
    other = {"claim_number": "C0", "admission_date": datetime(2026, 5, 2), "discharge_date": datetime(2026, 5, 4)}
    r = score(make(), history=[other])
    assert "DUPLICATE_STAY" in [f["code"] for f in r["flags"]]


def test_inflated_bill_early_claim_watchlist_is_high_risk():
    r = score(make(total=200000), inception=datetime(2026, 4, 20), watch=True)
    codes = {f["code"] for f in r["flags"]}
    assert {"INFLATED_BILL", "EARLY_CLAIM", "WATCHLIST_HOSPITAL"} <= codes
    assert r["risk_level"] == "High"


def test_out_of_state_planned_treatment():
    r = score(make(planned=True, state="Maharashtra"))
    assert [f["code"] for f in r["flags"]] == ["OUT_OF_STATE"]
