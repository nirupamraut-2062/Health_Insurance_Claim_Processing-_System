import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["CLAIMSURE_MODE"] = "mock"  # tests always run on mongomock, even with a .env present
# Known initial passwords for the test run (they override any values in .env)
ADMIN_PASSWORD = os.environ["CLAIMSURE_ADMIN_PASSWORD"] = "Admin-test-2026"
STAFF_PASSWORD = os.environ["CLAIMSURE_STAFF_PASSWORD"] = "Staff-test-2026"
HEAD_ADMINS = ("USR023", "USR024")  # Nirupam Raut, Bhudev Singh (USR001 is deactivated)
# Never send real email / WhatsApp from tests: blank provider settings win over .env,
# and the background sender stays off (tests drive it with fake providers).
for _key in ("CLAIMSURE_SMTP_USER", "CLAIMSURE_SMTP_PASSWORD", "CLAIMSURE_BREVO_API_KEY", "CLAIMSURE_TWILIO_SID",
             "CLAIMSURE_TWILIO_TOKEN"):
    os.environ[_key] = ""
os.environ["CLAIMSURE_NOTIFY_WORKER"] = "0"


def sign_in(client, user_id, password=None):
    """Sign a TestClient in; the session cookie stays on the client."""
    admin = user_id in HEAD_ADMINS or user_id.split("@")[0] in ("nirupam.raut", "bhudev.singh")
    password = password or (ADMIN_PASSWORD if admin else STAFF_PASSWORD)
    r = client.post("/login", data={"identifier": user_id, "password": password}, follow_redirects=False)
    assert r.status_code == 303, r.text[:300]
    return r


@pytest.fixture(scope="session")
def db():
    from claimsure.db import get_db
    from claimsure.seed import seed
    from claimsure import auth
    database = get_db()
    seed(database, verbose=False)
    auth.ensure_setup(database)
    return database


@pytest.fixture(scope="session")
def manager(db):
    from claimsure.services import get_user
    return get_user(db, "USR003")  # Claims Manager, limit 10 lakh


@pytest.fixture(scope="session")
def executive(db):
    from claimsure.services import get_user
    return get_user(db, "USR011")  # Claims Executive, limit 75,000


@pytest.fixture(scope="session")
def head_admin(db):
    from claimsure.services import get_user
    return get_user(db, "USR023")  # Nirupam Raut, Head Admin
