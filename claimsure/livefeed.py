"""Live activity feed with change streams.

A change stream is MongoDB's push notification of every insert/update/delete,
read from the replica set's oplog. ClaimSure watches claims, payments,
policies and grievances and pushes each change to the browser as a
Server-Sent Event, so the dashboard updates without polling.

mongomock has no change streams, so in mock mode the feed falls back to
polling the audit log (clearly labelled).
"""
import json
import time
from datetime import datetime

from .db import is_mock
from .utils import inr

WATCHED = ["claims", "payments", "policies", "grievances"]
PIPELINE = [{"$match": {"operationType": {"$in": ["insert", "update", "replace", "delete"]},
                        "ns.coll": {"$in": WATCHED}}}]


def describe(change):
    """Turn a change event into a short, readable line."""
    op = change["operationType"]
    coll = change["ns"]["coll"]
    doc = change.get("fullDocument") or {}
    key_field = {"claims": "claim_number", "payments": "payment_id", "policies": "policy_number",
                 "grievances": "grievance_id"}[coll]
    key = doc.get(key_field) or str(change.get("documentKey", {}).get("_id", ""))
    fields = sorted((change.get("updateDescription") or {}).get("updatedFields", {}).keys())
    fields = [f for f in fields if f not in ("updated_at", "version")]
    if coll == "claims" and op == "update" and "status" in fields:
        text = f"{key} is now {doc.get('status')}"
    elif coll == "claims" and op == "insert":
        text = f"New claim {key} for {(doc.get('snapshot') or {}).get('member_name', '')}"
    elif coll == "payments" and op == "insert":
        text = f"Payment {key}: {inr(doc.get('amount', 0))} to {(doc.get('payee') or {}).get('name', '')}"
    elif coll == "policies" and op == "update":
        text = f"{key} cover now {inr(doc.get('available_cover', 0))}" if "available_cover" in fields \
            else f"{key} updated ({', '.join(fields[:3])})"
    else:
        text = f"{op} on {coll} {key}" + (f" ({', '.join(fields[:3])})" if fields else "")
    ts = change.get("clusterTime")
    when = datetime.fromtimestamp(ts.time) if ts is not None else datetime.now()
    return {"op": op, "collection": coll, "key": key, "text": text, "fields": fields[:6],
            "at": when.strftime("%H:%M:%S"), "source": "change stream"}


def events(db, stop_after=None):
    """Yield readable events forever (or until ``stop_after`` seconds pass)."""
    started = time.time()
    if is_mock():
        last = datetime.now()
        while stop_after is None or time.time() - started < stop_after:
            for a in db.audit_logs.find({"timestamp": {"$gt": last}}).sort("timestamp", 1):
                last = a["timestamp"]
                yield {"op": a["action"], "collection": a["entity_type"], "key": a["entity_id"],
                       "text": a["summary"], "fields": [], "at": a["timestamp"].strftime("%H:%M:%S"),
                       "source": "audit-log polling (mock mode)"}
            yield None  # heartbeat
            time.sleep(1.5)
        return
    with db.watch(PIPELINE, full_document="updateLookup", max_await_time_ms=1000) as stream:
        while stream.alive and (stop_after is None or time.time() - started < stop_after):
            change = stream.try_next()
            yield describe(change) if change else None


def sse(db):
    """Server-Sent Events wire format for FastAPI's StreamingResponse."""
    yield "retry: 3000\n\n"
    for ev in events(db):
        if ev is None:
            yield ": keep-alive\n\n"
        else:
            yield f"data: {json.dumps(ev)}\n\n"
