"""ClaimSure web application (FastAPI + Jinja2 + Chart.js).

Run:  python main.py web      (or)   uvicorn web.app:app --reload
"""
import base64
import csv
import io
import json
import math
import os
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bson  # noqa: E402
from bson import json_util  # noqa: E402
from fastapi import FastAPI, Form, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

from claimsure import APP_NAME, APP_TAGLINE, VERSION, analytics, indexes, services  # noqa: E402
from claimsure.constants import (  # noqa: E402
    CLAIM_STATUSES, OPEN_STATUSES, AWAITING_PAYMENT, STATUS_COLORS, CLAIM_TYPES, ADMISSION_TYPES,
    ROOM_CATEGORIES, ROOM_TARIFF, BILL_HEADS, REJECTION_REASONS, MANUAL_REJECTION_CODES, GRIEVANCE_CATEGORIES,
    GRIEVANCE_CHANNELS, GRIEVANCE_STATUSES, POLICY_STATUSES, POLICY_TYPES,
)
from claimsure.db import get_db, ensure_seeded, is_mock, server_info, write_lock  # noqa: E402
from claimsure.schema import VALIDATORS, ValidationError, validate  # noqa: E402
from claimsure.seed import COLLECTIONS, seed  # noqa: E402
from claimsure.services import WorkflowError  # noqa: E402
from claimsure.utils import inr, inr_short, fmt_date, age_on, to_jsonable  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PER_PAGE = 25
DEFAULT_USER = "USR003"


def _warm_cache():
    db = get_db()
    analytics.kpis(db)
    for key in analytics.REPORTS:
        try:
            analytics.REPORTS[key]["fn"](db)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(_app):
    ensure_seeded(get_db(), verbose=True)
    threading.Thread(target=_warm_cache, daemon=True).start()
    yield


app = FastAPI(title=f"{APP_NAME} - {APP_TAGLINE}", version=VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))
env = templates.env
env.filters["inr"] = inr
env.filters["inr_short"] = inr_short
env.filters["date"] = fmt_date
env.filters["datetime"] = lambda v: fmt_date(v, with_time=True)
env.filters["status_color"] = lambda s: STATUS_COLORS.get(s, "gray")
env.filters["num"] = lambda v: f"{v:,}" if isinstance(v, (int, float)) else v
env.filters["pretty"] = lambda v: json_util.dumps(v, indent=2, json_options=json_util.RELAXED_JSON_OPTIONS,
                                                   ensure_ascii=False)
env.globals.update(STATUS_COLORS=STATUS_COLORS, APP_NAME=APP_NAME, APP_TAGLINE=APP_TAGLINE, VERSION=VERSION)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def db():
    return get_db()


def current_user(request):
    uid = request.cookies.get("cs_user", DEFAULT_USER)
    return services.get_user(db(), uid) or services.get_user(db(), DEFAULT_USER)


def _encode(data):
    return base64.urlsafe_b64encode(json.dumps(data, default=str).encode()).decode()


def redirect(url, message=None, kind="success", steps=None):
    resp = RedirectResponse(url, status_code=303)
    if message:
        resp.set_cookie("cs_flash", _encode({"message": message, "kind": kind, "steps": steps}), max_age=60)
    return resp


def render(request, template, active="", **ctx):
    flash = None
    raw = request.cookies.get("cs_flash")
    if raw:
        try:
            flash = json.loads(base64.urlsafe_b64decode(raw.encode()))
        except Exception:
            flash = None
    user = current_user(request)
    ctx.update(request=request, user=user, active=active, flash=flash, mock=is_mock(),
               users=list(db().users.find({"active": True}, {"_id": 0}).sort("user_id", 1)))
    resp = templates.TemplateResponse(request=request, name=template, context=ctx)
    if flash:
        resp.delete_cookie("cs_flash")
    return resp


def paginate(request, total, page):
    pages = max(1, math.ceil(total / PER_PAGE))
    page = min(max(page, 1), pages)
    params = {k: v for k, v in request.query_params.items() if k != "page" and v}

    def url(p):
        return "?" + urlencode({**params, "page": p})
    return {"page": page, "pages": pages, "total": total, "url": url,
            "start": (page - 1) * PER_PAGE + 1 if total else 0, "end": min(page * PER_PAGE, total)}


def _int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d") if value else None
    except ValueError:
        return None


def users_by_id():
    return {u["user_id"]: u for u in db().users.find({}, {"_id": 0})}


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    d = db()
    user = current_user(request)
    k = analytics.kpis(d)
    list_fields = {"_id": 0, "claim_number": 1, "status": 1, "claim_type": 1, "snapshot": 1, "claimed_amount": 1,
                   "approved_amount": 1, "intimation_date": 1, "fraud.risk_level": 1, "fraud.score": 1,
                   "primary_icd": 1, "diagnosis": {"$slice": 1}}
    recent = list(d.claims.find({}, list_fields).sort("intimation_date", -1).limit(8))
    my_queue = list(d.claims.find({"assigned_to": user["user_id"], "status": {"$in": OPEN_STATUSES}}, list_fields)
                    .sort("intimation_date", 1).limit(6))
    alerts = list(d.claims.find({"fraud.risk_level": {"$in": ["High", "Medium"]},
                                 "status": {"$in": OPEN_STATUSES + AWAITING_PAYMENT}}, list_fields)
                  .sort("fraud.score", -1).limit(6))
    awaiting = list(d.claims.find({"status": {"$in": AWAITING_PAYMENT}}, list_fields)
                    .sort("approved_amount", -1).limit(6))
    return render(request, "dashboard.html", "dashboard", k=k, recent=recent, my_queue=my_queue, alerts=alerts,
                  awaiting=awaiting, open_grievances=d.grievances.count_documents({"status": {"$in": ["Open", "In Progress"]}}))


