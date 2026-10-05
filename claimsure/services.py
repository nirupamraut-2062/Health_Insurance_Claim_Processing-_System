"""Claim lifecycle services.

Every write the application performs goes through this module so that the
business rules (state machine, approval limits, validation, audit trail) are
applied consistently by both the web app and the CLI.
"""
import random
from datetime import datetime

from pymongo import ReturnDocument

from . import audit, fraud
from .adjudication import adjudicate
from .constants import (
    TRANSITIONS, DECISION_STATUSES, AWAITING_PAYMENT, ROLES, STATE_REGION, DOCUMENT_TYPES,
    ROOM_TARIFF, BILL_KEYS, REJECTION_REASONS, GRIEVANCE_CATEGORIES,
    INTIMATED, DOCS_PENDING, UNDER_REVIEW, QUERY_RAISED, ESCALATED, REJECTED, SETTLED, WITHDRAWN,
)
from .db import write_lock
from .schema import insert_validated
from .transactions import transaction
from .utils import age_on, inr, parse_date


class WorkflowError(Exception):
    """A business rule prevented the requested action."""


# A counter bumped on every write; analytics caches use it to know when to refresh.
_version = [0]


def data_version():
    return _version[0]


def mark_changed():
    _version[0] += 1


def next_id(db, counter):
    """Atomic sequence generator (counters collection + $inc)."""
    doc = db.counters.find_one_and_update({"_id": counter}, {"$inc": {"seq": 1}},
                                          upsert=True, return_document=ReturnDocument.AFTER)
    return doc["seq"]


def get_user(db, user_id):
    return db.users.find_one({"user_id": user_id}, {"_id": 0})


def get_claim(db, claim_number):
    claim = db.claims.find_one({"claim_number": claim_number})
    if not claim:
        raise WorkflowError(f"Claim {claim_number} not found")
    return claim


def claim_context(db, claim):
    policy = db.policies.find_one({"policy_number": claim["policy_number"]})
    plan = db.plans.find_one({"plan_id": claim["plan_id"]})
    icd = db.icd_codes.find_one({"code": claim["primary_icd"]})
    return policy, plan, icd


def preview_adjudication(db, claim, manual_rejection=None):
    policy, plan, icd = claim_context(db, claim)
    return adjudicate(claim, policy, plan, icd, manual_rejection=manual_rejection)


def available_actions(claim, user):
    status, role = claim["status"], user["role"]
    manager = role in ("Admin", "Claims Manager")
    transitions = []
    for nxt in TRANSITIONS[status]:
        if nxt in DECISION_STATUSES or nxt in (SETTLED, ESCALATED):
            continue
        if status == REJECTED and not manager:
            continue
        transitions.append(nxt)
    return {
        "transitions": transitions,
        "can_adjudicate": status in (UNDER_REVIEW, ESCALATED) and ROLES[role]["can_decide"],
        "can_settle": status in AWAITING_PAYMENT and role in ("Admin", "Claims Manager", "Senior Claims Adjuster"),
        "can_review_fraud": role in ("Fraud Analyst", "Admin", "Claims Manager")
                            and (claim.get("fraud") or {}).get("risk_level") in ("Medium", "High"),
        "approval_limit": ROLES[role]["approval_limit"],
    }


def _history(status, user, remarks, at=None):
    return {"status": status, "at": at or datetime.now(), "by": user["user_id"], "by_name": user["name"],
            "remarks": remarks}


