"""Business-intelligence aggregation pipelines.

Every report is an aggregation pipeline that runs inside the database. Each
function returns ``(rows, pipelines)`` so the UI can show both the result and
the exact pipeline that produced it. The pipelines avoid operators that
mongomock does not implement (e.g. $round, $dateDiff), so they run unchanged
in mock mode and on a real MongoDB server; rounding happens in Python.
"""
import time
from datetime import datetime

from .constants import SETTLED, REJECTED, OPEN_STATUSES, AWAITING_PAYMENT, CLAIM_STATUSES
from .services import data_version

CLAIM_WINDOW_START = datetime(2024, 4, 1)  # first claim in the data set


def _r(value, digits=1):
    return round(value, digits) if isinstance(value, (int, float)) else value


def slim(*fields):
    """Project early: keep only the fields a pipeline needs (less data per stage)."""
    return {"$project": {"_id": 0, **{f: 1 for f in fields}}}


def pct(part, whole, digits=1):
    return round(100.0 * part / whole, digits) if whole else 0.0


# --------------------------------------------------------------------------- #
# caching - results are reused until the data changes (or 60 s pass)
# --------------------------------------------------------------------------- #
_cache = {}


def cached(fn):
    def wrapper(db, *args):
        key = (fn.__name__, args)
        hit = _cache.get(key)
        if hit and hit[0] == data_version() and time.time() - hit[1] < 60:
            return hit[2]
        started = time.perf_counter()
        value = fn(db, *args)
        _cache[key] = (data_version(), time.time(), value, round((time.perf_counter() - started) * 1000))
        return value
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def clear_cache():
    _cache.clear()


# --------------------------------------------------------------------------- #
# Dashboard KPIs - one round trip with $facet
# --------------------------------------------------------------------------- #
@cached
def kpis(db):
    pipeline = [slim("status", "claim_type", "claimed_amount", "approved_amount", "settled_amount", "tat_days",
                     "settlement_tat_days", "fraud.risk_level", "intimation_date"),
                {"$facet": {
        "totals": [{"$group": {"_id": None, "claims": {"$sum": 1}, "claimed": {"$sum": "$claimed_amount"},
                               "approved": {"$sum": "$approved_amount"}, "settled": {"$sum": "$settled_amount"}}}],
        "by_status": [{"$group": {"_id": "$status", "n": {"$sum": 1}, "amount": {"$sum": "$claimed_amount"}}}],
        "tat": [{"$match": {"tat_days": {"$type": "number"}}},
                {"$group": {"_id": "$claim_type", "decision": {"$avg": "$tat_days"}, "n": {"$sum": 1}}}],
        "settle_tat": [{"$match": {"settlement_tat_days": {"$type": "number"}}},
                       {"$group": {"_id": None, "avg": {"$avg": "$settlement_tat_days"}}}],
        "fraud": [{"$match": {"fraud.risk_level": {"$in": ["High", "Medium"]},
                              "status": {"$nin": [SETTLED, REJECTED, "Withdrawn"]}}},
                  {"$group": {"_id": "$fraud.risk_level", "n": {"$sum": 1}}}],
        "last_30": [{"$match": {"intimation_date": {"$gte": datetime.fromtimestamp(time.time() - 30 * 86400)}}},
                    {"$count": "n"}],
    }}]
    out = list(db.claims.aggregate(pipeline))[0]
    totals = (out["totals"] or [{}])[0]
    status = {r["_id"]: r for r in out["by_status"]}
    n = lambda s: status.get(s, {}).get("n", 0)  # noqa: E731
    decided = n(SETTLED) + n(REJECTED) + sum(n(s) for s in AWAITING_PAYMENT)
    tat = {r["_id"]: r["decision"] for r in out["tat"]}
    policy_stats = list(db.policies.aggregate([{"$group": {
        "_id": None, "active": {"$sum": {"$cond": [{"$eq": ["$status", "Active"]}, 1, 0]}},
        "lives": {"$sum": {"$size": "$insured_members"}},
        "premium": {"$sum": {"$cond": [{"$eq": ["$status", "Active"]}, "$premium.total", 0]}},
        "cover": {"$sum": {"$cond": [{"$eq": ["$status", "Active"]}, "$total_cover", 0]}}}}]))[0]
    return {
        "total_claims": totals.get("claims", 0),
        "claimed": totals.get("claimed", 0),
        "approved": totals.get("approved", 0),
        "settled": totals.get("settled", 0),
        "open": sum(n(s) for s in OPEN_STATUSES),
        "awaiting_payment": sum(n(s) for s in AWAITING_PAYMENT),
        "awaiting_payment_amount": sum(status.get(s, {}).get("amount", 0) for s in AWAITING_PAYMENT),
        "settlement_ratio": pct(n(SETTLED) + sum(n(s) for s in AWAITING_PAYMENT), decided),
        "rejection_ratio": pct(n(REJECTED), decided),
        "tat_cashless": _r(tat.get("Cashless", 0)),
        "tat_reimbursement": _r(tat.get("Reimbursement", 0)),
        "settlement_tat": _r((out["settle_tat"] or [{"avg": 0}])[0]["avg"]),
        "fraud_high": sum(r["n"] for r in out["fraud"] if r["_id"] == "High"),
        "fraud_medium": sum(r["n"] for r in out["fraud"] if r["_id"] == "Medium"),
        "last_30_days": (out["last_30"] or [{"n": 0}])[0]["n"],
        "status_counts": {s: n(s) for s in CLAIM_STATUSES},
        "active_policies": policy_stats["active"],
        "lives_covered": policy_stats["lives"],
        "premium_in_force": policy_stats["premium"],
        "cover_in_force": policy_stats["cover"],
        "pipeline": pipeline,
    }


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
REPORTS = {}