# --------------------------------------------------------------------------- #
# Claims
# --------------------------------------------------------------------------- #
CLAIM_SORTS = {"newest": [("intimation_date", -1)], "oldest": [("intimation_date", 1)],
               "amount": [("claimed_amount", -1)], "risk": [("fraud.score", -1)],
               "admission": [("admission_date", -1)]}


def claim_query(request, user):
    p = request.query_params
    q, notes = {}, []
    status = p.get("status")
    if status == "open":
        q["status"] = {"$in": OPEN_STATUSES}
    elif status == "awaiting":
        q["status"] = {"$in": AWAITING_PAYMENT}
    elif status:
        q["status"] = status
    for field, param in (("claim_type", "type"), ("insurer_id", "insurer"), ("fraud.risk_level", "risk"),
                         ("hospital_id", "hospital"), ("member_id", "member"), ("policy_number", "policy"),
                         ("primary_icd", "icd")):
        if p.get(param):
            q[field] = p[param]
    if p.get("assigned") == "me":
        q["assigned_to"] = user["user_id"]
    dates = {}
    if _date(p.get("from")):
        dates["$gte"] = _date(p["from"])
    if _date(p.get("to")):
        dates["$lte"] = _date(p["to"]) + timedelta(days=1)
    if dates:
        q["admission_date"] = dates
    amounts = {}
    if p.get("min"):
        amounts["$gte"] = _int(p["min"])
    if p.get("max"):
        amounts["$lte"] = _int(p["max"])
    if amounts:
        q["claimed_amount"] = amounts
    search = (p.get("q") or "").strip()
    if search:
        if re.fullmatch(r"(?i)clm-\d{4}-\d{6}", search):
            q["claim_number"] = search.upper()
        elif not is_mock() and re.search(r"[a-zA-Z]{3,}", search):
            q["$text"] = {"$search": search}
            notes.append("full-text index")
        else:
            rx = {"$regex": re.escape(search), "$options": "i"}
            q["$or"] = [{"claim_number": rx}, {"snapshot.member_name": rx}, {"policy_number": rx},
                        {"snapshot.hospital_name": rx}, {"primary_icd": rx}, {"diagnosis.description": rx}]
            notes.append("regex search")
    return q, notes


@app.get("/claims", response_class=HTMLResponse)
def claims_list(request: Request, page: int = 1, sort: str = "newest"):
    d = db()
    user = current_user(request)
    q, notes = claim_query(request, user)
    total = d.claims.count_documents(q)
    pg = paginate(request, total, page)
    fields = {"_id": 0, "claim_number": 1, "status": 1, "claim_type": 1, "admission_type": 1, "snapshot": 1,
              "claimed_amount": 1, "approved_amount": 1, "settled_amount": 1, "intimation_date": 1,
              "admission_date": 1, "fraud.risk_level": 1, "fraud.score": 1, "primary_icd": 1,
              "diagnosis": {"$slice": 1}, "policy_number": 1, "assigned_to": 1}
    rows = list(d.claims.find(q, fields).sort(CLAIM_SORTS.get(sort, CLAIM_SORTS["newest"]))
                .skip((pg["page"] - 1) * PER_PAGE).limit(PER_PAGE))
    insurers = list(d.insurers.find({}, {"_id": 0, "insurer_id": 1, "name": 1}).sort("name", 1))
    export_url = "/claims/export.csv?" + urlencode({k: v for k, v in request.query_params.items() if v})
    return render(request, "claims_list.html", "claims", rows=rows, pg=pg, insurers=insurers, sort=sort,
                  f=request.query_params, statuses=CLAIM_STATUSES, query=q, notes=notes, export_url=export_url,
                  staff=users_by_id())


@app.get("/claims/export.csv")
def claims_export(request: Request):
    q, _ = claim_query(request, current_user(request))
    cursor = db().claims.find(q, {"_id": 0}).sort("intimation_date", -1).limit(5000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Claim No", "Status", "Type", "Patient", "Age", "Hospital", "City", "Insurer", "Diagnosis",
                "Admission", "Discharge", "Claimed", "Approved", "Settled", "Fraud Score"])
    for c in cursor:
        s = c["snapshot"]
        w.writerow([c["claim_number"], c["status"], c["claim_type"], s["member_name"], s["member_age"],
                    s["hospital_name"], s["hospital_city"], s["insurer_name"], c["primary_icd"],
                    fmt_date(c["admission_date"]), fmt_date(c.get("discharge_date")), c["claimed_amount"],
                    c["approved_amount"], c["settled_amount"], (c.get("fraud") or {}).get("score")])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=claimsure_claims.csv"})


