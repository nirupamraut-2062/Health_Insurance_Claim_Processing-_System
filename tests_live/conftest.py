"""Live tests: run against a real MongoDB replica set (local or Atlas).

    python main.py test-live

They use a separate database (claimsure_test by default) so the demo data in
the main database is never touched.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("CLAIMSURE_DB", "claimsure_test")
ADMIN_PASSWORD = os.environ["CLAIMSURE_ADMIN_PASSWORD"] = "Admin-live-2026"
STAFF_PASSWORD = os.environ["CLAIMSURE_STAFF_PASSWORD"] = "Staff-live-2026"

from claimsure import db as dbmod  # noqa: E402

if dbmod.is_mock():
    pytest.skip("live tests need CLAIMSURE_MONGO_URI (a real MongoDB)", allow_module_level=True)


@pytest.fixture(scope="session")
def db():
    from claimsure.seed import seed
    database = dbmod.get_db()
    assert database.name != "claimsure", "live tests must not run on the demo database"
    seed(database, verbose=False)
    from claimsure import auth
    database.credentials.drop()  # fresh test credentials from the passwords above
    database.sessions.drop()
    auth.ensure_setup(database)
    return database


@pytest.fixture(scope="session")
def manager(db):
    from claimsure.services import get_user
    return get_user(db, "USR003")


@pytest.fixture(scope="session")
def client(db):
    from fastapi.testclient import TestClient
    from web.app import app
    with TestClient(app) as c:
        r = c.post("/login", data={"identifier": "USR023", "password": ADMIN_PASSWORD}, follow_redirects=False)
        assert r.status_code == 303, r.text[:300]
        yield c