def report(key, title, description, chart, collection="claims"):
    def deco(fn):
        REPORTS[key] = {"key": key, "title": title, "description": description, "chart": chart,
                        "collection": collection, "fn": cached(fn)}
        return REPORTS[key]["fn"]
    return deco


@report("monthly_trend", "Monthly claim inflow & payout",
        "Claims intimated, amount claimed and amount settled per month of admission.", "line")
def monthly_trend(db):
    pipeline = [
        slim("admission_date", "claimed_amount", "settled_amount", "status"),
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m", "date": "$admission_date"}},
                    "claims": {"$sum": 1}, "claimed": {"$sum": "$claimed_amount"},
                    "settled": {"$sum": "$settled_amount"},
                    "rejected": {"$sum": {"$cond": [{"$eq": ["$status", REJECTED]}, 1, 0]}}}},
        {"$sort": {"_id": 1}},
    ]
    rows = [{"month": r["_id"], "claims": r["claims"], "claimed": r["claimed"], "settled": r["settled"],
             "rejected": r["rejected"]} for r in db.claims.aggregate(pipeline)]
    return rows, [pipeline]


@report("status_mix", "Claims by status", "Current position of every claim in the lifecycle.", "doughnut")
def status_mix(db):
    pipeline = [slim("status", "claimed_amount"),
                {"$group": {"_id": "$status", "claims": {"$sum": 1}, "amount": {"$sum": "$claimed_amount"}}},
                {"$sort": {"claims": -1}}]
    rows = [{"status": r["_id"], "claims": r["claims"], "amount": r["amount"]} for r in db.claims.aggregate(pipeline)]
    return rows, [pipeline]


@report("claim_type", "Cashless vs reimbursement",
        "Volume, average bill and turnaround time by claim type.", "bar")