def _claim_form_options(d):
    hospitals = list(d.hospitals.find({}, {"_id": 0, "hospital_id": 1, "name": 1, "address.city": 1,
                                           "network.insurer_ids": 1, "city_tier": 1}).sort([("address.city", 1), ("name", 1)]))
    icds = list(d.icd_codes.find({}, {"_id": 0, "code": 1, "description": 1, "specialty": 1, "avg_cost": 1,
                                      "avg_los": 1}).sort("description", 1))
    sample = list(d.policies.aggregate([{"$match": {"status": "Active"}}, {"$sample": {"size": 5}},
                                        {"$project": {"_id": 0, "policy_number": 1}}]))
    return {"hospitals": hospitals, "icds": icds, "room_categories": ROOM_CATEGORIES, "bill_heads": BILL_HEADS,
            "room_tariff": ROOM_TARIFF, "claim_types": CLAIM_TYPES, "admission_types": ADMISSION_TYPES,
            "samples": [s["policy_number"] for s in sample]}


@app.get("/claims/new", response_class=HTMLResponse)
def claim_new(request: Request, policy: str = ""):
    return render(request, "claim_new.html", "new_claim", form={"policy_number": policy}, error=None,
                  **_claim_form_options(db()))


@app.post("/claims/new", response_class=HTMLResponse)
async def claim_create(request: Request):
    form = dict(await request.form())
    user = current_user(request)
    try:
        number = services.intimate_claim(db(), form, user)
    except (WorkflowError, ValidationError, ValueError) as exc:
        return render(request, "claim_new.html", "new_claim", form=form, error=str(exc), **_claim_form_options(db()))
    return redirect(f"/claims/{number}", f"Claim {number} intimated successfully. Move it to 'Under Review' once "
                                         f"documents are verified.")


@app.get("/claims/{claim_number}", response_class=HTMLResponse)
def claim_detail(request: Request, claim_number: str):
    d = db()
    user = current_user(request)
    claim = d.claims.find_one({"claim_number": claim_number})
    if not claim:
        return render(request, "error.html", "claims", message=f"Claim {claim_number} not found")
    policy, plan, icd = services.claim_context(d, claim)
    member = d.members.find_one({"member_id": claim["member_id"]})
    hospital = d.hospitals.find_one({"hospital_id": claim["hospital_id"]})
    doctor = d.doctors.find_one({"doctor_id": claim.get("doctor_id")}) if claim.get("doctor_id") else None
    insurer = d.insurers.find_one({"insurer_id": claim["insurer_id"]})
    tpa = d.tpas.find_one({"tpa_id": claim.get("tpa_id")}) if claim.get("tpa_id") else None
    preview = services.preview_adjudication(d, claim)
    other_claims = list(d.claims.find({"member_id": claim["member_id"], "claim_number": {"$ne": claim_number}},
                                      {"_id": 0, "claim_number": 1, "status": 1, "admission_date": 1,
                                       "claimed_amount": 1, "settled_amount": 1, "primary_icd": 1})
                        .sort("admission_date", -1).limit(10))
    payment = d.payments.find_one({"claim_number": claim_number})
    grievances = list(d.grievances.find({"claim_number": claim_number}).sort("raised_at", -1))
    audit_log = list(d.audit_logs.find({"entity_type": "claim", "entity_id": claim_number}).sort("timestamp", -1).limit(20))
    return render(request, "claim_detail.html", "claims", c=claim, policy=policy, plan=plan, icd=icd, member=member,
                  hospital=hospital, doctor=doctor, insurer=insurer, tpa=tpa, preview=preview,
                  actions=services.available_actions(claim, user), other_claims=other_claims, payment=payment,
                  grievances=grievances, audit_log=audit_log, bill_heads=BILL_HEADS,
                  manual_reasons={k: REJECTION_REASONS[k] for k in MANUAL_REJECTION_CODES},
                  grievance_categories=GRIEVANCE_CATEGORIES, grievance_channels=GRIEVANCE_CHANNELS,
                  staff=users_by_id(), member_age=age_on(member["dob"]) if member else None)


def _claim_action(request, claim_number, fn, success):
    try:
        out = fn(current_user(request))
    except (WorkflowError, ValidationError) as exc:
        return redirect(f"/claims/{claim_number}", str(exc), "error")
    if isinstance(out, dict) and "steps" in out:
        kind = "success" if out["committed"] else "error"
        msg = ("Settlement committed - claim, policy, payment and audit log updated atomically."
               if out["committed"] else f"Transaction rolled back: {out['error']}")
        return redirect(f"/claims/{claim_number}", msg, kind, steps=out["steps"])
    if isinstance(out, dict) and "message" in out:
        return redirect(f"/claims/{claim_number}", out["message"], "warning" if out.get("escalated") else "success")
    return redirect(f"/claims/{claim_number}", success)


@app.post("/claims/{claim_number}/status")
def claim_status(request: Request, claim_number: str, new_status: str = Form(...), remarks: str = Form("")):
    return _claim_action(request, claim_number,
                         lambda u: services.change_status(db(), claim_number, new_status, u, remarks),
                         f"Claim moved to {new_status}")


