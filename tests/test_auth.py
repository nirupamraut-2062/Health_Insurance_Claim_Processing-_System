"""Sign-in, sessions, access levels and the Head Admin's re-verification authority (mock mode)."""
import hashlib
import re

import pytest
from fastapi.testclient import TestClient

from conftest import ADMIN_PASSWORD, STAFF_PASSWORD, sign_in
from claimsure import auth, services
from claimsure.constants import REVERIFICATION, UNDER_REVIEW


@pytest.fixture(scope="module")
def app(db):
    from web.app import app as web_app
    return web_app


@pytest.fixture()
def anon(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_client(app):
    with TestClient(app) as c:
        sign_in(c, "USR001")
        yield c


# --------------------------------------------------------------------------- #
# Sign-in and sessions
# --------------------------------------------------------------------------- #
def test_pages_require_sign_in(anon):
    r = anon.get("/claims?status=open", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    assert anon.get("/api/reports/insurer_performance").status_code == 401
    assert anon.get("/login").status_code == 200
    health = anon.get("/health")
    assert health.status_code == 200 and "hosts" not in health.json()


def test_sign_in_with_email_and_sign_out(anon, db):
    r = anon.post("/login", data={"identifier": "Lakshmi.Iyer@claimsure.example", "password": STAFF_PASSWORD,
                                  "next": "/claims"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/claims"
    assert r.headers["x-frame-options"] == "DENY"
    token = anon.cookies.get(auth.SESSION_COOKIE)
    stored = db.sessions.find_one({"_id": hashlib.sha256(token.encode()).hexdigest()})
    assert stored and stored["user_id"] == "USR003"
    assert db.sessions.find_one({"_id": token}) is None  # only the digest is stored
    assert "Lakshmi Iyer" in anon.get("/").text
    anon.post("/logout")
    assert db.sessions.find_one({"_id": hashlib.sha256(token.encode()).hexdigest()}) is None
    assert anon.get("/", follow_redirects=False).status_code == 303


def test_passwords_are_hashed(db):
    cred = db.credentials.find_one({"user_id": "USR001"})
    assert cred["password_hash"].startswith("scrypt$") and ADMIN_PASSWORD not in cred["password_hash"]
    assert auth.verify_password(ADMIN_PASSWORD, cred["password_hash"])
    assert not auth.verify_password(STAFF_PASSWORD, cred["password_hash"])


def test_open_redirect_is_blocked(anon):
    r = anon.post("/login", data={"identifier": "USR003", "password": STAFF_PASSWORD, "next": "//evil.example"},
                  follow_redirects=False)
    assert r.headers["location"] == "/"


def test_lockout_after_five_wrong_passwords(anon, db, head_admin):
    for _ in range(auth.MAX_FAILED):
        r = anon.post("/login", data={"identifier": "USR012", "password": "wrong-pass-1"})
        assert r.status_code == 401
    r = anon.post("/login", data={"identifier": "USR012", "password": STAFF_PASSWORD})
    assert r.status_code == 401 and "locked" in r.text
    assert db.audit_logs.find_one({"action": "ACCOUNT_LOCKED", "entity_id": "USR012"})
    auth.unlock(db, head_admin, "USR012")
    sign_in(anon, "USR012")


# --------------------------------------------------------------------------- #
# Access levels
# --------------------------------------------------------------------------- #
def test_levels():
    assert auth.level_name({"role": "Admin"}) == "Head Admin"
    assert auth.level_name({"role": "Claims Manager"}) == "Supervisor"
    assert auth.level_name({"role": "Claims Executive"}) == "Staff"
    assert auth.can({"role": "Admin"}, "claims.reopen_closed")
    assert not auth.can({"role": "Claims Manager"}, "claims.reopen_closed")


def test_staff_are_refused_admin_actions(anon, db):
    sign_in(anon, "USR011")  # Claims Executive
    assert anon.get("/admin/staff").status_code == 403
    assert anon.get("/audit").status_code == 403
    before = db.claims.count_documents({})
    r = anon.post("/database/reseed", headers={"referer": "http://testserver/database"})
    assert "does not have permission" in r.text
    assert db.claims.count_documents({}) == before
    r = anon.post("/database/maintenance/migrate")
    assert "does not have permission" in r.text
    assert anon.get("/database").status_code == 200  # reading is still allowed


def test_playground_cannot_read_credentials(admin_client):
    r = admin_client.post("/database/query", data={
        "collection": "users", "operation": "aggregate",
        "query": '[{"$lookup": {"from": "credentials", "localField": "user_id", "foreignField": "user_id", "as": "c"}}]'})
    assert "credentials and sessions are private" in r.text and "scrypt$" not in r.text


# --------------------------------------------------------------------------- #
# Reopening closed cases
# --------------------------------------------------------------------------- #
def test_only_head_admin_reopens_closed_cases(db, manager, executive):
    claim = db.claims.find_one({"status": "Settled"})
    for user in (manager, executive):
        with pytest.raises(services.WorkflowError, match="Head Admin"):
            services.reopen_claim(db, claim["claim_number"], user, "Bills look duplicated, verify again")


def test_settled_case_can_be_flagged_for_recovery(db, head_admin):
    claim = db.claims.find_one({"status": "Settled"})
    number = claim["claim_number"]
    with pytest.raises(services.WorkflowError, match="reason"):
        services.reopen_claim(db, number, head_admin, "short")
    services.reopen_claim(db, number, head_admin, "Hospital watch-listed after payment; audit bills", "USR021")
    reopened = db.claims.find_one({"claim_number": number})
    assert reopened["status"] == REVERIFICATION and reopened["assigned_to"] == "USR021"
    assert services.reverification_outcomes(reopened) == ["uphold", "recovery"]
    executive = services.get_user(db, "USR011")
    with pytest.raises(services.WorkflowError, match="assigned verifier"):
        services.complete_reverification(db, number, executive, "uphold", "Looks fine to me overall")
    with pytest.raises(services.WorkflowError, match="not allowed"):
        services.complete_reverification(db, number, services.get_user(db, "USR021"), "readjudicate",
                                         "Trying to pay it again")
    services.complete_reverification(db, number, services.get_user(db, "USR021"), "recovery",
                                     "Implant billed twice; recover the excess from the hospital")
    done = db.claims.find_one({"claim_number": number})
    assert done["status"] == "Settled" and done["recovery"]["status"] == "Recovery Initiated"
    assert done["reverifications"][-1]["outcome"] == services.OUTCOMES["recovery"]
    assert db.payments.count_documents({"claim_number": number}) == 1  # never paid twice
    assert db.audit_logs.find_one({"action": "CLAIM_REOPENED", "entity_id": number})


def test_rejected_case_can_be_adjudicated_again(db, head_admin):
    claim = db.claims.find_one({"status": "Rejected", "adjudication": {"$ne": None}})
    number = claim["claim_number"]
    services.reopen_claim(db, number, head_admin, "Member submitted the missing discharge summary", "USR006")
    services.complete_reverification(db, number, services.get_user(db, "USR006"), "readjudicate",
                                     "New documents change the picture; decide again")
    again = db.claims.find_one({"claim_number": number})
    assert again["status"] == UNDER_REVIEW and "adjudication" not in again
    assert again["previous_adjudications"][-1]["decision"] == "Rejected"
    assert again["assigned_to"] == "USR006"


def test_open_claims_cannot_be_reopened(db, head_admin):
    claim = db.claims.find_one({"status": "Under Review"})
    with pytest.raises(services.WorkflowError, match="Only closed claims"):
        services.reopen_claim(db, claim["claim_number"], head_admin, "Nothing to reopen here at all")


def test_reopen_through_the_web(admin_client, db):
    claim = db.claims.find_one({"status": "Withdrawn"})
    number = claim["claim_number"]
    page = admin_client.get(f"/claims/{number}").text
    assert "Reopen case for re-verification" in page
    r = admin_client.post(f"/claims/{number}/reopen", data={
        "reason": "Member says the withdrawal was filed by mistake", "assign_to": "USR003",
        "version": claim.get("version") or 0})
    assert r.status_code == 200 and "Re-verification" in r.text
    assert db.claims.find_one({"claim_number": number})["status"] == REVERIFICATION


# --------------------------------------------------------------------------- #
# Staff administration
# --------------------------------------------------------------------------- #
def test_head_admin_creates_staff_with_temporary_password(admin_client, app, db):
    r = admin_client.post("/admin/staff/create", data={"name": "Test Verifier", "email": "test.verifier@claimsure.example",
                                                        "role": "Medical Officer", "region": "South"})
    assert r.status_code == 200
    temp = re.search(r'<span class="secret">([^<]+)</span>', r.text).group(1)
    user = db.users.find_one({"email": "test.verifier@claimsure.example"})
    assert user["approval_limit"] == 200_000
    with TestClient(app) as c:
        r = sign_in(c, user["user_id"], temp)
        assert r.headers["location"] == "/account"
        assert c.get("/", follow_redirects=False).headers["location"] == "/account"  # must change first
        r = c.post("/account/password", data={"current": temp, "new": "Verify-2026", "confirm": "Verify-2026"},
                   follow_redirects=False)
        assert r.headers["location"] == "/"
        assert c.get("/").status_code == 200


def test_deactivation_ends_sessions(admin_client, app, db, head_admin):
    with TestClient(app) as c:
        sign_in(c, "USR014")
        assert c.get("/", follow_redirects=False).status_code == 200
        admin_client.post("/admin/staff/USR014/deactivate")
        assert c.get("/", follow_redirects=False).status_code == 303
        r = c.post("/login", data={"identifier": "USR014", "password": STAFF_PASSWORD})
        assert "deactivated" in r.text
    auth.update_staff(db, head_admin, "USR014", active=True)


def test_last_head_admin_is_protected(db, head_admin):
    with pytest.raises(auth.AuthError, match="at least one active Head Admin"):
        auth.update_staff(db, head_admin, "USR001", role="Claims Manager")
    with pytest.raises(auth.AuthError, match="your own account"):
        auth.update_staff(db, head_admin, "USR001", active=False)


def test_password_policy(db):
    user = services.get_user(db, "USR003")
    for weak in ("short1", "lettersonly", "12345678", "lakshmi.iyer"):
        with pytest.raises(auth.AuthError):
            auth.check_password_policy(weak, user)
    auth.check_password_policy("Claims-2026", user)