def claim_type(db):
    pipeline = [slim("claim_type", "claimed_amount", "settled_amount", "tat_days", "settlement_tat_days", "status"),
                {"$group": {"_id": "$claim_type", "claims": {"$sum": 1}, "avg_claimed": {"$avg": "$claimed_amount"},
                            "settled": {"$sum": "$settled_amount"}, "avg_tat": {"$avg": "$tat_days"},
                            "avg_settlement_tat": {"$avg": "$settlement_tat_days"},
                            "rejected": {"$sum": {"$cond": [{"$eq": ["$status", REJECTED]}, 1, 0]}}}},
                {"$sort": {"claims": -1}}]
    rows = [{"claim_type": r["_id"], "claims": r["claims"], "avg_claimed": round(r["avg_claimed"]),
             "settled": r["settled"], "avg_tat_days": _r(r["avg_tat"]),
             "avg_settlement_days": _r(r["avg_settlement_tat"]), "rejection_rate": pct(r["rejected"], r["claims"])}
            for r in db.claims.aggregate(pipeline)]
    return rows, [pipeline]


@report("insurer_performance", "Insurer scorecard",
        "Claim settlement ratio, rejection rate, turnaround and incurred claim ratio (claims paid / premium "
        "earned) per insurer. Two pipelines: claims grouped then joined to insurers with $lookup, and premium "
        "earned from the embedded renewal history.", "bar")
def insurer_performance(db):
    claims_pipeline = [
        slim("insurer_id", "status", "settled_amount", "claimed_amount", "tat_days"),
        {"$group": {"_id": "$insurer_id", "claims": {"$sum": 1},
                    "settled_n": {"$sum": {"$cond": [{"$in": ["$status", [SETTLED] + AWAITING_PAYMENT]}, 1, 0]}},
                    "rejected_n": {"$sum": {"$cond": [{"$eq": ["$status", REJECTED]}, 1, 0]}},
                    "paid": {"$sum": "$settled_amount"}, "claimed": {"$sum": "$claimed_amount"},
                    "avg_tat": {"$avg": "$tat_days"}}},
        {"$lookup": {"from": "insurers", "localField": "_id", "foreignField": "insurer_id", "as": "insurer"}},
        {"$unwind": "$insurer"},
        {"$project": {"_id": 0, "insurer_id": "$_id", "name": "$insurer.name", "type": "$insurer.type",
                      "claims": 1, "settled_n": 1, "rejected_n": 1, "paid": 1, "claimed": 1, "avg_tat": 1}},
        {"$sort": {"claims": -1}},
    ]
    premium_pipeline = [
        slim("insurer_id", "policy_number", "renewal_history.end_date", "renewal_history.premium.total"),
        {"$unwind": "$renewal_history"},
        {"$match": {"renewal_history.end_date": {"$gte": CLAIM_WINDOW_START}}},
        {"$group": {"_id": "$insurer_id", "premium": {"$sum": "$renewal_history.premium.total"},
                    "policies": {"$addToSet": "$policy_number"}}},
        {"$project": {"premium": 1, "policies": {"$size": "$policies"}}},
    ]
    premium = {r["_id"]: r for r in db.policies.aggregate(premium_pipeline)}
    rows = []
    for r in db.claims.aggregate(claims_pipeline):
        p = premium.get(r["insurer_id"], {"premium": 0, "policies": 0})
        decided = r["settled_n"] + r["rejected_n"]
        rows.append({"insurer_id": r["insurer_id"], "insurer": r["name"], "type": r["type"],
                     "claims": r["claims"], "settlement_ratio": pct(r["settled_n"], decided),
                     "rejection_rate": pct(r["rejected_n"], decided), "avg_tat_days": _r(r["avg_tat"]),
                     "paid": r["paid"], "premium": p["premium"], "policies": p["policies"],
                     "incurred_claim_ratio": pct(r["paid"], p["premium"])})
    return rows, [claims_pipeline, premium_pipeline]


@report("hospital_performance", "Top hospitals by payout",
        "Top 15 hospitals by amount paid; grouped first and then joined with $lookup (group-before-lookup keeps "
        "the join small).", "bar")
