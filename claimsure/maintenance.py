"""Database administration jobs.

* renew_due_policies   - nightly batch: auto-renew policies whose term ended (one bulk_write)
* migrate_schema       - schema versioning: upgrade claims from schema v1 to v2
* create_views         - read-only views, incl. a PII-masked view for analysts
* refresh_materialized - on-demand materialised view built with $merge
* backup / restore     - logical backup of every collection to Extended JSON
"""
import json
import os
import time
from datetime import datetime, timedelta

from bson import json_util
from pymongo import UpdateOne

from . import audit
from .db import is_mock, write_lock, ROOT
from .seed import COLLECTIONS

BACKUP_DIR = os.path.join(ROOT, "backups")


def _add_years(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


# --------------------------------------------------------------------------- #
# 1. Policy auto-renewal (bulk write)
# --------------------------------------------------------------------------- #
def renew_due_policies(db, today=None, user=audit.SYSTEM_USER):
    """Renew every Active policy whose current term has ended.

    All renewals go to the server in ONE bulk_write round trip. Each update is
    guarded by the old end_date, so running the job twice cannot renew twice.
    """
    today = today or datetime.now()
    plans = {p["plan_id"]: p for p in db.plans.find({}, {"plan_id": 1, "features.no_claim_bonus": 1})}
    due = list(db.policies.find({"status": "Active", "end_date": {"$lt": today}},
                                {"policy_number": 1, "plan_id": 1, "start_date": 1, "end_date": 1, "sum_insured": 1,
                                 "renewal_history": {"$slice": -1}, "renewal_count": 1}))
    ops, logs = [], []
    for p in due:
        last = p["renewal_history"][-1]
        start, end, term, ncb, premium = p["start_date"], p["end_date"], last["term"], last["ncb_percent"], last["premium"]["base"]
        rule = plans[p["plan_id"]]["features"]["no_claim_bonus"]
        new_terms = []
        while end < today:  # catch up if more than one year was missed
            start = end + timedelta(days=1)
            end = _add_years(start, 1) - timedelta(days=1)
            term += 1
            ncb = min(rule["max_percent"], ncb + rule["percent_per_year"])
            premium = round(premium * 1.05)
            new_terms.append({"term": term, "start_date": start, "end_date": end, "sum_insured": p["sum_insured"],
                              "ncb_percent": ncb, "premium": {"base": premium, "gst": round(premium * 0.18),
                                                              "total": premium + round(premium * 0.18)}})
        bonus = p["sum_insured"] * ncb // 100
        cur = new_terms[-1]
        ops.append((
            {"_id": p["_id"], "end_date": p["end_date"]},
            {"$set": {"start_date": start, "end_date": end, "cumulative_bonus": bonus,
                      "total_cover": p["sum_insured"] + bonus, "available_cover": p["sum_insured"] + bonus,
                      "utilized_amount": 0, "renewal_count": p.get("renewal_count", 0) + len(new_terms),
                      "premium.base": cur["premium"]["base"], "premium.gst": cur["premium"]["gst"],
                      "premium.total": cur["premium"]["total"], "updated_at": today},
             "$push": {"renewal_history": {"$each": new_terms}}}))
        logs.append({"timestamp": today, "actor_id": user["user_id"], "actor_name": user["name"],
                     "actor_role": user.get("role", "System"), "action": "POLICY_RENEWED", "entity_type": "policy",
                     "entity_id": p["policy_number"], "summary": f"{p['policy_number']} auto-renewed to {end:%d %b %Y}",
                     "details": {"terms_added": len(new_terms), "premium": cur["premium"]["total"]}})
    started = time.perf_counter()
    modified = 0
    if ops:
        with write_lock:
            if is_mock():  # mongomock's bulk_write is incompatible with current pymongo
                modified = sum(db.policies.update_one(f, u).modified_count for f, u in ops)
            else:
                result = db.policies.bulk_write([UpdateOne(f, u) for f, u in ops], ordered=False)
                modified = result.modified_count
            db.audit_logs.insert_many(logs)
    return {"due": len(due), "renewed": modified, "ms": round((time.perf_counter() - started) * 1000)}


# --------------------------------------------------------------------------- #
# 2. Schema versioning
# --------------------------------------------------------------------------- #
CURRENT_SCHEMA = 2
MIGRATIONS = {
    2: "Adds 'version' (optimistic-locking counter) and stamps schema_version = 2 on every claim",
}


def schema_versions(db):
    rows = db.claims.aggregate([{"$group": {"_id": {"$ifNull": ["$schema_version", 1]}, "n": {"$sum": 1}}},
                                {"$sort": {"_id": 1}}])
    return {int(r["_id"]): r["n"] for r in rows}


def migrate_schema(db):
    """Upgrade v1 claims to v2. Idempotent: only touches documents still on v1."""
    started = time.perf_counter()
    with write_lock:
        res = db.claims.update_many({"schema_version": {"$exists": False}},
                                    {"$set": {"schema_version": 2, "version": 0}})
    return {"migrated": res.modified_count, "versions": schema_versions(db),
            "ms": round((time.perf_counter() - started) * 1000)}


# --------------------------------------------------------------------------- #
# 3. Views
# --------------------------------------------------------------------------- #
VIEWS = {
    "members_masked": {
        "on": "members",
        "purpose": "PII-safe member data for analysts: no KYC, masked phone and email",
        "pipeline": [
            {"$project": {
                "_id": 0, "member_id": 1, "gender": 1, "family_id": 1, "blood_group": 1,
                "pre_existing_conditions": 1, "address.city": 1, "address.state": 1,
                "birth_year": {"$year": "$dob"},
                "name": {"$concat": [{"$substrCP": ["$name", 0, 1]}, "***"]},
                "phone": {"$concat": ["XXXXXX", {"$substrCP": ["$contact.phone", 10, 4]}]},
            }},
        ],
    },
    "open_claims_queue": {
        "on": "claims",
        "purpose": "Work queue: open claims with only the fields a reviewer needs",
        "pipeline": [
            {"$match": {"status": {"$in": ["Intimated", "Documents Pending", "Under Review", "Query Raised", "Escalated"]}}},
            {"$project": {"_id": 0, "claim_number": 1, "status": 1, "assigned_to": 1, "claimed_amount": 1,
                          "intimation_date": 1, "member": "$snapshot.member_name",
                          "hospital": "$snapshot.hospital_name", "risk": "$fraud.risk_level"}},
        ],
    },
}


def create_views(db):
    """Create (or recreate) the read-only views. Real MongoDB only."""
    if is_mock():
        return {"created": [], "note": "Views need a real MongoDB server (mongomock does not support them)."}
    existing = set(db.list_collection_names())
    for name, spec in VIEWS.items():
        if name in existing:
            db.drop_collection(name)
        db.command("create", name, viewOn=spec["on"], pipeline=spec["pipeline"])
    return {"created": list(VIEWS)}


def view_sample(db, name, limit=5):
    if is_mock() or name not in db.list_collection_names():
        return None
    return list(db[name].find().limit(limit))


# --------------------------------------------------------------------------- #
# 4. Materialised view ($merge)
# --------------------------------------------------------------------------- #
MV_NAME = "mv_insurer_monthly"
MV_PIPELINE = [
    {"$project": {"insurer_id": 1, "claimed_amount": 1, "settled_amount": 1, "status": 1,
                  "month": {"$dateToString": {"format": "%Y-%m", "date": "$admission_date"}}}},
    {"$group": {"_id": {"insurer_id": "$insurer_id", "month": "$month"},
                "claims": {"$sum": 1}, "claimed": {"$sum": "$claimed_amount"}, "paid": {"$sum": "$settled_amount"},
                "rejected": {"$sum": {"$cond": [{"$eq": ["$status", "Rejected"]}, 1, 0]}}}},
]


def refresh_materialized(db):
    """Rebuild mv_insurer_monthly. Real MongoDB uses $merge (upsert per key);
    mongomock lacks $merge, so the same rows are written with $out."""
    started = time.perf_counter()
    now = datetime.now()
    if is_mock():
        db[MV_NAME].drop()
        db.claims.aggregate(MV_PIPELINE + [{"$addFields": {"refreshed_at": now}}, {"$out": MV_NAME}])
        stage = "$out (mock fallback)"
    else:
        db.claims.aggregate(MV_PIPELINE + [{"$addFields": {"refreshed_at": now}},
                                           {"$merge": {"into": MV_NAME, "on": "_id",
                                                       "whenMatched": "replace", "whenNotMatched": "insert"}}])
        stage = "$merge"
    build_ms = round((time.perf_counter() - started) * 1000)
    return {"rows": db[MV_NAME].count_documents({}), "build_ms": build_ms, "stage": stage}


def compare_materialized(db, insurer_id="INS02"):
    """Time the same question answered from claims (live) vs the materialised view."""
    t0 = time.perf_counter()
    live = list(db.claims.aggregate([{"$match": {"insurer_id": insurer_id}}] + MV_PIPELINE[:1] + [
        {"$group": {"_id": "$month", "paid": {"$sum": "$settled_amount"}}}, {"$sort": {"_id": 1}}]))
    live_ms = (time.perf_counter() - t0) * 1000
    t1 = time.perf_counter()
    mv = list(db[MV_NAME].find({"_id.insurer_id": insurer_id}, {"paid": 1}).sort("_id.month", 1))
    mv_ms = (time.perf_counter() - t1) * 1000
    return {"live_ms": round(live_ms, 1), "mv_ms": round(mv_ms, 1), "rows": len(mv),
            "same_total": sum(r["paid"] for r in live) == sum(r["paid"] for r in mv)}


# --------------------------------------------------------------------------- #
# 5. Backup and restore
# --------------------------------------------------------------------------- #
def backup(db, folder=None):
    """Logical backup: every collection to Extended JSON plus a manifest."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = folder or os.path.join(BACKUP_DIR, stamp)
    os.makedirs(folder, exist_ok=True)
    manifest = {"created_at": datetime.now().isoformat(timespec="seconds"), "database": db.name, "collections": {}}
    started = time.perf_counter()
    for name in COLLECTIONS + ["counters"]:
        docs = list(db[name].find())
        with open(os.path.join(folder, f"{name}.json"), "w", encoding="utf-8") as f:
            f.write(json_util.dumps(docs, json_options=json_util.CANONICAL_JSON_OPTIONS))
        manifest["collections"][name] = len(docs)
    manifest["seconds"] = round(time.perf_counter() - started, 2)
    with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return {"folder": folder, **manifest}


def list_backups():
    if not os.path.isdir(BACKUP_DIR):
        return []
    out = []
    for name in sorted(os.listdir(BACKUP_DIR), reverse=True):
        path = os.path.join(BACKUP_DIR, name, "manifest.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                m = json.load(f)
            out.append({"name": name, "created_at": m["created_at"], "documents": sum(m["collections"].values())})
    return out


def restore(db, name):
    """Drop every collection and reload it from a backup (validators and indexes re-applied)."""
    from .indexes import ensure_indexes
    from .schema import apply_validators
    folder = os.path.join(BACKUP_DIR, name)
    with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    started = time.perf_counter()
    with write_lock:
        for coll in manifest["collections"]:
            db.drop_collection(coll)
        if not is_mock():
            apply_validators(db)
        for coll, count in manifest["collections"].items():
            with open(os.path.join(folder, f"{coll}.json"), encoding="utf-8") as f:
                docs = json_util.loads(f.read(), json_options=json_util.JSONOptions(tz_aware=False))
            if docs:
                db[coll].insert_many(docs, ordered=False)
        ensure_indexes(db)
    return {"restored": sum(manifest["collections"].values()), "seconds": round(time.perf_counter() - started, 2),
            "from": manifest["created_at"]}