# --------------------------------------------------------------------------- #
# Status changes
# --------------------------------------------------------------------------- #
def change_status(db, claim_number, new_status, user, remarks=""):
    with write_lock:
        claim = get_claim(db, claim_number)
        old = claim["status"]
        if new_status not in TRANSITIONS[old] or new_status in DECISION_STATUSES + [SETTLED, ESCALATED]:
            raise WorkflowError(f"Cannot move a claim from '{old}' to '{new_status}' manually")
        if old == REJECTED and user["role"] not in ("Admin", "Claims Manager"):
            raise WorkflowError("Only a Claims Manager can re-open a rejected claim")
        now = datetime.now()
        sets = {"status": new_status, "updated_at": now}
        push = {"status_history": _history(new_status, user, remarks or f"Moved to {new_status}", now)}
        unset = {}

        if new_status == QUERY_RAISED:
            if not remarks.strip():
                raise WorkflowError("Enter the question to be sent to the insured / hospital")
            push["queries"] = {"question": remarks, "raised_by": user["user_id"], "raised_at": now,
                               "response": None, "responded_at": None}
        if new_status == UNDER_REVIEW:
            if old == QUERY_RAISED and claim.get("queries"):
                idx = len(claim["queries"]) - 1
                sets[f"queries.{idx}.response"] = remarks or "Response received"
                sets[f"queries.{idx}.responded_at"] = now
            if old in (INTIMATED, DOCS_PENDING):
                sets["submitted_at"] = claim.get("submitted_at") or now
                for i, doc in enumerate(claim.get("documents", [])):
                    sets[f"documents.{i}.status"] = "Verified"
                    if not doc.get("uploaded_at"):
                        sets[f"documents.{i}.uploaded_at"] = now
            if old == REJECTED:
                sets["approved_amount"] = 0
                push["previous_adjudications"] = claim.get("adjudication")
                unset = {"adjudication": "", "decided_at": ""}
            if not claim.get("assigned_to"):
                sets["assigned_to"] = user["user_id"]
        if new_status == WITHDRAWN:
            sets["assigned_to"] = None

        update = {"$set": sets, "$push": push}
        if unset:
            update["$unset"] = unset
        db.claims.update_one({"claim_number": claim_number, "status": old}, update)
        audit.log(db, user, "CLAIM_STATUS_CHANGED", "claim", claim_number,
                  f"{claim_number}: {old} → {new_status}", {"from": old, "to": new_status, "remarks": remarks})
        mark_changed()
    return new_status


# --------------------------------------------------------------------------- #
# Adjudication (decision)
# --------------------------------------------------------------------------- #
def _approver_for(db, amount, region):
    for role in ("Senior Claims Adjuster", "Claims Manager", "Admin"):
        if ROLES[role]["approval_limit"] >= amount:
            users = list(db.users.find({"role": role, "active": True}, {"_id": 0}))
            local = [u for u in users if u["region"] == region]
            if local or users:
                return (local or users)[0]
    return db.users.find_one({"role": "Admin"}, {"_id": 0})


def adjudicate_claim(db, claim_number, user, manual_rejection=None, remarks=""):
    """Run the adjudication engine and record the decision, or escalate it."""
    if manual_rejection and manual_rejection not in REJECTION_REASONS:
        raise WorkflowError("Unknown rejection reason")
    with write_lock:
        claim = get_claim(db, claim_number)
        if claim["status"] not in (UNDER_REVIEW, ESCALATED):
            raise WorkflowError(f"Only claims Under Review or Escalated can be adjudicated (now '{claim['status']}')")
        if not ROLES[user["role"]]["can_decide"]:
            raise WorkflowError(f"A {user['role']} cannot take claim decisions")
        result = preview_adjudication(db, claim, manual_rejection)
        now = datetime.now()
        limit = user["approval_limit"]

        if result["eligible"] and result["payable_amount"] > limit:
            approver = _approver_for(db, result["payable_amount"], STATE_REGION.get(claim["snapshot"]["hospital_state"]))
            note = (f"Payable {inr(result['payable_amount'])} exceeds {user['name']}'s approval limit "
                    f"of {inr(limit)}; escalated to {approver['name']} ({approver['role']})")
            if claim["status"] == ESCALATED:
                raise WorkflowError(note.replace("escalated to", "needs approval from"))
            db.claims.update_one({"claim_number": claim_number}, {
                "$set": {"status": ESCALATED, "assigned_to": approver["user_id"], "updated_at": now},
                "$push": {"status_history": _history(ESCALATED, user, note, now)}})
            audit.log(db, user, "CLAIM_ESCALATED", "claim", claim_number, f"{claim_number}: escalated",
                      {"payable": result["payable_amount"], "limit": limit, "to": approver["user_id"]})
            mark_changed()
            return {"escalated": True, "message": note, "result": result}

        decision = result["decision"]
        stored = {k: result[k] for k in (
            "decision", "eligible", "rejection_code", "rejection_reason", "claimed_amount", "deductions",
            "total_deductions", "copay_amount", "payable_amount", "member_payable", "eligible_room_rent")}
        stored.update(adjudicated_by=user["user_id"], adjudicated_at=now, remarks=remarks)
        sets = {
            "status": decision, "adjudication": stored, "approved_amount": result["payable_amount"],
            "decided_at": now, "updated_at": now, "assigned_to": None,
        }
        if claim.get("submitted_at"):
            sets["tat_days"] = round((now - claim["submitted_at"]).total_seconds() / 86400, 1)
        if claim.get("pre_auth"):
            sets["pre_auth.status"] = "Approved" if result["eligible"] else "Denied"
        note = (f"{decision} for {inr(result['payable_amount'])}" if result["eligible"]
                else f"Rejected: {result['rejection_reason']}") + (f" - {remarks}" if remarks else "")
        db.claims.update_one({"claim_number": claim_number},
                             {"$set": sets, "$push": {"status_history": _history(decision, user, note, now)}})
        audit.log(db, user, "CLAIM_DECISION", "claim", claim_number, f"{claim_number}: {decision}",
                  {"payable": result["payable_amount"], "rejection_code": result["rejection_code"]})
        mark_changed()
        return {"escalated": False, "message": note, "result": result}