@app.post("/claims/{claim_number}/adjudicate")
def claim_adjudicate(request: Request, claim_number: str, manual_rejection: str = Form(""), remarks: str = Form("")):
    return _claim_action(request, claim_number,
                         lambda u: services.adjudicate_claim(db(), claim_number, u, manual_rejection or None, remarks),
                         "Decision recorded")


@app.post("/claims/{claim_number}/settle")
def claim_settle(request: Request, claim_number: str, simulate_failure: str = Form("")):
    return _claim_action(request, claim_number,
                         lambda u: services.settle_claim(db(), claim_number, u, simulate_failure == "1"), "")


@app.post("/claims/{claim_number}/fraud-review")
def claim_fraud_review(request: Request, claim_number: str, decision: str = Form(...), notes: str = Form("")):
    return _claim_action(request, claim_number,
                         lambda u: services.review_fraud(db(), claim_number, u, decision, notes),
                         f"Fraud review recorded: {decision}")


@app.post("/claims/{claim_number}/grievance")
def claim_grievance(request: Request, claim_number: str, category: str = Form(...), channel: str = Form(...),
                    description: str = Form(""), priority: str = Form("Medium")):
    return _claim_action(request, claim_number,
                         lambda u: services.raise_grievance(db(), claim_number, u, category, channel, description, priority),
                         "Grievance registered")


# --------------------------------------------------------------------------- #
# Policies, members, hospitals
# --------------------------------------------------------------------------- #
@app.get("/policies", response_class=HTMLResponse)
def policies_list(request: Request, page: int = 1):
    d = db()
    p = request.query_params
    q = {}
    for field, param in (("status", "status"), ("policy_type", "type"), ("insurer_id", "insurer"), ("channel", "channel")):
        if p.get(param):
            q[field] = p[param]
    if p.get("q"):
        rx = {"$regex": re.escape(p["q"].strip()), "$options": "i"}
        q["$or"] = [{"policy_number": rx}, {"insured_members.name": rx}, {"proposer_id": rx}]
    if p.get("renewal") == "due":
        now = datetime.now()
        q.update(status="Active", end_date={"$gte": now, "$lte": now + timedelta(days=45)})
    total = d.policies.count_documents(q)
    pg = paginate(request, total, page)
    rows = list(d.policies.find(q, {"_id": 0, "renewal_history": 0}).sort("first_inception_date", -1)
                .skip((pg["page"] - 1) * PER_PAGE).limit(PER_PAGE))
    insurers = {i["insurer_id"]: i for i in d.insurers.find({}, {"_id": 0})}
    plans = {pl["plan_id"]: pl for pl in d.plans.find({}, {"_id": 0, "plan_id": 1, "name": 1, "category": 1})}
    return render(request, "policies.html", "policies", rows=rows, pg=pg, f=p, insurers=insurers, plans=plans,
                  statuses=POLICY_STATUSES, types=POLICY_TYPES)


@app.get("/policies/{policy_number}", response_class=HTMLResponse)
def policy_detail(request: Request, policy_number: str):
    d = db()
    policy = d.policies.find_one({"policy_number": policy_number})
    if not policy:
        return render(request, "error.html", "policies", message=f"Policy {policy_number} not found")
    plan = d.plans.find_one({"plan_id": policy["plan_id"]})
    members = {m["member_id"]: m for m in d.members.find({"member_id": {"$in": [im["member_id"] for im in policy["insured_members"]]}})}
    claims = list(d.claims.find({"policy_number": policy_number}, {"_id": 0, "claim_number": 1, "status": 1,
                                "snapshot.member_name": 1, "admission_date": 1, "primary_icd": 1, "claimed_amount": 1,
                                "settled_amount": 1, "diagnosis": {"$slice": 1}, "claim_type": 1}).sort("admission_date", -1))
    return render(request, "policy_detail.html", "policies", p=policy, plan=plan, members=members, claims=claims,
                  insurer=d.insurers.find_one({"insurer_id": policy["insurer_id"]}),
                  tpa=d.tpas.find_one({"tpa_id": policy["tpa_id"]}) if policy.get("tpa_id") else None,
                  agent=d.agents.find_one({"agent_id": policy["agent_id"]}) if policy.get("agent_id") else None,
                  age_on=age_on)


@app.get("/members", response_class=HTMLResponse)
def members_list(request: Request, page: int = 1):
    d = db()
    p = request.query_params
    q = {}
    if p.get("q"):
        term = p["q"].strip()
        if re.fullmatch(r"[+\d\- ]{6,}", term):
            digits = re.sub(r"\D", "", term)[-10:]
            q["contact.phone"] = {"$regex": digits + "$"}
        else:
            rx = {"$regex": re.escape(term), "$options": "i"}
            q["$or"] = [{"name": rx}, {"member_id": rx}, {"contact.email": rx}]
    if p.get("city"):
        q["address.city"] = p["city"]
    if p.get("ped"):
        q["pre_existing_conditions"] = p["ped"]
    total = d.members.count_documents(q)
    pg = paginate(request, total, page)
    rows = list(d.members.find(q, {"_id": 0}).sort("member_id", 1).skip((pg["page"] - 1) * PER_PAGE).limit(PER_PAGE))
    cities = sorted(d.members.distinct("address.city"))
    return render(request, "members.html", "members", rows=rows, pg=pg, f=p, cities=cities, age_on=age_on)


