"""Staff sign-in, sessions and access levels.

Authentication data is kept out of the ``users`` collection on purpose:

* ``credentials`` - one document per staff member (1 : 1 with users, unique
  ``user_id``): scrypt password hash, failed-attempt counter, lock-out time and
  last sign-in. A database reset reloads ``users`` from the seed files but never
  touches this collection, so passwords survive it.
* ``sessions``    - one document per signed-in browser. The cookie holds a random
  token and only its SHA-256 digest is stored, so a copy of the collection cannot
  be replayed. A TTL index on ``expires_at`` lets MongoDB delete expired sessions.

Access levels: the Head Admin (role Admin) has every permission, Supervisors
(Claims Managers) can re-open appealed rejections and review fraud, and Staff
work within their role. ``PERMISSIONS`` is the single source of truth used by
the services, the web routes and the templates.
"""
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

from pymongo import ReturnDocument

from . import audit
from .constants import ROLES
from .schema import insert_validated

SESSION_COOKIE = "cs_session"
SESSION_HOURS = 12
MAX_FAILED = 5          # wrong passwords in a row before the account is locked
LOCK_MINUTES = 15
IP_WINDOW_SECONDS = 15 * 60
IP_MAX_FAILURES = 20    # failed sign-ins per IP address per window
MIN_PASSWORD = 8
REGIONS = ["All India", "North", "South", "East", "West"]
DEPARTMENTS = {"Admin": "Operations", "Fraud Analyst": "Special Investigations", "Medical Officer": "Medical"}

ANONYMOUS = {"user_id": "ANONYMOUS", "name": "Unknown (sign-in page)", "role": "Anonymous"}

# --------------------------------------------------------------------------- #
# Access levels and permissions
# --------------------------------------------------------------------------- #
LEVELS = {"Admin": 3, "Claims Manager": 2}  # every other role is level 1
LEVEL_NAMES = {3: "Head Admin", 2: "Supervisor", 1: "Staff"}
ALL_ROLES = list(ROLES)

PERMISSIONS = {
    "claims.reopen_closed": ({"Admin"},
                             "Reopen a closed case (Settled, Rejected or Withdrawn) for re-verification"),
    "claims.reopen_rejected": ({"Admin", "Claims Manager"}, "Re-open a rejected claim after an appeal"),
    "claims.conclude_reverification": ({"Admin", "Claims Manager"},
                                       "Conclude any re-verification (the assigned verifier can conclude their own)"),
    "claims.settle": ({"Admin", "Claims Manager", "Senior Claims Adjuster"},
                      "Release payments (settlement transaction)"),
    "claims.fraud_review": ({"Admin", "Claims Manager", "Fraud Analyst"}, "Record a fraud review"),
    "fraud.scan": ({"Admin", "Claims Manager", "Fraud Analyst"}, "Re-run the fraud rules on open claims"),
    "audit.view": ({"Admin", "Claims Manager", "Fraud Analyst"}, "Read the full audit log"),
    "alerts.view": ({"Admin", "Claims Manager"}, "See the email and WhatsApp alerts sent to the insured"),
    "alerts.manage": ({"Admin"}, "Set alert recipients, send test messages and resend failed alerts"),
    "db.labs": ({"Admin", "Claims Manager"}, "Run the concurrency lab and the analyst-account test"),
    "db.maintenance": ({"Admin"}, "Run maintenance jobs: renewal, migration, views, backup"),
    "db.benchmark": ({"Admin"}, "Run the 100,000-document scale benchmark"),
    "db.reset": ({"Admin"}, "Reset the database or restore a backup"),
    "staff.manage": ({"Admin"}, "Create staff accounts, change roles, reset passwords, deactivate"),
}


class AuthError(Exception):
    """Sign-in failed or an account action was refused."""


class PermissionDenied(Exception):
    """The signed-in user lacks the permission for this action."""


def level(user):
    return LEVELS.get((user or {}).get("role"), 1)