# --------------------------------------------------------------------------- #
# Settlement - multi-document ACID transaction
# --------------------------------------------------------------------------- #
def settle_claim(db, claim_number, user, simulate_failure=False):
    """Settle an approved claim atomically:

    1. claims    - status -> Settled, payment details, status history
    2. policies  - utilised amount up, available cover down (guarded by $gte)
    3. payments  - new payment record (unique index on claim_number = no double pay)
    4. audit_logs- audit entry

    Either all four writes happen or none do.
    """
    steps, now = [], datetime.now()
    claim = get_claim(db, claim_number)
    policy_before = db.policies.find_one({"policy_number": claim["policy_number"]},
                                         {"available_cover": 1, "utilized_amount": 1})
    if claim["status"] not in AWAITING_PAYMENT:
        raise WorkflowError(f"Only Approved / Partially Approved claims can be settled (now '{claim['status']}')")
    if user["role"] not in ("Admin", "Claims Manager", "Senior Claims Adjuster"):
        raise WorkflowError(f"A {user['role']} cannot release payments")
    payment_id = f"PAY-{now.year}-{next_id(db, 'payment'):06d}"
    try:
        with transaction(db) as tx:
            steps.append(("Start transaction", "native MongoDB session (snapshot / majority)" if tx.native
                          else "emulated: lock + before-images (mongomock)"))
            claim = tx.find_one("claims", {"claim_number": claim_number})
            policy = tx.find_one("policies", {"policy_number": claim["policy_number"]})
            amount = claim["approved_amount"]
            current_term = claim["admission_date"] >= policy["start_date"]
            steps.append(("Read claim and policy",
                          f"Approved {inr(amount)}; policy available cover {inr(policy['available_cover'])}"))

            payee_type = "Hospital" if claim["claim_type"] == "Cashless" else "Member"
            payee_name = claim["snapshot"]["hospital_name"] if payee_type == "Hospital" else claim["snapshot"]["member_name"]
            utr = f"CSUR{now:%y%j}{random.randint(10000000, 99999999)}"
            mode = "RTGS" if amount >= 200000 else "NEFT"
            sets = {
                "status": SETTLED, "settled_amount": amount, "settled_at": now, "updated_at": now,
                "payment": {"payment_id": payment_id, "utr": utr, "mode": mode, "paid_at": now, "payee_type": payee_type},
            }
            if claim.get("submitted_at"):
                sets["settlement_tat_days"] = round((now - claim["submitted_at"]).total_seconds() / 86400, 1)
            res = tx.update_one("claims", {"claim_number": claim_number, "status": {"$in": AWAITING_PAYMENT}}, {
                "$set": sets,
                "$push": {"status_history": _history(SETTLED, user, f"Paid {inr(amount)} to {payee_type.lower()} via {mode} (UTR {utr})", now)}})
            if res.matched_count != 1:
                raise WorkflowError("Claim was changed by another user; settlement aborted")
            steps.append(("Update claim", f"{claim_number} → Settled"))

            if current_term:
                res = tx.update_one("policies",
                                    {"policy_number": policy["policy_number"], "available_cover": {"$gte": amount}},
                                    {"$inc": {"utilized_amount": amount, "available_cover": -amount},
                                     "$set": {"updated_at": now}})
                if res.matched_count != 1:
                    raise WorkflowError(f"Insufficient sum insured: available {inr(policy['available_cover'])}, "
                                        f"required {inr(amount)}")
                steps.append(("Deduct policy cover",
                              f"{policy['policy_number']}: available {inr(policy['available_cover'])} → "
                              f"{inr(policy['available_cover'] - amount)}"))
            else:
                steps.append(("Deduct policy cover", "Claim belongs to a previous policy year - current cover unchanged"))

            if simulate_failure:
                raise RuntimeError("Simulated failure: bank payment gateway timed out before the payment was recorded")

            tx.insert_one("payments", {
                "payment_id": payment_id, "claim_number": claim_number, "policy_number": claim["policy_number"],
                "insurer_id": claim["insurer_id"], "amount": amount,
                "payee": {"type": payee_type, "name": payee_name, "bank": "Pragati Bank",
                          "account_masked": f"XXXXXX{random.randint(1000, 9999)}", "ifsc": "PRGB0001234"},
                "mode": mode, "utr": utr, "status": "Success", "initiated_by": user["user_id"],
                "initiated_at": now, "paid_at": now,
            })
            steps.append(("Insert payment", f"{payment_id} for {inr(amount)} ({mode})"))
            audit.log(db, user, "CLAIM_SETTLED", "claim", claim_number, f"{claim_number}: settled for {inr(amount)}",
                      {"payment_id": payment_id, "utr": utr}, session=tx)
            steps.append(("Write audit log", "CLAIM_SETTLED"))
        steps.append(("COMMIT", "All writes applied atomically"))
        mark_changed()
        return {"committed": True, "steps": steps, "payment_id": payment_id}
    except Exception as exc:
        after = db.policies.find_one({"policy_number": claim["policy_number"]}, {"available_cover": 1, "utilized_amount": 1})
        status_after = db.claims.find_one({"claim_number": claim_number}, {"status": 1})["status"]
        paid = db.payments.count_documents({"claim_number": claim_number})
        steps.append(("ROLLBACK", str(exc)))
        unchanged = (after["available_cover"] == policy_before["available_cover"] and paid == 0
                     and status_after in AWAITING_PAYMENT)
        steps.append(("Verify", f"Claim status '{status_after}', available cover {inr(after['available_cover'])}, "
                                f"payments for claim: {paid} → " + ("database unchanged" if unchanged else "CHECK DATA")))
        return {"committed": False, "steps": steps, "error": str(exc), "consistent": unchanged}