def hospital_performance(db):
    pipeline = [
        slim("hospital_id", "settled_amount", "claimed_amount", "length_of_stay", "status", "fraud.score"),
        {"$group": {"_id": "$hospital_id", "claims": {"$sum": 1}, "paid": {"$sum": "$settled_amount"},
                    "avg_bill": {"$avg": "$claimed_amount"}, "avg_los": {"$avg": "$length_of_stay"},
                    "rejected": {"$sum": {"$cond": [{"$eq": ["$status", REJECTED]}, 1, 0]}},
                    "flagged": {"$sum": {"$cond": [{"$gte": ["$fraud.score", 30]}, 1, 0]}}}},
        {"$sort": {"paid": -1}},
        {"$limit": 15},
        {"$lookup": {"from": "hospitals", "localField": "_id", "foreignField": "hospital_id", "as": "h"}},
        {"$unwind": "$h"},
        {"$project": {"_id": 0, "hospital_id": "$_id", "name": "$h.name", "city": "$h.address.city",
                      "type": "$h.type", "accreditation": "$h.accreditation", "watchlisted": "$h.watchlisted",
                      "claims": 1, "paid": 1, "avg_bill": 1, "avg_los": 1, "rejected": 1, "flagged": 1}},
    ]
    rows = [{**r, "avg_bill": round(r["avg_bill"]), "avg_los": _r(r["avg_los"]),
             "rejection_rate": pct(r["rejected"], r["claims"])} for r in db.claims.aggregate(pipeline)]
    return rows, [pipeline]


@report("top_diagnoses", "Top diagnoses (ICD-10)",
        "Primary diagnoses by volume, with the average bill compared to the benchmark cost. Uses $unwind on the "
        "diagnosis array and $lookup into the ICD master.", "bar")
def top_diagnoses(db):
    pipeline = [
        slim("diagnosis.icd_code", "diagnosis.type", "claimed_amount", "settled_amount"),
        {"$unwind": "$diagnosis"},
        {"$match": {"diagnosis.type": "Primary"}},
        {"$group": {"_id": "$diagnosis.icd_code", "claims": {"$sum": 1}, "avg_claimed": {"$avg": "$claimed_amount"},
                    "paid": {"$sum": "$settled_amount"}}},
        {"$sort": {"claims": -1}},
        {"$limit": 12},
        {"$lookup": {"from": "icd_codes", "localField": "_id", "foreignField": "code", "as": "icd"}},
        {"$unwind": "$icd"},
        {"$project": {"_id": 0, "code": "$_id", "description": "$icd.description", "specialty": "$icd.specialty",
                      "benchmark": "$icd.avg_cost", "claims": 1, "avg_claimed": 1, "paid": 1}},
    ]
    rows = [{**r, "avg_claimed": round(r["avg_claimed"]),
             "vs_benchmark": pct(r["avg_claimed"] - r["benchmark"], r["benchmark"])}
            for r in db.claims.aggregate(pipeline)]
    return rows, [pipeline]


@report("specialty_mix", "Claims by speciality", "Volume and payout by treating speciality.", "bar")
def specialty_mix(db):
    pipeline = [slim("treatment.specialty", "treatment.line", "settled_amount"),
                {"$group": {"_id": "$treatment.specialty", "claims": {"$sum": 1}, "paid": {"$sum": "$settled_amount"},
                            "surgical": {"$sum": {"$cond": [{"$eq": ["$treatment.line", "Surgical"]}, 1, 0]}}}},
                {"$sort": {"paid": -1}}]
    rows = [{"specialty": r["_id"], "claims": r["claims"], "paid": r["paid"],
             "surgical_share": pct(r["surgical"], r["claims"])} for r in db.claims.aggregate(pipeline)]
    return rows, [pipeline]


@report("age_bands", "Claims by age band",
        "$bucket groups patients into age bands; older members claim more often and cost more.", "bar")