def level_name(user):
    return LEVEL_NAMES[level(user)]


def can(user, permission):
    return bool(user) and user.get("role") in PERMISSIONS[permission][0]


def permissions_of(user):
    return sorted(p for p in PERMISSIONS if can(user, p))


def require(user, permission):
    if not can(user, permission):
        raise PermissionDenied(f"{user.get('name', 'You')} ({user.get('role')}, {level_name(user)}) does not have "
                               f"permission to {PERMISSIONS[permission][1][0].lower()}{PERMISSIONS[permission][1][1:]}.")


def permission_matrix():
    return [{"key": k, "label": label, "roles": {r: r in roles for r in ALL_ROLES}}
            for k, (roles, label) in PERMISSIONS.items()]


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${dk.hex()}"


def verify_password(password, stored):
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt((password or "").encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                            dklen=len(digest) // 2)
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, AttributeError):
        return False


_DUMMY_HASH = hash_password(secrets.token_hex(8))  # equalises timing for unknown accounts


def check_password_policy(password, user=None):
    if len(password or "") < MIN_PASSWORD:
        raise AuthError(f"The password must be at least {MIN_PASSWORD} characters long")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"[0-9]", password):
        raise AuthError("The password must contain both letters and digits")
    if user and password.lower() in (user["email"].lower(), user["user_id"].lower(), user["email"].split("@")[0].lower()):
        raise AuthError("The password cannot be your email or staff ID")


_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz"


def generate_password():
    """Readable random password such as 'Kxqm-7pTa-Wc4r' (letters and digits, no look-alikes)."""
    groups = []
    for _ in range(3):
        chars = [secrets.choice(_ALPHABET) for _ in range(3)] + [secrets.choice("23456789")]
        secrets.SystemRandom().shuffle(chars)
        groups.append("".join(chars))
    return "-".join(groups)


# --------------------------------------------------------------------------- #
# Set-up: indexes and initial credentials
# --------------------------------------------------------------------------- #
def ensure_setup(db):
    """Create the auth indexes and give every staff member without a credential one.

    Initial passwords come from CLAIMSURE_ADMIN_PASSWORD (Head Admin) and
    CLAIMSURE_STAFF_PASSWORD (everyone else). When they are not set a random
    password is generated and returned so the caller can print it once.
    """
    db.credentials.create_index("user_id", unique=True, name="uq_credential_user")
    db.sessions.create_index("expires_at", expireAfterSeconds=0, name="ttl_session_expiry")
    db.sessions.create_index("user_id", name="ix_session_user")
    have = set(db.credentials.distinct("user_id"))
    missing = [u for u in db.users.find({}, {"_id": 0, "user_id": 1, "role": 1}) if u["user_id"] not in have]
    if not missing:
        return {"created": 0, "generated": {}}
    passwords = {"Head Admin": os.environ.get("CLAIMSURE_ADMIN_PASSWORD"),
                 "Staff": os.environ.get("CLAIMSURE_STAFF_PASSWORD")}
    generated = {}
    for key, value in passwords.items():
        if not value:
            passwords[key] = generated[key] = generate_password()
    now = datetime.now()
    hashes = {k: hash_password(v) for k, v in passwords.items()}
    docs = [_credential(u["user_id"], hashes["Head Admin" if u["role"] == "Admin" else "Staff"], now)
            for u in missing]
    db.credentials.insert_many(docs, ordered=False)
    return {"created": len(docs), "generated": {k: v for k, v in generated.items()
                                                if any((u["role"] == "Admin") == (k == "Head Admin") for u in missing)}}


def _credential(user_id, password_hash, now, must_change=False):
    return {"user_id": user_id, "password_hash": password_hash, "must_change": must_change,
            "failed_attempts": 0, "locked_until": None, "password_changed_at": now,
            "last_login_at": None, "last_login_ip": None, "created_at": now}


# --------------------------------------------------------------------------- #
# Sign-in and sessions
# --------------------------------------------------------------------------- #
_ip_failures = defaultdict(deque)
_ip_lock = threading.Lock()


