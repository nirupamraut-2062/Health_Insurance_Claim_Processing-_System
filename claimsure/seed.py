"""Load the data set from data/*.json into the database."""
import os
import re
import time

from bson import json_util
from bson.json_util import JSONOptions

from .db import is_mock, get_db, DB_NAME
from .indexes import ensure_indexes
from .schema import apply_validators

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# Parents before children so a reader can follow the references top-down.
COLLECTIONS = ["insurers", "tpas", "plans", "icd_codes", "hospitals", "doctors", "users", "agents",
               "members", "policies", "claims", "payments", "grievances", "audit_logs"]

_JSON = JSONOptions(tz_aware=False)

# Sequence counters used to generate new business ids (see services.next_id)
COUNTERS = {
    "claim": ("claims", "claim_number", r"(\d+)$"),
    "payment": ("payments", "payment_id", r"(\d+)$"),
    "grievance": ("grievances", "grievance_id", r"(\d+)$"),
    "member": ("members", "member_id", r"(\d+)$"),
    "policy": ("policies", "policy_number", r"(\d+)$"),
}


def load(name):
    with open(os.path.join(DATA_DIR, f"{name}.json"), encoding="utf-8") as f:
        return json_util.loads(f.read(), json_options=_JSON)


def seed(db=None, verbose=True):
    db = db if db is not None else get_db()
    started = time.perf_counter()
    say = print if verbose else (lambda *a, **k: None)
    say(f"\nSeeding database '{DB_NAME}' ({'mongomock' if is_mock() else 'MongoDB'}) ...")

    for name in COLLECTIONS + ["counters"]:
        db.drop_collection(name)
    if not is_mock():
        apply_validators(db)

    counts = {}
    for name in COLLECTIONS:
        docs = load(name)
        if docs:
            db[name].insert_many(docs, ordered=False)
        counts[name] = len(docs)
        say(f"  [OK] {name:12s} {len(docs):6,d} documents")

    for key, (coll, field, pattern) in COUNTERS.items():
        top = 0
        for doc in db[coll].find({}, {field: 1}):
            top = max(top, int(re.search(pattern, doc[field]).group(1)))
        db.counters.insert_one({"_id": key, "seq": top})

    # Build indexes after the bulk load (much faster than maintaining them per insert)
    results = ensure_indexes(db)
    built = sum(1 for r in results if r[2] == "ok")
    say(f"  [OK] {built} indexes built" + (f", {len(results) - built} skipped (not supported by mongomock)"
                                            if built < len(results) else ""))
    total = sum(counts.values())
    say(f"  Total: {total:,} documents in {len(COLLECTIONS)} collections "
        f"({time.perf_counter() - started:.1f}s)\n")
    return {"counts": counts, "total": total, "indexes": results, "seconds": time.perf_counter() - started}