def age_bands(db):
    pipeline = [slim("snapshot.member_age", "claimed_amount", "settled_amount"),
                {"$bucket": {"groupBy": "$snapshot.member_age", "boundaries": [0, 18, 31, 46, 61, 76, 121],
                             "default": "Unknown",
                             "output": {"claims": {"$sum": 1}, "avg_claimed": {"$avg": "$claimed_amount"},
                                        "paid": {"$sum": "$settled_amount"}}}}]
    labels = {0: "0-17", 18: "18-30", 31: "31-45", 46: "46-60", 61: "61-75", 76: "76+"}
    rows = [{"band": labels.get(r["_id"], str(r["_id"])), "claims": r["claims"],
             "avg_claimed": round(r["avg_claimed"]), "paid": r["paid"]} for r in db.claims.aggregate(pipeline)]
    return rows, [pipeline]


@report("state_mix", "Claims by state", "Where treatment happens (hospital state).", "bar")
def state_mix(db):
    pipeline = [slim("snapshot.hospital_state", "settled_amount", "hospital_id"),
                {"$group": {"_id": "$snapshot.hospital_state", "claims": {"$sum": 1}, "paid": {"$sum": "$settled_amount"},
                            "hospitals": {"$addToSet": "$hospital_id"}}},
                {"$project": {"claims": 1, "paid": 1, "hospitals": {"$size": "$hospitals"}}},
                {"$sort": {"claims": -1}}]
    rows = [{"state": r["_id"], "claims": r["claims"], "paid": r["paid"], "hospitals": r["hospitals"]}
            for r in db.claims.aggregate(pipeline)]
    return rows, [pipeline]


@report("tat_buckets", "Decision turnaround time",
        "$bucket on days from document submission to decision. IRDAI expects decisions within 30 days.", "bar")
def tat_buckets(db):
    pipeline = [{"$match": {"tat_days": {"$type": "number"}}},
                slim("tat_days", "claim_type"),
                {"$bucket": {"groupBy": "$tat_days", "boundaries": [0, 1, 3, 7, 15, 30, 10000],
                             "output": {"claims": {"$sum": 1},
                                        "cashless": {"$sum": {"$cond": [{"$eq": ["$claim_type", "Cashless"]}, 1, 0]}}}}}]
    labels = {0: "< 1 day", 1: "1-3 days", 3: "3-7 days", 7: "7-15 days", 15: "15-30 days", 30: "> 30 days"}
    rows = [{"bucket": labels[r["_id"]], "claims": r["claims"], "cashless": r["cashless"],
             "reimbursement": r["claims"] - r["cashless"]} for r in db.claims.aggregate(pipeline)]
    return rows, [pipeline]


@report("rejection_reasons", "Rejection reasons", "Why claims are repudiated.", "bar")
def rejection_reasons(db):
    pipeline = [{"$match": {"status": REJECTED}},
                slim("adjudication.rejection_code", "adjudication.rejection_reason", "claimed_amount"),
                {"$group": {"_id": {"code": "$adjudication.rejection_code", "reason": "$adjudication.rejection_reason"},
                            "claims": {"$sum": 1}, "amount": {"$sum": "$claimed_amount"}}},
                {"$sort": {"claims": -1}}]
    rows = [{"code": r["_id"]["code"], "reason": r["_id"]["reason"], "claims": r["claims"], "amount": r["amount"]}
            for r in db.claims.aggregate(pipeline)]
    return rows, [pipeline]


@report("deductions", "Where deductions come from",
        "$unwind on the adjudication deductions array shows which policy conditions reduce payouts.", "bar")
def deductions(db):
    pipeline = [{"$match": {"adjudication.deductions.0": {"$exists": True}}},
                slim("adjudication.deductions.category", "adjudication.deductions.amount"),
                {"$unwind": "$adjudication.deductions"},
                {"$group": {"_id": "$adjudication.deductions.category", "claims": {"$sum": 1},
                            "amount": {"$sum": "$adjudication.deductions.amount"}}},
                {"$sort": {"amount": -1}}]
    rows = [{"category": r["_id"], "claims": r["claims"], "amount": r["amount"]} for r in db.claims.aggregate(pipeline)]
    return rows, [pipeline]


@report("plan_loss_ratio", "Loss ratio by product",
        "Claims paid vs premium earned for each plan category - the core profitability metric.", "bar",
        collection="policies")
