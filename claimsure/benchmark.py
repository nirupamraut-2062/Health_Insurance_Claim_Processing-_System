"""Scale benchmark: indexes measured on a large collection with real explain().

Builds ``bench_claims`` (100,000 slim claim documents by default), runs a set
of queries with only the default _id index, builds the secondary indexes,
and runs them again. Each query records the winning plan, keys and documents
examined, server execution time and wall-clock time (median of 3 runs).
The cases include ones where an index does NOT help, which is just as
important to understand as the ones where it does.

Needs a real MongoDB server (mongomock has no query planner).
"""
import random
import statistics
import threading
import time
from datetime import datetime, timedelta

from .db import is_mock

COLL = "bench_claims"
STATUSES = (["Settled"] * 78 + ["Rejected"] * 14 + ["Under Review"] * 3 + ["Intimated"] * 2
            + ["Approved"] * 2 + ["Query Raised"])
STATES = ["Maharashtra", "Karnataka", "Tamil Nadu", "Delhi", "Telangana", "West Bengal", "Gujarat", "Uttar Pradesh",
          "Kerala", "Rajasthan", "Madhya Pradesh", "Haryana", "Andhra Pradesh", "Bihar", "Odisha", "Assam", "Chandigarh"]

INDEXES = [
    ([("member_id", 1), ("submitted_at", -1)], "member_submitted"),
    ([("status", 1), ("submitted_at", -1)], "status_submitted"),
    ([("claimed_amount", 1)], "claimed_amount"),
    ([("diagnosis.icd_code", 1)], "diagnosis_icd"),
    ([("hospital_state", 1)], "hospital_state"),
]

state = {"running": False, "phase": "", "error": None}


def build(db, n=100_000, batch=5000, progress=None):
    rng = random.Random(42)
    members = [m["member_id"] for m in db.members.find({}, {"member_id": 1})] or [f"MEM{i:06d}" for i in range(1, 5001)]
    icds = [i["code"] for i in db.icd_codes.find({}, {"code": 1})] or ["A90", "J18.9", "K35.80"]
    insurers = [f"INS{i:02d}" for i in range(1, 11)]
    start = datetime(2023, 10, 1)
    db.drop_collection(COLL)
    t0 = time.perf_counter()
    for offset in range(0, n, batch):
        docs = []
        for i in range(offset, min(offset + batch, n)):
            amount = int(rng.lognormvariate(11.3, 0.7))
            docs.append({
                "claim_number": f"BCH-{i + 1:07d}",
                "member_id": rng.choice(members),
                "insurer_id": rng.choice(insurers),
                "status": rng.choice(STATUSES),
                "submitted_at": start + timedelta(minutes=rng.randint(0, 3 * 365 * 24 * 60)),
                "claimed_amount": amount,
                "hospital_state": rng.choice(STATES),
                "diagnosis": [{"icd_code": c} for c in rng.sample(icds, rng.choice([1, 1, 1, 2, 3]))],
                "fraud_score": min(100, int(rng.expovariate(1 / 12))),
            })
        db[COLL].insert_many(docs, ordered=False)
        if progress:
            progress(f"inserted {min(offset + batch, n):,} / {n:,}")
    return round(time.perf_counter() - t0, 1)


def cases(db):
    sample = db[COLL].find_one({}, {"member_id": 1})
    last_month = datetime(2026, 9, 1)
    return [
        {"key": "member", "title": "One member's claims, newest first", "kind": "Compound (equality + sort)",
         "filter": {"member_id": sample["member_id"]}, "sort": [("submitted_at", -1)], "projection": None},
        {"key": "queue", "title": "Under-review queue, newest first", "kind": "Compound, ESR rule",
         "filter": {"status": "Under Review"}, "sort": [("submitted_at", -1)], "projection": None},
        {"key": "range", "title": "Claims above ₹10 lakh", "kind": "Range on a selective field",
         "filter": {"claimed_amount": {"$gt": 1_000_000}}, "sort": None, "projection": None},
        {"key": "multikey", "title": "Claims with diagnosis A90 (array field)", "kind": "Multikey",
         "filter": {"diagnosis.icd_code": "A90"}, "sort": None, "projection": None},
        {"key": "covered", "title": "Covered query: member's claim dates only", "kind": "Covered (no documents read)",
         "filter": {"member_id": sample["member_id"]}, "sort": None,
         "projection": {"_id": 0, "member_id": 1, "submitted_at": 1}},
        {"key": "prefix", "title": "All claims submitted since Sept 2026", "kind": "Index NOT used: not a prefix",
         "filter": {"submitted_at": {"$gte": last_month}}, "sort": None, "projection": None},
        {"key": "anchored", "title": "State starting with 'Ma' (anchored regex)", "kind": "Anchored regex: index seek",
         "filter": {"hospital_state": {"$regex": "^Ma"}}, "sort": None, "projection": None},
        {"key": "unanchored", "title": "State containing 'desh' (unanchored regex)", "kind": "Unanchored regex: full index scan",
         "filter": {"hospital_state": {"$regex": "desh"}}, "sort": None, "projection": None},
        {"key": "lowsel", "title": "All settled claims (78% of rows)", "kind": "Low selectivity: index barely helps",
         "filter": {"status": "Settled"}, "sort": None, "projection": None},
    ]


