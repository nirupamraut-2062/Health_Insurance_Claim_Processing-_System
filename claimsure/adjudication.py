"""Claim adjudication engine.

A pure function (no database access) that applies the policy wording to a
claim, the way a claims adjuster does:

1. Eligibility - policy in force, patient covered, waiting periods, exclusions.
2. Financials  - non-payable items, room-rent capping with proportionate
                 deduction, ambulance limit, sub-limits, co-payment and the
                 available sum insured.

The same engine is used by the data generator (so seeded amounts are
consistent) and by the live claim workflow.
"""
from .constants import (
    REJECTION_REASONS, ROOM_TARIFF, PROPORTIONATE_HEADS,
    APPROVED, PARTIALLY_APPROVED, REJECTED,
)
from .utils import months_between, inr

# Deductions that do not, on their own, make a claim "partially" approved.
_ROUTINE = {"Non-Payable Items"}


def eligible_room_rent(plan, sum_insured, tier):
    """Per-day room rent the member is eligible for (None = no limit)."""
    rule = plan["features"]["room_rent_limit"]
    if rule["type"] == "percent_si":
        return round(sum_insured * rule["value"] / 100)
    if rule["type"] == "category":
        return ROOM_TARIFF[rule["value"]][tier]
    return None


def adjudicate(claim, policy, plan, icd, available_cover=None, manual_rejection=None):
    """Return the adjudication result for ``claim`` as a dict.

    ``available_cover`` defaults to the policy's current available cover.
    ``manual_rejection`` is a rejection code chosen by the reviewer
    (documents missing, fraud, not medically necessary).
    """
    feat = plan["features"]
    wait = plan["waiting_periods"]
    adm = claim["admission_date"]
    inception = policy["first_inception_date"]
    cover = policy["available_cover"] if available_cover is None else available_cover
    member = next((m for m in policy["insured_members"] if m["member_id"] == claim["member_id"]), None)
    months = months_between(inception, adm)
    days = (adm - inception).days

    checks, reject = [], None

    def check(code, rule, passed, detail):
        nonlocal reject
        checks.append({"rule": rule, "passed": passed, "detail": detail})
        if not passed and reject is None:
            reject = code

    policy_end = policy.get("cancelled_on") or policy["end_date"]
    check("R01", "Policy in force", inception <= adm <= policy_end,
          f"Cover {inception:%d %b %Y} to {policy_end:%d %b %Y}, admitted {adm:%d %b %Y}")
    check("R02", "Patient covered", member is not None,
          f"{member['name']} ({member['relation']})" if member else "Not an insured member")

    if icd.get("is_accident"):
        check("R03", "Initial waiting period", True, "Accident - waiting period not applicable")
    else:
        check("R03", "Initial waiting period", days >= wait["initial_days"],
              f"{days} days since inception (required {wait['initial_days']})")

    if icd.get("is_maternity"):
        if not feat.get("maternity_cover"):
            check("R04", "Maternity cover", False, "Plan does not cover maternity")
        else:
            check("R04", "Maternity waiting period", months >= wait["maternity_months"],
                  f"{months} months since inception (required {wait['maternity_months']})")

    if icd.get("specific_illness"):
        check("R05", "Specific illness waiting period", months >= wait["specific_illness_months"],
              f"{months} months since inception (required {wait['specific_illness_months']})")

    ped = icd.get("ped_group")
    if ped and member and ped in member.get("pre_existing_conditions", []):
        check("R06", "Pre-existing disease waiting period", months >= wait["pre_existing_months"],
              f"Declared PED '{ped}': {months} months (required {wait['pre_existing_months']})")

    check("R07", "Treatment not excluded", icd["code"] not in plan.get("excluded_icd_codes", []),
          icd["description"])

    if manual_rejection:
        check(manual_rejection, "Reviewer assessment", False, REJECTION_REASONS[manual_rejection])

    if reject is None:
        check("R11", "Sum insured available", cover > 0, f"Available cover {inr(cover)}")

    bill = claim["bill"]
    claimed = bill["total"]
    result = {
        "claimed_amount": claimed,
        "checks": checks,
        "deductions": [],
        "eligible_room_rent": None,
        "copay_amount": 0,
    }
    if reject:
        result.update(eligible=False, decision=REJECTED, rejection_code=reject,
                      rejection_reason=REJECTION_REASONS[reject], payable_amount=0,
                      total_deductions=claimed, member_payable=claimed)
        return result

    deductions = []

    def deduct(category, description, amount):
        amount = int(round(amount))
        if amount > 0:
            deductions.append({"category": category, "description": description, "amount": amount})

    # 1. Non-payable items (IRDAI list of non-medical expenses)
    deduct("Non-Payable Items", "Non-medical consumables (gloves, masks, kits)", bill.get("consumables", 0) * 0.6)
    deduct("Non-Payable Items", "Registration & admin charges", bill.get("admin_charges", 0))

    # 2. Room rent capping + proportionate deduction
    tier = claim["snapshot"]["hospital_tier"]
    eligible = eligible_room_rent(plan, policy["sum_insured"], tier)
    result["eligible_room_rent"] = eligible
    if eligible is not None:
        actual = claim.get("room_rent_per_day", 0)
        room_days = max(claim["length_of_stay"] - claim.get("icu_days", 0), 0)
        if actual > eligible and room_days:
            deduct("Room Rent Excess",
                   f"{claim['room_category']} at {inr(actual)}/day vs eligible {inr(eligible)}/day x {room_days} days",
                   (actual - eligible) * room_days)
            ratio = eligible / actual
            associated = sum(bill.get(h, 0) for h in PROPORTIONATE_HEADS)
            deduct("Proportionate Deduction",
                   f"Associated charges scaled to {ratio:.0%} of billed (room rent eligibility)",
                   associated * (1 - ratio))
        icu_rate = claim.get("icu_rent_per_day", 0)
        if claim.get("icu_days") and plan["features"]["room_rent_limit"]["type"] == "percent_si":
            icu_eligible = eligible * 2
            if icu_rate > icu_eligible:
                deduct("Room Rent Excess",
                       f"ICU at {inr(icu_rate)}/day vs eligible {inr(icu_eligible)}/day x {claim['icu_days']} days",
                       (icu_rate - icu_eligible) * claim["icu_days"])

    # 3. Ambulance limit
    if bill.get("ambulance", 0) > feat["ambulance_limit"]:
        deduct("Ambulance Limit", f"Road ambulance capped at {inr(feat['ambulance_limit'])}",
               bill["ambulance"] - feat["ambulance_limit"])

    payable = claimed - sum(d["amount"] for d in deductions)

    # 4. Disease-wise sub-limits and maternity limit
    for sub in plan.get("sub_limits", []):
        if icd["code"] in sub["icd_codes"] and payable > sub["limit"]:
            deduct("Sub-Limit", f"{sub['label']} capped at {inr(sub['limit'])}", payable - sub["limit"])
            payable = sub["limit"]
    if icd.get("is_maternity") and payable > feat.get("maternity_limit", 0):
        deduct("Sub-Limit", f"Maternity benefit capped at {inr(feat['maternity_limit'])}",
               payable - feat["maternity_limit"])
        payable = feat["maternity_limit"]

    # 5. Co-payment
    copay = int(round(payable * feat.get("copay_percent", 0) / 100))
    if copay:
        deduct("Co-Payment", f"{feat['copay_percent']}% co-payment borne by insured", copay)
        payable -= copay
    result["copay_amount"] = copay

    # 6. Available sum insured
    if payable > cover:
        deduct("Sum Insured Limit", f"Available sum insured {inr(cover)}", payable - cover)
        payable = cover

    payable = max(int(payable), 0)
    partial = any(d["category"] not in _ROUTINE for d in deductions)
    result.update(
        eligible=True,
        decision=PARTIALLY_APPROVED if partial else APPROVED,
        rejection_code=None,
        rejection_reason=None,
        deductions=deductions,
        payable_amount=payable,
        total_deductions=claimed - payable,
        member_payable=claimed - payable,
    )
    return result
