import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop("CLAIMSURE_MONGO_URI", None)  # tests always run on mongomock


@pytest.fixture(scope="session")
def db():
    from claimsure.db import get_db
    from claimsure.seed import seed
    database = get_db()
    seed(database, verbose=False)
    return database


@pytest.fixture(scope="session")
def manager(db):
    from claimsure.services import get_user
    return get_user(db, "USR003")  # Claims Manager, limit 10 lakh


@pytest.fixture(scope="session")
def executive(db):
    from claimsure.services import get_user
    return get_user(db, "USR011")  # Claims Executive, limit 75,000