def plan_loss_ratio(db):
    premium_pipeline = [
        slim("plan_id", "policy_number", "renewal_history.end_date", "renewal_history.premium.total"),
        {"$unwind": "$renewal_history"},
        {"$match": {"renewal_history.end_date": {"$gte": CLAIM_WINDOW_START}}},
        {"$group": {"_id": "$plan_id", "premium": {"$sum": "$renewal_history.premium.total"},
                    "policies": {"$addToSet": "$policy_number"}}},
        # group first, then join: 35 plan rows instead of thousands of policy terms
        {"$lookup": {"from": "plans", "localField": "_id", "foreignField": "plan_id", "as": "plan"}},
        {"$unwind": "$plan"},
        {"$group": {"_id": "$plan.category", "premium": {"$sum": "$premium"},
                    "policies": {"$sum": {"$size": "$policies"}}}},
    ]
    claims_pipeline = [
        slim("plan_id", "settled_amount"),
        {"$group": {"_id": "$plan_id", "paid": {"$sum": "$settled_amount"}, "claims": {"$sum": 1}}},
        {"$lookup": {"from": "plans", "localField": "_id", "foreignField": "plan_id", "as": "plan"}},
        {"$unwind": "$plan"},
        {"$group": {"_id": "$plan.category", "paid": {"$sum": "$paid"}, "claims": {"$sum": "$claims"}}},
    ]
    paid = {r["_id"]: r for r in db.claims.aggregate(claims_pipeline)}
    rows = []
    for r in db.policies.aggregate(premium_pipeline):
        c = paid.get(r["_id"], {"paid": 0, "claims": 0})
        rows.append({"category": r["_id"], "policies": r["policies"], "premium": r["premium"], "paid": c["paid"],
                     "claims": c["claims"], "loss_ratio": pct(c["paid"], r["premium"])})
    rows.sort(key=lambda x: -x["loss_ratio"])
    return rows, [premium_pipeline, claims_pipeline]


@report("adjuster_productivity", "Adjuster productivity",
        "Decisions per staff member, from the embedded status history ($unwind + $match + $lookup).", "bar")
def adjuster_productivity(db):
    pipeline = [
        slim("status_history.status", "status_history.by", "approved_amount"),
        {"$unwind": "$status_history"},
        {"$match": {"status_history.status": {"$in": ["Approved", "Partially Approved", "Rejected"]}}},
        {"$group": {"_id": "$status_history.by", "decisions": {"$sum": 1},
                    "rejections": {"$sum": {"$cond": [{"$eq": ["$status_history.status", REJECTED]}, 1, 0]}},
                    "amount": {"$sum": "$approved_amount"}}},
        {"$lookup": {"from": "users", "localField": "_id", "foreignField": "user_id", "as": "u"}},
        {"$unwind": "$u"},
        {"$project": {"_id": 0, "user_id": "$_id", "name": "$u.name", "role": "$u.role", "region": "$u.region",
                      "decisions": 1, "rejections": 1, "amount": 1}},
        {"$sort": {"decisions": -1}},
    ]
    rows = [{**r, "rejection_rate": pct(r["rejections"], r["decisions"])} for r in db.claims.aggregate(pipeline)]
    return rows, [pipeline]


@report("top_claimants", "Highest-cost members", "Members with the largest total payout.", "table")
def top_claimants(db):
    pipeline = [
        slim("member_id", "snapshot.member_name", "snapshot.member_age", "claimed_amount", "settled_amount"),
        {"$group": {"_id": "$member_id", "name": {"$first": "$snapshot.member_name"},
                    "age": {"$max": "$snapshot.member_age"}, "claims": {"$sum": 1},
                    "claimed": {"$sum": "$claimed_amount"}, "paid": {"$sum": "$settled_amount"}}},
        {"$sort": {"paid": -1}},
        {"$limit": 10},
    ]
    rows = [{"member_id": r["_id"], "name": r["name"], "age": r["age"], "claims": r["claims"],
             "claimed": r["claimed"], "paid": r["paid"]} for r in db.claims.aggregate(pipeline)]
    return rows, [pipeline]


