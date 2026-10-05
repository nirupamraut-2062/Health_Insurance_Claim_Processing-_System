"""Index management and query-plan benchmarking.

Index definitions live in ``schema/indexes.json``. ``benchmark`` compares the
plan of a query with and without its index:

* real MongoDB - ``explain("executionStats")`` with ``hint({$natural: 1})``
  to force a collection scan for the "before" run;
* mock mode    - mongomock has no query planner, so the plan is *simulated*
  from the index definitions and clearly labelled as such.
"""
import json
import os
import time

from .db import is_mock
from .schema import SCHEMA_DIR

with open(os.path.join(SCHEMA_DIR, "indexes.json"), encoding="utf-8") as _f:
    INDEX_SPECS = json.load(_f)


def ensure_indexes(db):
    results = []
    for coll, specs in INDEX_SPECS.items():
        for spec in specs:
            keys = [tuple(k) for k in spec["keys"]]
            try:
                db[coll].create_index(keys, **spec["options"])
                results.append((coll, spec["options"]["name"], "ok", None))
            except Exception as exc:  # e.g. 2dsphere/text on mongomock
                results.append((coll, spec["options"]["name"], "skipped", str(exc).splitlines()[0][:120]))
    return results


def index_catalog(db):
    """Indexes per collection, merged with the documented purpose."""
    docs = {c: {s["options"]["name"]: s for s in specs} for c, specs in INDEX_SPECS.items()}
    catalog = {}
    for coll in sorted(INDEX_SPECS):
        try:
            info = db[coll].index_information()
        except Exception:
            info = {}
        rows = []
        for name, meta in info.items():
            spec = docs.get(coll, {}).get(name, {})
            opts = spec.get("options", {})
            rows.append({
                "name": name,
                "keys": ", ".join(f"{k}: {v}" for k, v in meta["key"]),
                "kind": spec.get("kind", "Primary key" if name == "_id_" else "Other"),
                "unique": bool(meta.get("unique") or opts.get("unique")),
                "partial": opts.get("partialFilterExpression"),
                "ttl": opts.get("expireAfterSeconds"),
                "purpose": spec.get("purpose", "Default index on _id"),
            })
        catalog[coll] = rows
    return catalog


# --------------------------------------------------------------------------- #
# Benchmarks
# --------------------------------------------------------------------------- #
def benchmark_cases(db):
    """Representative queries, using real values from the data set."""
    claim = db.claims.find_one({"fraud.score": {"$gte": 30}}, {"member_id": 1, "claim_number": 1}) or {}
    member = db.members.find_one({}, {"member_id": 1, "contact.phone": 1}) or {}
    paid = db.payments.find_one({}, {"claim_number": 1}) or {}
    return [
        {"key": "member_history", "title": "Member claim history", "collection": "claims",
         "filter": {"member_id": claim.get("member_id")}, "sort": [("admission_date", -1)],
         "index": "member_admission"},
        {"key": "work_queue", "title": "Under-review work queue (newest first)", "collection": "claims",
         "filter": {"status": "Under Review"}, "sort": [("submitted_at", -1)], "index": "status_submitted"},
        {"key": "diagnosis", "title": "Claims for a diagnosis (multikey)", "collection": "claims",
         "filter": {"diagnosis.icd_code": "A90"}, "sort": None, "index": "diagnosis_icd"},
        {"key": "fraud", "title": "High-risk fraud watch-list (partial index)", "collection": "claims",
         "filter": {"fraud.score": {"$gte": 60}}, "sort": [("fraud.score", -1)], "index": "fraud_watchlist"},
        {"key": "member_policies", "title": "Policies covering a member (multikey)", "collection": "policies",
         "filter": {"insured_members.member_id": member.get("member_id")}, "sort": None, "index": "insured_member"},
        {"key": "phone", "title": "Call-centre lookup by phone", "collection": "members",
         "filter": {"contact.phone": (member.get("contact") or {}).get("phone")}, "sort": None, "index": "phone"},
        {"key": "payment", "title": "Payment for a claim (unique)", "collection": "payments",
         "filter": {"claim_number": paid.get("claim_number")}, "sort": None, "index": "uq_payment_claim"},
    ]


def _plan_stages(plan):
    stages = []
    node = plan.get("queryPlan", plan)
    while node:
        stages.append(node.get("stage"))
        node = node.get("inputStage") or (node.get("inputStages") or [None])[0]
    return [s for s in stages if s]


def _index_name(plan):
    node = plan.get("queryPlan", plan)
    while node:
        if node.get("indexName"):
            return node["indexName"]
        node = node.get("inputStage") or (node.get("inputStages") or [None])[0]
    return None


def _explain(db, case, hint=None):
    cmd = {"find": case["collection"], "filter": case["filter"]}
    if case["sort"]:
        cmd["sort"] = dict(case["sort"])
    if hint:
        cmd["hint"] = hint
    out = db.command("explain", cmd, verbosity="executionStats")
    stats = out["executionStats"]
    winning = out["queryPlanner"]["winningPlan"]
    return {"stages": " → ".join(reversed(_plan_stages(winning))), "index": _index_name(winning),
            "docs_examined": stats["totalDocsExamined"], "keys_examined": stats["totalKeysExamined"],
            "returned": stats["nReturned"], "time_ms": stats["executionTimeMillis"]}


def _simulate(db, case, use_index):
    coll = db[case["collection"]]
    total = coll.estimated_document_count()
    start = time.perf_counter()
    cursor = coll.find(case["filter"])
    if case["sort"]:
        cursor = cursor.sort(case["sort"])
    returned = len(list(cursor))
    elapsed = (time.perf_counter() - start) * 1000
    if use_index:
        return {"stages": "IXSCAN → FETCH", "index": case["index"], "docs_examined": returned,
                "keys_examined": returned, "returned": returned, "time_ms": None}
    stages = "COLLSCAN" + (" → SORT" if case["sort"] else "")
    return {"stages": stages, "index": None, "docs_examined": total, "keys_examined": 0,
            "returned": returned, "time_ms": round(elapsed, 1)}


def benchmark(db):
    rows = []
    for case in benchmark_cases(db):
        if is_mock():
            before, after = _simulate(db, case, False), _simulate(db, case, True)
        else:
            before = _explain(db, case, hint={"$natural": 1})
            after = _explain(db, case)
        saved = before["docs_examined"] - after["docs_examined"]
        rows.append({**case, "before": before, "after": after,
                     "reduction": round(100 * saved / before["docs_examined"], 1) if before["docs_examined"] else 0,
                     "simulated": is_mock()})
    return rows