@app.get("/members/{member_id}", response_class=HTMLResponse)
def member_detail(request: Request, member_id: str):
    d = db()
    m = d.members.find_one({"member_id": member_id})
    if not m:
        return render(request, "error.html", "members", message=f"Member {member_id} not found")
    policies = list(d.policies.find({"insured_members.member_id": member_id}, {"renewal_history": 0}))
    claims = list(d.claims.find({"member_id": member_id}, {"_id": 0, "claim_number": 1, "status": 1, "admission_date": 1,
                                "snapshot.hospital_name": 1, "diagnosis": {"$slice": 1}, "claimed_amount": 1,
                                "settled_amount": 1, "fraud.risk_level": 1}).sort("admission_date", -1))
    family = list(d.members.find({"family_id": m["family_id"], "member_id": {"$ne": member_id}}, {"_id": 0}))
    bmi = round(m["weight_kg"] / (m["height_cm"] / 100) ** 2, 1) if m.get("height_cm") else None
    return render(request, "member_detail.html", "members", mem=m, policies=policies, claims=claims, family=family,
                  age=age_on(m["dob"]), bmi=bmi, age_on=age_on)


def _distance_km(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


@app.get("/hospitals", response_class=HTMLResponse)
def hospitals_list(request: Request, page: int = 1):
    d = db()
    p = request.query_params
    q = {}
    if p.get("q"):
        q["name"] = {"$regex": re.escape(p["q"].strip()), "$options": "i"}
    for field, param in (("address.state", "state"), ("type", "type"), ("network.insurer_ids", "insurer"),
                         ("accreditation", "accreditation")):
        if p.get(param):
            q[field] = p[param]
    if p.get("watch"):
        q["watchlisted"] = True
    total = d.hospitals.count_documents(q)
    pg = paginate(request, total, page)
    rows = list(d.hospitals.find(q, {"_id": 0}).sort([("address.state", 1), ("name", 1)])
                .skip((pg["page"] - 1) * PER_PAGE).limit(PER_PAGE))
    ids = [h["hospital_id"] for h in rows]
    stats = {r["_id"]: r for r in d.claims.aggregate([
        {"$match": {"hospital_id": {"$in": ids}}},
        {"$group": {"_id": "$hospital_id", "claims": {"$sum": 1}, "paid": {"$sum": "$settled_amount"},
                    "avg_bill": {"$avg": "$claimed_amount"}}}])}
    return render(request, "hospitals.html", "hospitals", rows=rows, pg=pg, f=p, stats=stats,
                  states=sorted(d.hospitals.distinct("address.state")),
                  insurers=list(d.insurers.find({}, {"_id": 0, "insurer_id": 1, "name": 1}).sort("name", 1)))


@app.get("/hospitals/{hospital_id}", response_class=HTMLResponse)
def hospital_detail(request: Request, hospital_id: str):
    d = db()
    h = d.hospitals.find_one({"hospital_id": hospital_id})
    if not h:
        return render(request, "error.html", "hospitals", message=f"Hospital {hospital_id} not found")
    doctors = list(d.doctors.find({"hospital_id": hospital_id}, {"_id": 0}).sort("specialization", 1))
    stats = list(d.claims.aggregate([
        {"$match": {"hospital_id": hospital_id}},
        {"$group": {"_id": None, "claims": {"$sum": 1}, "paid": {"$sum": "$settled_amount"},
                    "claimed": {"$sum": "$claimed_amount"}, "avg_los": {"$avg": "$length_of_stay"},
                    "rejected": {"$sum": {"$cond": [{"$eq": ["$status", "Rejected"]}, 1, 0]}},
                    "flagged": {"$sum": {"$cond": [{"$gte": ["$fraud.score", 30]}, 1, 0]}}}}]))
    claims = list(d.claims.find({"hospital_id": hospital_id}, {"_id": 0, "claim_number": 1, "status": 1,
                                "snapshot.member_name": 1, "admission_date": 1, "diagnosis": {"$slice": 1},
                                "claimed_amount": 1, "fraud.risk_level": 1}).sort("admission_date", -1).limit(15))
    # Nearest other hospitals: $near on the 2dsphere index (MongoDB) or haversine (mock)
    point = h["location"]["coordinates"]
    if is_mock():
        others = list(d.hospitals.find({"hospital_id": {"$ne": hospital_id}},
                                       {"_id": 0, "hospital_id": 1, "name": 1, "address.city": 1, "location": 1, "type": 1}))
        for o in others:
            o["km"] = _distance_km(point, o["location"]["coordinates"])
        nearby = sorted([o for o in others if o["km"] <= 60], key=lambda o: o["km"])[:6]
        geo_mode = "Haversine distance in Python (mock mode)"
    else:
        nearby = list(d.hospitals.find({"hospital_id": {"$ne": hospital_id}, "location": {
            "$near": {"$geometry": h["location"], "$maxDistance": 60000}}},
            {"_id": 0, "hospital_id": 1, "name": 1, "address.city": 1, "location": 1, "type": 1}).limit(6))
        for o in nearby:
            o["km"] = _distance_km(point, o["location"]["coordinates"])
        geo_mode = "$near query on the 2dsphere index"
    insurers = {i["insurer_id"]: i["name"] for i in d.insurers.find({}, {"_id": 0, "insurer_id": 1, "name": 1})}
    return render(request, "hospital_detail.html", "hospitals", h=h, doctors=doctors, stats=(stats or [{}])[0],
                  claims=claims, nearby=nearby, geo_mode=geo_mode, insurers=insurers)


# --------------------------------------------------------------------------- #
# Analytics, fraud, grievances, audit
# --------------------------------------------------------------------------- #
@app.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request):
    reports = [{k: v for k, v in r.items() if k != "fn"} for r in analytics.REPORTS.values()]
    return render(request, "analytics.html", "analytics", reports=reports, k=analytics.kpis(db()))