def _ip_blocked(ip, record=False):
    now = time.monotonic()
    with _ip_lock:
        q = _ip_failures[ip]
        while q and now - q[0] > IP_WINDOW_SECONDS:
            q.popleft()
        if record:
            q.append(now)
        return len(q) >= IP_MAX_FAILURES


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def find_user(db, identifier):
    ident = (identifier or "").strip()
    if not ident:
        return None
    return db.users.find_one({"$or": [{"email": ident.lower()}, {"user_id": ident.upper()}]}, {"_id": 0})


def authenticate(db, identifier, password, ip=None, user_agent=""):
    """Check the password and open a session. Returns (token, user, must_change)."""
    ip = ip or "unknown"
    generic = "Incorrect email / staff ID or password."
    if _ip_blocked(ip):
        raise AuthError("Too many failed sign-in attempts from this network. Wait 15 minutes and try again.")
    user = find_user(db, identifier)
    cred = db.credentials.find_one({"user_id": user["user_id"]}) if user else None
    now = datetime.now()
    if cred and cred.get("locked_until") and cred["locked_until"] > now:
        minutes = max(1, round((cred["locked_until"] - now).total_seconds() / 60))
        raise AuthError(f"This account is locked after {MAX_FAILED} wrong passwords. Try again in {minutes} "
                        f"minute(s) or ask the Head Admin to unlock it.")
    if not verify_password(password, cred["password_hash"] if cred else _DUMMY_HASH) or not cred:
        _ip_blocked(ip, record=True)
        if cred:
            after = db.credentials.find_one_and_update({"user_id": user["user_id"]}, {"$inc": {"failed_attempts": 1}},
                                                       return_document=ReturnDocument.AFTER)
            details = {"ip": ip, "failed_attempts": after["failed_attempts"]}
            audit.log(db, ANONYMOUS, "LOGIN_FAILED", "user", user["user_id"],
                      f"Wrong password for {user['name']} ({after['failed_attempts']}/{MAX_FAILED})", details)
            if after["failed_attempts"] >= MAX_FAILED:
                db.credentials.update_one({"user_id": user["user_id"]}, {"$set": {
                    "locked_until": now + timedelta(minutes=LOCK_MINUTES), "failed_attempts": 0}})
                audit.log(db, ANONYMOUS, "ACCOUNT_LOCKED", "user", user["user_id"],
                          f"{user['name']} locked for {LOCK_MINUTES} minutes after {MAX_FAILED} wrong passwords", details)
        raise AuthError(generic)
    if not user.get("active"):
        raise AuthError("This account has been deactivated. Contact the Head Admin.")
    db.credentials.update_one({"user_id": user["user_id"]}, {"$set": {
        "failed_attempts": 0, "locked_until": None, "last_login_at": now, "last_login_ip": ip}})
    token = secrets.token_urlsafe(32)
    db.sessions.insert_one({"_id": _digest(token), "user_id": user["user_id"], "created_at": now,
                            "expires_at": now + timedelta(hours=SESSION_HOURS), "ip": ip,
                            "user_agent": (user_agent or "")[:200]})
    audit.log(db, user, "LOGIN", "user", user["user_id"], f"{user['name']} signed in ({level_name(user)})", {"ip": ip})
    return token, user, bool(cred.get("must_change"))


SESSION_PIPELINE = [
    {"$lookup": {"from": "users", "localField": "user_id", "foreignField": "user_id", "as": "user"}},
    {"$lookup": {"from": "credentials", "localField": "user_id", "foreignField": "user_id", "as": "cred"}},
    {"$project": {"_id": 0, "expires_at": 1, "user": {"$arrayElemAt": ["$user", 0]},
                  "must_change": {"$arrayElemAt": ["$cred.must_change", 0]}}},
]