@report("channel_mix", "Policies by sales channel", "Distribution channel mix and premium.", "doughnut",
        collection="policies")
def channel_mix(db):
    pipeline = [slim("channel", "premium.total", "insured_members.member_id"),
                {"$group": {"_id": "$channel", "policies": {"$sum": 1}, "premium": {"$sum": "$premium.total"},
                            "lives": {"$sum": {"$size": "$insured_members"}}}},
                {"$sort": {"policies": -1}}]
    rows = [{"channel": r["_id"], "policies": r["policies"], "premium": r["premium"], "lives": r["lives"]}
            for r in db.policies.aggregate(pipeline)]
    return rows, [pipeline]


@report("portfolio", "Policy portfolio", "Policies, lives and average cover by policy type.", "bar",
        collection="policies")
def portfolio(db):
    pipeline = [{"$match": {"status": "Active"}},
                slim("policy_type", "insured_members.member_id", "total_cover", "premium.total", "utilized_amount"),
                {"$group": {"_id": "$policy_type", "policies": {"$sum": 1}, "lives": {"$sum": {"$size": "$insured_members"}},
                            "avg_cover": {"$avg": "$total_cover"}, "premium": {"$sum": "$premium.total"},
                            "utilised": {"$sum": "$utilized_amount"}, "cover": {"$sum": "$total_cover"}}},
                {"$sort": {"policies": -1}}]
    rows = [{"policy_type": r["_id"], "policies": r["policies"], "lives": r["lives"], "avg_cover": round(r["avg_cover"]),
             "premium": r["premium"], "utilisation": pct(r["utilised"], r["cover"])} for r in db.policies.aggregate(pipeline)]
    return rows, [pipeline]


@report("fraud_flags", "Fraud red flags", "How often each fraud rule fires and the average score of those claims.",
        "bar")
def fraud_flags(db):
    pipeline = [{"$match": {"fraud.flags.0": {"$exists": True}}},
                slim("fraud.flags.code", "fraud.flags.description", "fraud.score", "claimed_amount"),
                {"$unwind": "$fraud.flags"},
                {"$group": {"_id": "$fraud.flags.code", "description": {"$first": "$fraud.flags.description"},
                            "claims": {"$sum": 1}, "avg_score": {"$avg": "$fraud.score"},
                            "amount": {"$sum": "$claimed_amount"}}},
                {"$sort": {"claims": -1}}]
    rows = [{"rule": r["_id"], "description": r["description"], "claims": r["claims"],
             "avg_score": _r(r["avg_score"]), "amount": r["amount"]} for r in db.claims.aggregate(pipeline)]
    return rows, [pipeline]


@report("grievances", "Grievances", "Customer grievances by category and status.", "bar", collection="grievances")
def grievances(db):
    pipeline = [{"$group": {"_id": "$category", "total": {"$sum": 1},
                            "open": {"$sum": {"$cond": [{"$in": ["$status", ["Open", "In Progress"]]}, 1, 0]}},
                            "ombudsman": {"$sum": {"$cond": [{"$eq": ["$status", "Escalated to Ombudsman"]}, 1, 0]}}}},
                {"$sort": {"total": -1}}]
    rows = [{"category": r["_id"], "total": r["total"], "open": r["open"], "ombudsman": r["ombudsman"]}
            for r in db.grievances.aggregate(pipeline)]
    return rows, [pipeline]


def run_report(db, key):
    """Run (or reuse) a report. ``ms`` is the pipeline execution time; ``cached`` says if it was reused."""
    meta = REPORTS[key]
    started = time.perf_counter()
    rows, pipelines = meta["fn"](db)
    elapsed = round((time.perf_counter() - started) * 1000)
    entry = _cache.get((key, ()))
    ms = entry[3] if entry else elapsed
    return {**{k: v for k, v in meta.items() if k != "fn"}, "rows": rows, "pipelines": pipelines,
            "ms": ms, "cached": elapsed < ms}