@app.get("/api/reports/{key}")
def api_report(key: str):
    if key not in analytics.REPORTS:
        return JSONResponse({"error": "unknown report"}, status_code=404)
    out = analytics.run_report(db(), key)
    out["pipelines"] = [json_util.dumps(p, indent=2, json_options=json_util.RELAXED_JSON_OPTIONS) for p in out["pipelines"]]
    return JSONResponse(to_jsonable(out))


@app.get("/api/policies/{policy_number}")
def api_policy(policy_number: str):
    d = db()
    p = d.policies.find_one({"policy_number": policy_number.strip().upper()}, {"_id": 0, "renewal_history": 0})
    if not p:
        return JSONResponse({"error": "Policy not found"}, status_code=404)
    plan = d.plans.find_one({"plan_id": p["plan_id"]}, {"_id": 0})
    insurer = d.insurers.find_one({"insurer_id": p["insurer_id"]}, {"_id": 0, "name": 1})
    return JSONResponse(to_jsonable({
        "policy_number": p["policy_number"], "status": p["status"], "insurer_id": p["insurer_id"],
        "insurer": insurer["name"], "plan": plan["name"], "sum_insured": p["sum_insured"],
        "available_cover": p["available_cover"], "start_date": p["start_date"], "end_date": p["end_date"],
        "room_rent_limit": plan["features"]["room_rent_limit"], "copay": plan["features"]["copay_percent"],
        "members": [{"member_id": m["member_id"], "name": m["name"], "relation": m["relation"],
                     "age": age_on(m["dob"])} for m in p["insured_members"]],
    }))


@app.get("/fraud", response_class=HTMLResponse)
def fraud_page(request: Request, page: int = 1):
    d = db()
    p = request.query_params
    q = {"fraud.risk_level": p.get("risk") or {"$in": ["High", "Medium"]}}
    if p.get("review"):
        q["fraud.review_status"] = p["review"]
    if p.get("open"):
        q["status"] = {"$in": OPEN_STATUSES + AWAITING_PAYMENT}
    if p.get("rule"):
        q["fraud.flags.code"] = p["rule"]
    total = d.claims.count_documents(q)
    pg = paginate(request, total, page)
    rows = list(d.claims.find(q, {"_id": 0, "claim_number": 1, "status": 1, "snapshot": 1, "claimed_amount": 1,
                                  "fraud": 1, "admission_date": 1, "primary_icd": 1})
                .sort("fraud.score", -1).skip((pg["page"] - 1) * PER_PAGE).limit(PER_PAGE))
    flags = analytics.run_report(d, "fraud_flags")
    watch = list(d.hospitals.find({"watchlisted": True}, {"_id": 0}))
    levels = {r["_id"]: r["n"] for r in d.claims.aggregate([{"$group": {"_id": "$fraud.risk_level", "n": {"$sum": 1}}}])}
    return render(request, "fraud.html", "fraud", rows=rows, pg=pg, f=p, flags=flags, watch=watch, levels=levels)


@app.post("/fraud/scan")
def fraud_scan(request: Request):
    counts = services.rescan_fraud(db(), current_user(request))
    return redirect("/fraud", f"Fraud rules re-run on open claims: {counts.get('High', 0)} high, "
                              f"{counts.get('Medium', 0)} medium, {counts.get('Low', 0)} low risk.")


@app.get("/grievances", response_class=HTMLResponse)
def grievances_page(request: Request, page: int = 1):
    d = db()
    p = request.query_params
    q = {}
    for field in ("status", "category", "priority"):
        if p.get(field):
            q[field] = p[field]
    total = d.grievances.count_documents(q)
    pg = paginate(request, total, page)
    rows = list(d.grievances.find(q, {"_id": 0}).sort("raised_at", -1).skip((pg["page"] - 1) * PER_PAGE).limit(PER_PAGE))
    summary = analytics.run_report(d, "grievances")
    return render(request, "grievances.html", "grievances", rows=rows, pg=pg, f=p, summary=summary,
                  categories=GRIEVANCE_CATEGORIES, statuses=GRIEVANCE_STATUSES, staff=users_by_id())


@app.post("/grievances/{grievance_id}/update")
def grievance_update(request: Request, grievance_id: str, status: str = Form(...), resolution: str = Form("")):
    try:
        services.update_grievance(db(), grievance_id, current_user(request), status, resolution)
    except WorkflowError as exc:
        return redirect("/grievances", str(exc), "error")
    return redirect("/grievances", f"{grievance_id} updated to {status}")