# --------------------------------------------------------------------------- #
# New claim intimation
# --------------------------------------------------------------------------- #
def intimate_claim(db, form, user):
    """Create a claim from a form dict. Returns the new claim number."""
    policy = db.policies.find_one({"policy_number": (form.get("policy_number") or "").strip().upper()})
    if not policy:
        raise WorkflowError("Policy not found")
    member = next((m for m in policy["insured_members"] if m["member_id"] == form.get("member_id")), None)
    if not member:
        raise WorkflowError("Select a patient who is insured under this policy")
    hospital = db.hospitals.find_one({"hospital_id": form.get("hospital_id")})
    if not hospital:
        raise WorkflowError("Select a hospital")
    icd = db.icd_codes.find_one({"code": form.get("primary_icd")})
    if not icd:
        raise WorkflowError("Select a diagnosis")
    plan = db.plans.find_one({"plan_id": policy["plan_id"]})
    admission, discharge = parse_date(form.get("admission_date")), parse_date(form.get("discharge_date"))
    if not admission or not discharge:
        raise WorkflowError("Admission and discharge dates are required")
    if discharge < admission:
        raise WorkflowError("Discharge date cannot be before admission date")
    if admission > datetime.now():
        raise WorkflowError("Admission date cannot be in the future")
    network = policy["insurer_id"] in hospital["network"]["insurer_ids"]
    claim_type = form.get("claim_type", "Reimbursement")
    if claim_type == "Cashless" and not network:
        raise WorkflowError(f"{hospital['name']} is not in the insurer's network - file a Reimbursement claim")

    doctor = db.doctors.find_one({"hospital_id": hospital["hospital_id"], "specialization": icd["specialty"]}) \
        or db.doctors.find_one({"hospital_id": hospital["hospital_id"]})
    los = max((discharge - admission).days, 1)
    icu_days = min(int(form.get("icu_days") or 0), los)
    room_category = form.get("room_category") or "Twin Sharing"
    tier = hospital["city_tier"]
    bill = {k: int(float(form.get(k) or 0)) for k in BILL_KEYS}
    if any(v < 0 for v in bill.values()):
        raise WorkflowError("Bill amounts cannot be negative")
    bill["total"] = sum(bill.values())
    if bill["total"] <= 0:
        raise WorkflowError("Enter the hospital bill break-up")
    room_rent = int(float(form.get("room_rent_per_day") or 0)) or (bill["room_charges"] // max(los - icu_days, 1)) \
        or ROOM_TARIFF[room_category][tier]
    now = datetime.now()
    claim_number = f"CLM-{now.year}-{next_id(db, 'claim'):06d}"
    insurer = db.insurers.find_one({"insurer_id": policy["insurer_id"]}, {"name": 1})
    emergency = form.get("admission_type") == "Emergency"
    claim = {
        "claim_number": claim_number,
        "claim_type": claim_type,
        "admission_type": "Emergency" if emergency else "Planned",
        "status": INTIMATED,
        "policy_number": policy["policy_number"],
        "member_id": member["member_id"],
        "hospital_id": hospital["hospital_id"],
        "doctor_id": doctor["doctor_id"] if doctor else None,
        "insurer_id": policy["insurer_id"],
        "tpa_id": policy.get("tpa_id"),
        "plan_id": policy["plan_id"],
        "snapshot": {
            "member_name": member["name"], "member_gender": member["gender"],
            "member_age": age_on(member["dob"], admission), "relation": member["relation"],
            "hospital_name": hospital["name"], "hospital_city": hospital["address"]["city"],
            "hospital_state": hospital["address"]["state"], "hospital_tier": tier, "network_hospital": network,
            "insurer_name": insurer["name"], "plan_name": plan["name"],
            "doctor_name": doctor["name"] if doctor else None,
        },
        "primary_icd": icd["code"],
        "diagnosis": [{"icd_code": icd["code"], "description": icd["description"], "type": "Primary"}],
        "treatment": {
            "specialty": icd["specialty"],
            "line": "Day Care" if icd["day_care"] else ("Surgical" if icd["is_surgical"] else "Medical"),
            "procedure": icd["procedure"] or "Conservative medical management",
            "description": form.get("treatment_notes") or f"{icd['procedure'] or 'Medical management'} for {icd['description'].lower()}",
        },
        "admission_date": admission,
        "discharge_date": discharge,
        "length_of_stay": los,
        "icu_days": icu_days,
        "room_category": room_category,
        "room_rent_per_day": room_rent,
        "icu_rent_per_day": int(bill["icu_charges"] / icu_days) if icu_days else 0,
        "bill": bill,
        "claimed_amount": bill["total"],
        "approved_amount": 0,
        "settled_amount": 0,
        "pre_auth": {"requested_amount": bill["total"], "requested_at": now, "status": "Pending",
                     "approved_amount": 0, "decided_at": None, "remarks": None} if claim_type == "Cashless" else None,
        "intimation_date": now,
        "submitted_at": None,
        "decided_at": None,
        "settled_at": None,
        "tat_days": None,
        "settlement_tat_days": None,
        "assigned_to": None,
        "queries": [],
        "documents": [{"type": d, "status": "Received" if form.get("documents_complete") else "Pending",
                       "file_name": None, "uploaded_at": now if form.get("documents_complete") else None}
                      for d in DOCUMENT_TYPES[claim_type]],
        "status_history": [_history(INTIMATED, user, form.get("remarks") or "Claim intimated", now)],
        "payment": None,
        "created_at": now,
        "updated_at": now,
    }
    context = fraud.build_context(db, {member["member_id"]})
    scored = fraud.score_with_context(claim, policy["first_inception_date"], context)
    scored.update(scanned_at=now, review_status="Not Required" if scored["risk_level"] == "Low" else "Not Reviewed")
    claim["fraud"] = scored
    with write_lock:
        insert_validated(db, "claims", claim)
        audit.log(db, user, "CLAIM_INTIMATED", "claim", claim_number,
                  f"{claim_number} intimated for {member['name']} at {hospital['name']}",
                  {"claimed": bill["total"], "type": claim_type})
        mark_changed()
    return claim_number


# --------------------------------------------------------------------------- #
# Fraud review and grievances
# --------------------------------------------------------------------------- #
def review_fraud(db, claim_number, user, decision, notes=""):
    if decision not in ("Cleared", "Under Investigation", "Confirmed Fraud"):
        raise WorkflowError("Invalid review decision")
    if user["role"] not in ("Fraud Analyst", "Admin", "Claims Manager"):
        raise WorkflowError(f"A {user['role']} cannot review fraud alerts")
    now = datetime.now()
    with write_lock:
        get_claim(db, claim_number)
        db.claims.update_one({"claim_number": claim_number}, {"$set": {
            "fraud.review_status": decision, "fraud.reviewed_by": user["user_id"],
            "fraud.reviewed_at": now, "fraud.review_notes": notes, "updated_at": now}})
        audit.log(db, user, "FRAUD_REVIEW", "claim", claim_number, f"{claim_number}: fraud review - {decision}",
                  {"notes": notes})
        mark_changed()


def rescan_fraud(db, user):
    with write_lock:
        counts = fraud.scan_claims(db, {"status": {"$nin": [SETTLED, REJECTED, WITHDRAWN]}})
        audit.log(db, user, "FRAUD_SCAN", "system", "claims", "Fraud rules re-run on open claims", counts)
        mark_changed()
    return counts


def raise_grievance(db, claim_number, user, category, channel, description, priority="Medium"):
    if category not in GRIEVANCE_CATEGORIES:
        raise WorkflowError("Choose a grievance category")
    claim = get_claim(db, claim_number)
    now = datetime.now()
    gid = f"GRV-{now.year}-{next_id(db, 'grievance'):05d}"
    with write_lock:
        insert_validated(db, "grievances", {
            "grievance_id": gid, "claim_number": claim_number, "member_id": claim["member_id"],
            "policy_number": claim["policy_number"], "insurer_id": claim["insurer_id"],
            "category": category, "channel": channel, "description": description or category,
            "status": "Open", "priority": priority, "raised_at": now, "resolved_at": None,
            "resolution": None, "assigned_to": user["user_id"],
        })
        audit.log(db, user, "GRIEVANCE_RAISED", "grievance", gid, f"{gid} raised on {claim_number}", {"category": category})
        mark_changed()
    return gid


def update_grievance(db, grievance_id, user, status, resolution=""):
    if status not in ("Open", "In Progress", "Resolved", "Escalated to Ombudsman"):
        raise WorkflowError("Invalid grievance status")
    if status == "Resolved" and not resolution.strip():
        raise WorkflowError("Enter the resolution given to the customer")
    now = datetime.now()
    with write_lock:
        res = db.grievances.update_one({"grievance_id": grievance_id}, {"$set": {
            "status": status, "resolution": resolution or None,
            "resolved_at": now if status == "Resolved" else None}})
        if not res.matched_count:
            raise WorkflowError("Grievance not found")
        audit.log(db, user, "GRIEVANCE_UPDATED", "grievance", grievance_id, f"{grievance_id} → {status}",
                  {"resolution": resolution})
        mark_changed()