def session_user(db, token):
    """Resolve a session cookie to the signed-in user in one round trip ($match + 2 x $lookup)."""
    if not token:
        return None
    rows = list(db.sessions.aggregate([{"$match": {"_id": _digest(token), "expires_at": {"$gt": datetime.now()}}}]
                                      + SESSION_PIPELINE))
    if not rows or not rows[0].get("user") or not rows[0]["user"].get("active"):
        return None
    user = rows[0]["user"]
    user.pop("_id", None)
    user["must_change_password"] = bool(rows[0].get("must_change"))
    user["session_expires_at"] = rows[0]["expires_at"]
    return user


def logout(db, token, user=None):
    if token:
        db.sessions.delete_one({"_id": _digest(token)})
    if user:
        audit.log(db, user, "LOGOUT", "user", user["user_id"], f"{user['name']} signed out")


def change_password(db, user, current, new, token=None):
    cred = db.credentials.find_one({"user_id": user["user_id"]})
    if not cred or not verify_password(current, cred["password_hash"]):
        raise AuthError("Your current password is not correct")
    if current == new:
        raise AuthError("Choose a password different from the current one")
    check_password_policy(new, user)
    db.credentials.update_one({"user_id": user["user_id"]}, {"$set": {
        "password_hash": hash_password(new), "must_change": False, "password_changed_at": datetime.now()}})
    keep = {"_id": {"$ne": _digest(token)}} if token else {}
    ended = db.sessions.delete_many({"user_id": user["user_id"], **keep}).deleted_count
    audit.log(db, user, "PASSWORD_CHANGED", "user", user["user_id"], f"{user['name']} changed their password",
              {"other_sessions_ended": ended})
    return ended


def my_sessions(db, user_id):
    return list(db.sessions.find({"user_id": user_id, "expires_at": {"$gt": datetime.now()}},
                                 {"_id": 0, "created_at": 1, "expires_at": 1, "ip": 1, "user_agent": 1})
                .sort("created_at", -1))


# --------------------------------------------------------------------------- #
# Head Admin: staff management
# --------------------------------------------------------------------------- #
def staff_overview(db):
    """Staff with their credential state and live session count ($lookup with a sub-pipeline)."""
    now = datetime.now()
    rows = list(db.users.aggregate([
        {"$lookup": {"from": "credentials", "localField": "user_id", "foreignField": "user_id", "as": "cred"}},
        {"$sort": {"user_id": 1}},
        {"$project": {"_id": 0, "user_id": 1, "name": 1, "email": 1, "role": 1, "region": 1, "department": 1,
                      "approval_limit": 1, "active": 1, "cred": {"$arrayElemAt": ["$cred", 0]}}},
    ]))
    sessions = {r["_id"]: r["n"] for r in db.sessions.aggregate([
        {"$match": {"expires_at": {"$gt": now}}}, {"$group": {"_id": "$user_id", "n": {"$sum": 1}}}])}
    for r in rows:
        cred = r.pop("cred", None) or {}
        r.update(level=level_name(r), has_password=bool(cred), must_change=cred.get("must_change", False),
                 locked=bool(cred.get("locked_until") and cred["locked_until"] > now),
                 failed_attempts=cred.get("failed_attempts", 0), last_login_at=cred.get("last_login_at"),
                 sessions=sessions.get(r["user_id"], 0))
    return rows


def _target(db, user_id):
    target = db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not target:
        raise AuthError(f"Staff member {user_id} not found")
    return target


def _issue_temporary(db, user_id, now):
    password = generate_password()
    db.credentials.update_one({"user_id": user_id}, {"$set": {
        "password_hash": hash_password(password), "must_change": True, "failed_attempts": 0, "locked_until": None,
        "password_changed_at": now}, "$setOnInsert": {"created_at": now, "last_login_at": None,
                                                      "last_login_ip": None}}, upsert=True)
    db.sessions.delete_many({"user_id": user_id})
    return password