@app.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request, page: int = 1):
    d = db()
    p = request.query_params
    q = {}
    for field in ("action", "entity_type", "actor_id"):
        if p.get(field):
            q[field] = p[field]
    if p.get("entity_id"):
        q["entity_id"] = p["entity_id"].strip()
    total = d.audit_logs.count_documents(q)
    pg = paginate(request, total, page)
    rows = list(d.audit_logs.find(q, {"_id": 0}).sort("timestamp", -1).skip((pg["page"] - 1) * PER_PAGE).limit(PER_PAGE))
    return render(request, "audit.html", "audit", rows=rows, pg=pg, f=p, actions=sorted(d.audit_logs.distinct("action")))


# --------------------------------------------------------------------------- #
# Database console
# --------------------------------------------------------------------------- #
def collection_stats(d):
    stats = []
    for name in COLLECTIONS + ["counters"]:
        count = d[name].estimated_document_count()
        if is_mock():
            sample = list(d[name].aggregate([{"$sample": {"size": 25}}])) if count else []
            avg = sum(len(bson.encode(doc)) for doc in sample) / len(sample) if sample else 0
            size = avg * count
            idx = len(d[name].index_information())
        else:
            try:
                cs = next(d[name].aggregate([{"$collStats": {"storageStats": {}}}]))["storageStats"]
            except Exception:
                cs = {}
            avg, size = cs.get("avgObjSize", 0), cs.get("size", 0)
            idx = cs.get("nindexes") or len(d[name].index_information())
        stats.append({"name": name, "count": count, "avg_size": round(avg), "size": size, "indexes": idx,
                      "validator": name in VALIDATORS})
    return stats


RELATIONSHIPS = [
    ("insurers", "plans", "1 : N", "Reference", "plans.insurer_id"),
    ("insurers", "tpas", "N : M", "Reference (array)", "insurers.tpa_ids / tpas.insurer_ids"),
    ("plans", "policies", "1 : N", "Reference", "policies.plan_id"),
    ("members", "policies", "N : M", "Embedded subset + reference", "policies.insured_members[]"),
    ("agents", "policies", "1 : N", "Reference", "policies.agent_id"),
    ("policies", "renewal history", "1 : N", "Embedded array", "policies.renewal_history[]"),
    ("hospitals", "doctors", "1 : N", "Reference", "doctors.hospital_id"),
    ("policies", "claims", "1 : N", "Reference", "claims.policy_number"),
    ("members", "claims", "1 : N", "Reference + extended reference", "claims.member_id + claims.snapshot"),
    ("hospitals", "claims", "1 : N", "Reference + extended reference", "claims.hospital_id + claims.snapshot"),
    ("icd_codes", "claims", "1 : N", "Reference (multikey)", "claims.diagnosis[].icd_code"),
    ("claims", "bill / deductions / history", "1 : N", "Embedded", "claims.bill, adjudication, status_history[]"),
    ("claims", "payments", "1 : 1", "Reference (unique index)", "payments.claim_number"),
    ("claims", "grievances", "1 : N", "Reference", "grievances.claim_number"),
]


@app.get("/database", response_class=HTMLResponse)
def database_page(request: Request):
    d = db()
    return render(request, "database.html", "database", info=server_info(d), stats=collection_stats(d),
                  catalog=indexes.index_catalog(d), validators=VALIDATORS, relationships=RELATIONSHIPS,
                  demo_claim=d.claims.find_one({"status": {"$in": AWAITING_PAYMENT}}, {"claim_number": 1, "approved_amount": 1,
                                                                                    "policy_number": 1}))


@app.get("/database/benchmark", response_class=HTMLResponse)
def database_benchmark(request: Request):
    rows = indexes.benchmark(db())
    return render(request, "benchmark.html", "database", rows=rows)


INVALID_CLAIM = {
    "claim_number": "CLAIM-42", "claim_type": "Cashless", "admission_type": "Planned", "status": "Paid",
    "policy_number": "SGI-HL-2024-000001", "member_id": "MEM000001", "hospital_id": "HSP0001",
    "insurer_id": "INS01", "plan_id": "PLN-SGI-BAS", "primary_icd": "A90",
    "diagnosis": [], "admission_date": "2026-09-01", "bill": {"total": -5000},
    "claimed_amount": -5000, "approved_amount": 0, "settled_amount": 0,
    "status_history": [], "snapshot": {},
}


@app.post("/database/validate", response_class=HTMLResponse)
def database_validate(request: Request):
    d = db()
    doc = dict(INVALID_CLAIM)
    errors = validate("claims", doc)
    server_error = None
    if not is_mock():
        try:
            d.claims.insert_one(dict(doc))
            d.claims.delete_one({"claim_number": doc["claim_number"]})
            server_error = "Inserted - validator not active!"
        except Exception as exc:
            details = getattr(exc, "details", None) or {}
            server_error = json_util.dumps(details.get("errInfo", str(exc)), indent=2)
    return render(request, "validation.html", "database", doc=doc, errors=errors, server_error=server_error,
                  schema=VALIDATORS["claims"])