def _stages(plan):
    out, node = [], plan.get("queryPlan", plan)
    while node:
        name = node.get("stage")
        if node.get("indexName"):
            name += f"({node['indexName']})"
        out.insert(0, name)
        node = node.get("inputStage") or (node.get("inputStages") or [None])[0]
    return " → ".join(out)


def measure(db, case):
    cmd = {"find": COLL, "filter": case["filter"]}
    if case["sort"]:
        cmd["sort"] = dict(case["sort"])
    if case["projection"]:
        cmd["projection"] = case["projection"]
    ex = db.command("explain", cmd, verbosity="executionStats")
    st = ex["executionStats"]
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        cur = db[COLL].find(case["filter"], case["projection"])
        if case["sort"]:
            cur = cur.sort(case["sort"])
        n = len(list(cur))
        times.append((time.perf_counter() - t0) * 1000)
    return {"plan": _stages(ex["queryPlanner"]["winningPlan"]), "keys": st["totalKeysExamined"],
            "docs": st["totalDocsExamined"], "returned": st["nReturned"], "server_ms": st["executionTimeMillis"],
            "wall_ms": round(statistics.median(times), 1), "rows": n}


def index_sizes(db):
    try:
        stats = db.command("collStats", COLL)
        return {"data_mb": round(stats["size"] / 1048576, 1), "index_mb": round(stats["totalIndexSize"] / 1048576, 1),
                "per_index_kb": {k: round(v / 1024) for k, v in stats.get("indexSizes", {}).items()}}
    except Exception:
        return {}


def run(db, n=100_000, progress=None):
    if is_mock():
        raise RuntimeError("The scale benchmark needs a real MongoDB server: mongomock has no query planner.")
    say = progress or (lambda m: None)
    say("building collection")
    load_s = build(db, n, progress=say)
    tests = cases(db)
    say("measuring without secondary indexes")
    before = {c["key"]: measure(db, c) for c in tests}
    say("building indexes")
    t0 = time.perf_counter()
    for keys, name in INDEXES:
        db[COLL].create_index(keys, name=name)
    index_s = round(time.perf_counter() - t0, 2)
    say("measuring with indexes")
    after = {c["key"]: measure(db, c) for c in tests}
    rows = []
    for c in tests:
        b, a = before[c["key"]], after[c["key"]]
        rows.append({"key": c["key"], "title": c["title"], "kind": c["kind"], "filter": c["filter"],
                     "sort": c["sort"], "projection": c["projection"], "before": b, "after": a,
                     "speedup": round(b["wall_ms"] / a["wall_ms"], 1) if a["wall_ms"] else None})
    doc = {"run_at": datetime.now(), "documents": n, "load_seconds": load_s, "index_build_seconds": index_s,
           "indexes": [name for _, name in INDEXES], "sizes": index_sizes(db), "cases": rows}
    db.bench_results.insert_one(doc)
    say("done")
    return doc


def latest(db):
    return db.bench_results.find_one(sort=[("run_at", -1)])


def run_in_background(db, n):
    if state["running"]:
        return False
    state.update(running=True, phase="starting", error=None)

    def work():
        try:
            run(db, n, progress=lambda m: state.update(phase=m))
        except Exception as exc:
            state["error"] = str(exc)
        finally:
            state["running"] = False
    threading.Thread(target=work, daemon=True).start()
    return True
