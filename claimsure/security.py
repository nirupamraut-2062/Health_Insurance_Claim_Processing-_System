"""Database security: users, roles and least privilege.

The application connects as ``claimsure_app`` (read/write). A second,
read-only account (``claimsure_analyst``, CLAIMSURE_ANALYST_URI) is used to
prove that the *database server* - not the application - refuses writes.

Role design (scripts/09_security.js creates these on a self-managed server;
on Atlas the same users are created under Database Access):

    claimsure_app      readWrite on claimsure           the web app / CLI
    claimsure_analyst  read on claimsure                analytics, reports
    claimsure_auditor  find on audit_logs only          compliance (custom role)
    claimsure_dba      dbAdmin + readWrite              schema, indexes, backups
"""
from .db import ANALYST_URI, DB_NAME, is_mock

ROLE_DESIGN = [
    {"user": "claimsure_app", "role": "readWrite on claimsure", "who": "Web app and CLI",
     "can": "Read and write all collections", "cannot": "Create users, drop the database"},
    {"user": "claimsure_analyst", "role": "read on claimsure", "who": "Analysts, report tools",
     "can": "Run find and aggregate, read views", "cannot": "Insert, update, delete or drop anything"},
    {"user": "claimsure_auditor", "role": "custom: find on audit_logs", "who": "Compliance team",
     "can": "Read the audit trail", "cannot": "See claims, members or payments"},
    {"user": "claimsure_dba", "role": "dbAdmin + readWrite", "who": "Database administrator",
     "can": "Validators, indexes, backups", "cannot": "Manage other users"},
]


def whoami(db):
    """Users and roles of the current connection (empty when auth is disabled)."""
    if is_mock():
        return {"mode": "mock", "users": [], "roles": [], "note": "mongomock has no authentication"}
    try:
        st = db.command("connectionStatus")["authInfo"]
        return {"mode": "mongodb", "users": [u["user"] for u in st.get("authenticatedUsers", [])],
                "roles": [f"{r['role']}@{r['db']}" for r in st.get("authenticatedUserRoles", [])]}
    except Exception as exc:
        return {"mode": "mongodb", "users": [], "roles": [], "note": str(exc)}


def _attempt(label, kind, fn):
    try:
        detail = fn()
        return {"action": label, "kind": kind, "allowed": True, "detail": detail, "code": None}
    except Exception as exc:
        code = getattr(exc, "code", None)
        name = (getattr(exc, "details", None) or {}).get("codeName") or type(exc).__name__
        return {"action": label, "kind": kind, "allowed": False, "detail": f"{name}: {str(exc).splitlines()[0][:160]}",
                "code": code}


def analyst_demo(uri=None, db_name=None):
    """Connect as the read-only user and try reads and writes."""
    uri = uri or ANALYST_URI
    if is_mock():
        return {"available": False, "reason": "Needs a real MongoDB server with authentication."}
    if not uri:
        return {"available": False, "reason": "Set CLAIMSURE_ANALYST_URI in .env to a read-only user's connection string."}
    from pymongo import MongoClient
    client = MongoClient(uri, serverSelectionTimeoutMS=6000, appname="ClaimSure-analyst")
    d = client[db_name or DB_NAME]
    try:
        info = d.command("connectionStatus")["authInfo"]
        user = ", ".join(u["user"] for u in info.get("authenticatedUsers", [])) or "(not authenticated)"
        roles = ", ".join(f"{r['role']}@{r['db']}" for r in info.get("authenticatedUserRoles", []))
        rows = [
            _attempt("Count claims", "read", lambda: f"{d.claims.count_documents({}):,} claims"),
            _attempt("Run an aggregation (claims by status)", "read",
                     lambda: f"{len(list(d.claims.aggregate([{'$group': {'_id': '$status'}}])))} groups"),
            _attempt("Read the masked members view", "read",
                     lambda: str(d.members_masked.find_one({}, {"name": 1, "phone": 1}))),
            _attempt("Insert a claim", "write", lambda: str(d.claims.insert_one({"claim_number": "HACK-1"}).inserted_id)),
            _attempt("Change a policy's cover", "write",
                     lambda: f"modified {d.policies.update_one({}, {'$set': {'available_cover': 99999999}}).modified_count}"),
            _attempt("Delete payments", "write", lambda: f"deleted {d.payments.delete_many({}).deleted_count}"),
            _attempt("Drop the audit log", "write", lambda: (d.drop_collection("audit_logs"), "dropped")[1]),
        ]
        return {"available": True, "user": user, "roles": roles, "rows": rows,
                "safe": all(r["allowed"] == (r["kind"] == "read") for r in rows)}
    finally:
        client.close()