EXAMPLE_QUERIES = [
    ("High-value cashless claims in Maharashtra", "claims", "find",
     '{"claim_type": "Cashless", "snapshot.hospital_state": "Maharashtra", "claimed_amount": {"$gt": 300000}}',
     '{"claim_number": 1, "snapshot.member_name": 1, "claimed_amount": 1, "status": 1, "_id": 0}', '{"claimed_amount": -1}'),
    ("Members with diabetes over 60 (array + date range)", "members", "find",
     '{"pre_existing_conditions": "Diabetes", "dob": {"$lt": {"$date": "1966-01-01T00:00:00Z"}}}',
     '{"member_id": 1, "name": 1, "dob": 1, "address.city": 1, "_id": 0}', '{"dob": 1}'),
    ("Claims with a room-rent deduction ($elemMatch)", "claims", "find",
     '{"adjudication.deductions": {"$elemMatch": {"category": "Room Rent Excess", "amount": {"$gt": 20000}}}}',
     '{"claim_number": 1, "room_category": 1, "room_rent_per_day": 1, "adjudication.eligible_room_rent": 1, "_id": 0}', '{}'),
    ("Average bill by city tier ($group)", "claims", "aggregate",
     '[{"$group": {"_id": "$snapshot.hospital_tier", "claims": {"$sum": 1}, "avg_bill": {"$avg": "$claimed_amount"}}}, {"$sort": {"_id": 1}}]',
     "", ""),
    ("Family floater policies with 4+ lives ($size / $expr)", "policies", "find",
     '{"policy_type": "Family Floater", "$expr": {"$gte": [{"$size": "$insured_members"}, 4]}}',
     '{"policy_number": 1, "insured_members.name": 1, "sum_insured": 1, "_id": 0}', '{}'),
    ("Network hospitals of an insurer with JCI/NABH", "hospitals", "find",
     '{"network.insurer_ids": "INS04", "accreditation": {"$in": ["JCI", "NABH"]}}',
     '{"name": 1, "address.city": 1, "accreditation": 1, "beds": 1, "_id": 0}', '{"beds": -1}'),
    ("Claims escalated in the status history ($unwind)", "claims", "aggregate",
     '[{"$unwind": "$status_history"}, {"$match": {"status_history.status": "Escalated"}}, {"$project": {"_id": 0, "claim_number": 1, "at": "$status_history.at", "remarks": "$status_history.remarks"}}, {"$sort": {"at": -1}}]',
     "", ""),
]
BLOCKED_STAGES = ("$out", "$merge")


@app.get("/database/query", response_class=HTMLResponse)
@app.post("/database/query", response_class=HTMLResponse)
async def database_query(request: Request):
    form = dict(await request.form()) if request.method == "POST" else {}
    if "example" in request.query_params:
        ex = EXAMPLE_QUERIES[_int(request.query_params["example"]) % len(EXAMPLE_QUERIES)]
        form = {"collection": ex[1], "operation": ex[2], "query": ex[3], "projection": ex[4], "sort": ex[5]}
    result, error, elapsed, count = None, None, None, None
    if form.get("query"):
        d = db()
        coll = form.get("collection")
        if coll not in COLLECTIONS:
            error = "Choose a collection"
        else:
            try:
                parsed = json_util.loads(form["query"], json_options=json_util.JSONOptions(tz_aware=False))
                limit = min(max(_int(form.get("limit"), 20), 1), 100)
                started = time.perf_counter()
                if form.get("operation") == "aggregate":
                    if not isinstance(parsed, list):
                        raise ValueError("An aggregation must be a JSON array of stages")
                    if any(k in stage for stage in parsed for k in BLOCKED_STAGES):
                        raise ValueError("$out / $merge are disabled - the playground is read-only")
                    result = list(d[coll].aggregate(parsed + [{"$limit": limit}]))
                    count = len(result)
                else:
                    projection = json_util.loads(form["projection"]) if form.get("projection", "").strip() else None
                    sort = json_util.loads(form["sort"]) if form.get("sort", "").strip() else {}
                    cursor = d[coll].find(parsed, projection)
                    if sort:
                        cursor = cursor.sort(list(sort.items()))
                    result = list(cursor.limit(limit))
                    count = d[coll].count_documents(parsed)
                elapsed = round((time.perf_counter() - started) * 1000, 1)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
    return render(request, "query.html", "query", form=form, result=result, error=error, elapsed=elapsed,
                  count=count, collections=COLLECTIONS, examples=EXAMPLE_QUERIES)


@app.post("/database/reseed")
def database_reseed(request: Request):
    user = current_user(request)
    if user["role"] != "Admin":
        return redirect("/database", "Only an Admin can reset the database (switch user to Ananya Rao).", "error")
    with write_lock:
        out = seed(db(), verbose=False)
        analytics.clear_cache()
        services.mark_changed()
    return redirect("/database", f"Database reset: {out['total']:,} documents reloaded in {out['seconds']:.1f}s.")


@app.post("/switch-user")
def switch_user(request: Request, user_id: str = Form(...), back: str = Form("/")):
    resp = redirect(back if back.startswith("/") else "/")
    resp.set_cookie("cs_user", user_id, max_age=60 * 60 * 24 * 30)
    return resp


@app.get("/health")
def health():
    return {"status": "ok", **server_info(db())}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