def create_staff(db, admin, name, email, role, region):
    require(admin, "staff.manage")
    name, email = (name or "").strip(), (email or "").strip().lower()
    if len(name) < 3:
        raise AuthError("Enter the staff member's full name")
    if role not in ROLES:
        raise AuthError("Choose a role")
    if region not in REGIONS:
        raise AuthError("Choose a region")
    if db.users.find_one({"email": email}):
        raise AuthError(f"{email} already has an account")
    numbers = [int(u["user_id"][3:]) for u in db.users.find({}, {"user_id": 1})]
    user_id = f"USR{max(numbers, default=0) + 1:03d}"
    now = datetime.now()
    user = {"user_id": user_id, "name": name, "email": email, "role": role,
            "department": DEPARTMENTS.get(role, "Claims"), "region": region,
            "approval_limit": ROLES[role]["approval_limit"], "active": True,
            "joined_on": datetime(now.year, now.month, now.day)}
    insert_validated(db, "users", user)  # $jsonSchema checks the e-mail format and the role
    password = _issue_temporary(db, user_id, now)
    audit.log(db, admin, "STAFF_CREATED", "user", user_id, f"{name} added as {role} ({level_name(user)})",
              {"email": email, "region": region})
    user.pop("_id", None)
    return user, password


def _admins_left(db, excluding):
    return db.users.count_documents({"role": "Admin", "active": True, "user_id": {"$ne": excluding}})


def update_staff(db, admin, user_id, role=None, region=None, active=None):
    require(admin, "staff.manage")
    target = _target(db, user_id)
    sets, changes = {}, {}
    if role and role != target["role"]:
        if role not in ROLES:
            raise AuthError("Unknown role")
        if target["role"] == "Admin" and not _admins_left(db, user_id):
            raise AuthError("There must always be at least one active Head Admin")
        sets.update(role=role, approval_limit=ROLES[role]["approval_limit"], department=DEPARTMENTS.get(role, "Claims"))
        changes["role"] = [target["role"], role]
    if region and region != target.get("region"):
        if region not in REGIONS:
            raise AuthError("Unknown region")
        sets["region"] = region
        changes["region"] = [target.get("region"), region]
    if active is not None and active != target["active"]:
        if not active and user_id == admin["user_id"]:
            raise AuthError("You cannot deactivate your own account")
        if not active and target["role"] == "Admin" and not _admins_left(db, user_id):
            raise AuthError("There must always be at least one active Head Admin")
        sets["active"] = active
        changes["active"] = [target["active"], active]
    if not sets:
        return target, "No changes"
    db.users.update_one({"user_id": user_id}, {"$set": sets})
    if sets.get("active") is False or "role" in sets:
        db.sessions.delete_many({"user_id": user_id})  # new rights apply from the next sign-in
    summary = "; ".join(f"{k}: {a} → {b}" for k, (a, b) in changes.items())
    audit.log(db, admin, "STAFF_UPDATED", "user", user_id, f"{target['name']}: {summary}", changes)
    return {**target, **sets}, summary


def reset_password(db, admin, user_id):
    require(admin, "staff.manage")
    target = _target(db, user_id)
    password = _issue_temporary(db, user_id, datetime.now())
    audit.log(db, admin, "PASSWORD_RESET", "user", user_id,
              f"Temporary password issued to {target['name']}; they must change it at sign-in")
    return target, password


def unlock(db, admin, user_id):
    require(admin, "staff.manage")
    target = _target(db, user_id)
    db.credentials.update_one({"user_id": user_id}, {"$set": {"locked_until": None, "failed_attempts": 0}})
    audit.log(db, admin, "ACCOUNT_UNLOCKED", "user", user_id, f"{target['name']} unlocked")
    return target


def revoke_sessions(db, admin, user_id):
    require(admin, "staff.manage")
    target = _target(db, user_id)
    n = db.sessions.delete_many({"user_id": user_id}).deleted_count
    audit.log(db, admin, "SESSIONS_REVOKED", "user", user_id, f"{target['name']} signed out of {n} session(s)")
    return target, n
