"""Rule-based fraud detection.

Each claim gets a 0-100 risk score from a set of red-flag rules that claims
investigation units commonly use. ``score_claim`` is pure; ``scan_claims``
builds the context it needs with a few aggregation pipelines (instead of one
query per claim) and writes the result back into ``claims.fraud``.
"""
from datetime import datetime, timedelta

from .constants import FRAUD_LEVELS

RULES = {
    "DUPLICATE_STAY":   "Overlapping hospitalisation already claimed by the same member",
    "INFLATED_BILL":    "Billed amount far above the benchmark cost for this diagnosis",
    "EARLY_CLAIM":      "Hospitalised soon after the policy was first bought",
    "FREQUENT_CLAIMANT": "Member has 4 or more claims in the preceding 12 months",
    "WATCHLIST_HOSPITAL": "Hospital is on the insurer's investigation watch-list",
    "LOS_ANOMALY":      "Length of stay inconsistent with the diagnosis",
    "OUT_OF_STATE":     "Planned treatment far from the member's home state",
    "NON_NETWORK_HIGH": "High-value reimbursement at a non-network hospital",
    "ROUND_AMOUNT":     "Bill total is a suspiciously round figure",
}


def risk_level(score):
    for threshold, level in FRAUD_LEVELS:
        if score >= threshold:
            return level
    return "Low"


def score_claim(claim, *, icd, member_state, inception, history, hospital_watchlisted):
    """Score one claim. ``history`` holds the member's *other* claims as dicts
    with ``admission_date`` and ``discharge_date``."""
    flags = []

    def flag(code, points, detail):
        flags.append({"code": code, "description": RULES[code], "detail": detail, "points": points})

    adm = claim["admission_date"]
    dis = claim.get("discharge_date") or adm
    claimed = claim["bill"]["total"]

    overlaps = [h for h in history
                if h["admission_date"] <= dis and (h.get("discharge_date") or h["admission_date"]) >= adm]
    if overlaps:
        flag("DUPLICATE_STAY", 40, f"Overlaps {', '.join(h['claim_number'] for h in overlaps[:3])}")

    ratio = claimed / icd["avg_cost"] if icd["avg_cost"] else 0
    if ratio >= 4:
        flag("INFLATED_BILL", 40, f"{ratio:.1f}x the benchmark cost")
    elif ratio >= 2.5:
        flag("INFLATED_BILL", 25, f"{ratio:.1f}x the benchmark cost")

    days = (adm - inception).days
    if 0 <= days < 30:
        flag("EARLY_CLAIM", 25, f"Admitted {days} days after inception")
    elif 0 <= days < 90:
        flag("EARLY_CLAIM", 15, f"Admitted {days} days after inception")

    recent = [h for h in history if adm - timedelta(days=365) <= h["admission_date"] < adm]
    if len(recent) >= 4:
        flag("FREQUENT_CLAIMANT", 15, f"{len(recent)} claims in previous 12 months")

    if hospital_watchlisted:
        flag("WATCHLIST_HOSPITAL", 20, claim["snapshot"]["hospital_name"])

    los = claim.get("length_of_stay") or 0
    if los > icd["avg_los"] * 2.5 + 1:
        flag("LOS_ANOMALY", 10, f"{los} days vs typical {icd['avg_los']}")
    elif los <= 1 and not icd.get("day_care") and ratio >= 2:
        flag("LOS_ANOMALY", 15, f"{los} day stay with {ratio:.1f}x benchmark bill")

    if (claim["admission_type"] == "Planned" and member_state
            and claim["snapshot"]["hospital_state"] != member_state):
        flag("OUT_OF_STATE", 10, f"Lives in {member_state}, treated in {claim['snapshot']['hospital_state']}")

    if not claim["snapshot"]["network_hospital"] and claimed > 200_000:
        flag("NON_NETWORK_HIGH", 10, "Reimbursement above ₹2,00,000 at non-network hospital")

    if claimed >= 50_000 and claimed % 10_000 == 0:
        flag("ROUND_AMOUNT", 5, f"Bill total {claimed}")

    score = min(sum(f["points"] for f in flags), 100)
    return {"score": score, "risk_level": risk_level(score), "flags": flags}


def build_context(db, member_ids=None):
    """Collect everything ``score_claim`` needs, using bulk queries."""
    match = {"member_id": {"$in": list(member_ids)}} if member_ids else {}
    history = {}
    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$member_id", "claims": {"$push": {
            "claim_number": "$claim_number",
            "admission_date": "$admission_date",
            "discharge_date": "$discharge_date",
            "status": "$status",
        }}}},
    ]
    for row in db.claims.aggregate(pipeline):
        history[row["_id"]] = [c for c in row["claims"] if c["status"] != "Withdrawn"]

    member_filter = {"member_id": {"$in": list(member_ids)}} if member_ids else {}
    states = {m["member_id"]: m["address"]["state"]
              for m in db.members.find(member_filter, {"member_id": 1, "address.state": 1})}
    icds = {i["code"]: i for i in db.icd_codes.find({}, {"_id": 0})}
    watch = {h["hospital_id"] for h in db.hospitals.find({"watchlisted": True}, {"hospital_id": 1})}
    return history, states, icds, watch


def score_with_context(claim, policy_inception, context):
    history, states, icds, watch = context
    others = [h for h in history.get(claim["member_id"], []) if h["claim_number"] != claim["claim_number"]]
    return score_claim(
        claim,
        icd=icds[claim["primary_icd"]],
        member_state=states.get(claim["member_id"]),
        inception=policy_inception,
        history=others,
        hospital_watchlisted=claim["hospital_id"] in watch,
    )


def scan_claims(db, query=None):
    """Re-score claims matching ``query`` and store the result. Returns counts by level."""
    query = query or {}
    claims = list(db.claims.find(query))
    if not claims:
        return {}
    context = build_context(db, {c["member_id"] for c in claims})
    inception = {p["policy_number"]: p["first_inception_date"]
                 for p in db.policies.find({"policy_number": {"$in": list({c["policy_number"] for c in claims})}},
                                           {"policy_number": 1, "first_inception_date": 1})}
    counts = {"High": 0, "Medium": 0, "Low": 0}
    now = datetime.now()
    for claim in claims:
        result = score_with_context(claim, inception[claim["policy_number"]], context)
        previous = claim.get("fraud") or {}
        result["scanned_at"] = now
        result["review_status"] = previous.get("review_status") or (
            "Not Required" if result["risk_level"] == "Low" else "Not Reviewed")
        for key in ("reviewed_by", "reviewed_at", "review_notes"):
            if key in previous:
                result[key] = previous[key]
        db.claims.update_one({"_id": claim["_id"]}, {"$set": {"fraud": result}})
        counts[result["risk_level"]] += 1
    return counts
