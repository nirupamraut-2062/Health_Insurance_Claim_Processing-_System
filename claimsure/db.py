"""Database connection.

ClaimSure runs in one of two modes:

* **mongodb** - a real MongoDB server (local Community Server or the free
  Atlas M0 tier). Enabled by setting ``CLAIMSURE_MONGO_URI``.
* **mock**    - an in-memory MongoDB (``mongomock``) so the project runs on
  any laptop with no database installed. Data is re-seeded on every start.

Everything else in the code base is identical in both modes.
"""
import os
import threading

DB_NAME = os.environ.get("CLAIMSURE_DB", "claimsure")
MONGO_URI = os.environ.get("CLAIMSURE_MONGO_URI") or os.environ.get("MONGO_URI")
MODE = "mongodb" if MONGO_URI else "mock"

_client = None
_lock = threading.Lock()
# Serialises writes in mock mode (mongomock has no concurrency control).
write_lock = threading.RLock()


def is_mock():
    return MODE == "mock"


def get_client():
    global _client
    with _lock:
        if _client is None:
            if is_mock():
                import mongomock
                _client = mongomock.MongoClient(tz_aware=False)
            else:
                from pymongo import MongoClient
                _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000, appname="ClaimSure")
        return _client


def get_db():
    return get_client()[DB_NAME]


def ensure_seeded(db=None, verbose=False):
    """Seed the database when it is empty (always the case for mock mode)."""
    from .seed import seed
    db = db if db is not None else get_db()
    if "claims" not in db.list_collection_names() or db.claims.estimated_document_count() == 0:
        return seed(db, verbose=verbose)
    return None


def server_info(db=None):
    db = db if db is not None else get_db()
    if is_mock():
        import mongomock
        return {"mode": "mock", "engine": f"mongomock {mongomock.__version__}", "database": DB_NAME,
                "transactions": "emulated", "validation": "application-level", "replica_set": False}
    info = db.client.server_info()
    hello = db.command("hello")
    return {"mode": "mongodb", "engine": f"MongoDB {info.get('version')}", "database": DB_NAME,
            "transactions": "native" if hello.get("setName") else "unavailable (needs replica set)",
            "validation": "$jsonSchema (server)", "replica_set": bool(hello.get("setName"))}
