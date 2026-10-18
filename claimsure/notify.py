"""Claim alerts to the insured by email and WhatsApp (transactional outbox).

How a message travels:

1. A claim decision, payment or query writes the claim change, the audit entry
   and one ``notifications`` document per recipient *in the same transaction*
   (the outbox pattern). A message therefore exists if and only if the decision
   committed: a rolled-back settlement never tells anyone they were paid.
2. A background sender claims due messages one at a time with an atomic
   ``find_one_and_update`` (queued -> sending), so two senders can never send the
   same message. It records the provider's message id, or schedules a retry
   with exponential backoff, and gives up after ``MAX_ATTEMPTS``.

Recipients: the data set is synthetic, and member phone numbers may belong to
real people, so the member contact on file is never messaged. A message goes to
(a) the alert contact entered on the claim at intimation (name, WhatsApp number,
email - given with the claimant's consent), and (b) a copy to the recipients the
Head Admin configures (stored in ``settings``). When a channel is not
configured, the message is still written, with status "simulated", so the
outbox can be shown.

Providers (credentials in .env or the hosting platform's environment):
    email     CLAIMSURE_SMTP_USER + CLAIMSURE_SMTP_PASSWORD  (Gmail app password), or
              CLAIMSURE_BREVO_API_KEY + CLAIMSURE_EMAIL_FROM (HTTPS API, for hosts that block SMTP)
    whatsapp  CLAIMSURE_TWILIO_SID + CLAIMSURE_TWILIO_TOKEN [+ CLAIMSURE_WHATSAPP_FROM]
"""
import os
import re
import smtplib
import threading
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import make_msgid
from html import escape

from pymongo import ReturnDocument

from .db import is_mock
from .utils import inr

CHANNELS = ("email", "whatsapp")
STATUSES = ("queued", "sending", "sent", "failed", "simulated")
MAX_ATTEMPTS = 5
STALE_SENDING = timedelta(minutes=5)
SENT_RETENTION_DAYS = 180
EVENTS = {
    "registered": "Claim registered",
    "approved": "Claim approved",
    "partially_approved": "Claim partially approved",
    "rejected": "Claim rejected",
    "settled": "Payment released",
    "query_raised": "Information needed",
    "test": "Test message",
}
DECISION_EVENTS = {"Approved": "approved", "Partially Approved": "partially_approved", "Rejected": "rejected"}

SCHEMA = {
    "bsonType": "object",
    "required": ["event", "channel", "to", "status", "attempts", "text", "created_at"],
    "properties": {
        "claim_number": {"bsonType": ["string", "null"]},
        "event": {"enum": list(EVENTS)},
        "channel": {"enum": list(CHANNELS)},
        "to": {"bsonType": "string", "minLength": 1},
        "status": {"enum": list(STATUSES)},
        "attempts": {"bsonType": "int", "minimum": 0},
        "text": {"bsonType": "string", "minLength": 1},
        "created_at": {"bsonType": "date"},
    },
}
INDEXES = [
    ([("dedupe_key", 1)], {"name": "uq_notification_dedupe", "unique": True}),
    ([("status", 1), ("next_attempt_at", 1)], {"name": "ix_notification_queue"}),
    ([("claim_number", 1), ("created_at", -1)], {"name": "ix_notification_claim"}),
    ([("sent_at", 1)], {"name": "ttl_notification_sent", "expireAfterSeconds": SENT_RETENTION_DAYS * 86400,
                        "partialFilterExpression": {"status": "sent"}}),
]


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
class DeliveryError(Exception):
    def __init__(self, message, permanent=False):
        super().__init__(message)
        self.permanent = permanent


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def _html(subject, text):
    paras = "".join(f"<p style='margin:0 0 10px'>{escape(line)}</p>" for line in text.replace("*", "").split("\n") if line)
    return (f"<div style='font-family:Segoe UI,Arial,sans-serif;max-width:560px;margin:auto;border:1px solid #e3e8f0;"
            f"border-radius:10px;overflow:hidden'><div style='background:#0b1730;color:#fff;padding:14px 20px;"
            f"font-weight:700'>ClaimSure &middot; {escape(subject)}</div><div style='padding:18px 20px;color:#0f172a;"
            f"font-size:14px;line-height:1.5'>{paras}</div><div style='padding:10px 20px;background:#f8fafc;color:#64748b;"
            f"font-size:12px'>Automated message from the ClaimSure claims system (academic project; all data is "
            f"synthetic).</div></div>")


def smtp_blocked():
    """Render's free web services block outbound SMTP (ports 25/465/587) since September 2025."""
    return bool(_env("RENDER")) and _env("CLAIMSURE_SMTP_ALLOWED") != "1"


class SMTPEmail:
    label = "Gmail SMTP"

    def configured(self):
        return bool(_env("CLAIMSURE_SMTP_USER") and _env("CLAIMSURE_SMTP_PASSWORD")) and not smtp_blocked()

    def send(self, to, subject, text):
        user = _env("CLAIMSURE_SMTP_USER")
        msg = EmailMessage()
        msg["From"] = f"ClaimSure Claims <{_env('CLAIMSURE_EMAIL_FROM', user)}>"
        msg["To"], msg["Subject"] = to, subject
        msg["Message-ID"] = make_msgid(domain="claimsure.local")
        msg.set_content(text.replace("*", ""))
        msg.add_alternative(_html(subject, text), subtype="html")
        try:
            with smtplib.SMTP(_env("CLAIMSURE_SMTP_HOST", "smtp.gmail.com"), int(_env("CLAIMSURE_SMTP_PORT", "587")),
                              timeout=10) as smtp:
                smtp.starttls()
                smtp.login(user, _env("CLAIMSURE_SMTP_PASSWORD").replace(" ", ""))
                smtp.send_message(msg)
        except smtplib.SMTPAuthenticationError as exc:
            raise DeliveryError(f"SMTP login refused ({exc.smtp_code}); check the Gmail app password", True)
        except smtplib.SMTPRecipientsRefused as exc:
            raise DeliveryError(f"Recipient refused: {exc}", True)
        except (OSError, smtplib.SMTPException) as exc:
            raise DeliveryError(f"SMTP error: {exc}")
        return msg["Message-ID"]


class BrevoEmail:
    label = "Brevo email API"

    def configured(self):
        return bool(_env("CLAIMSURE_BREVO_API_KEY") and _env("CLAIMSURE_EMAIL_FROM"))

    def send(self, to, subject, text):
        import httpx
        try:
            r = httpx.post("https://api.brevo.com/v3/smtp/email", timeout=20, headers={
                "api-key": _env("CLAIMSURE_BREVO_API_KEY"), "accept": "application/json"}, json={
                "sender": {"name": "ClaimSure Claims", "email": _env("CLAIMSURE_EMAIL_FROM")},
                "to": [{"email": to}], "subject": subject, "textContent": text.replace("*", ""),
                "htmlContent": _html(subject, text)})
        except httpx.HTTPError as exc:
            raise DeliveryError(f"Brevo unreachable: {exc}")
        if r.status_code >= 300:
            raise DeliveryError(f"Brevo {r.status_code}: {r.text[:200]}", 400 <= r.status_code < 500 and r.status_code != 429)
        return r.json().get("messageId", "")


class TwilioWhatsApp:
    label = "Twilio WhatsApp"

    def configured(self):
        return bool(_env("CLAIMSURE_TWILIO_SID") and _env("CLAIMSURE_TWILIO_TOKEN"))

    def send(self, to, subject, text):
        import httpx
        sid = _env("CLAIMSURE_TWILIO_SID")
        try:
            r = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", timeout=20,
                           auth=(sid, _env("CLAIMSURE_TWILIO_TOKEN")),
                           data={"From": "whatsapp:" + _env("CLAIMSURE_WHATSAPP_FROM", "+14155238886"),
                                 "To": "whatsapp:" + to, "Body": text})
        except httpx.HTTPError as exc:
            raise DeliveryError(f"Twilio unreachable: {exc}")
        if r.status_code >= 300:
            try:
                body = r.json()
                code = body.get("code")
                detail = TWILIO_ERRORS.get(code) or f"{code}: {body.get('message')}"
            except ValueError:
                detail = r.text[:200]
            raise DeliveryError(f"Twilio {r.status_code}: {detail}", 400 <= r.status_code < 500 and r.status_code != 429)
        return r.json().get("sid", "")

    def status(self, sid):
        """Delivery status reported by WhatsApp through Twilio: (status, error_code)."""
        import httpx
        account = _env("CLAIMSURE_TWILIO_SID")
        r = httpx.get(f"https://api.twilio.com/2010-04-01/Accounts/{account}/Messages/{sid}.json", timeout=15,
                      auth=(account, _env("CLAIMSURE_TWILIO_TOKEN")))
        r.raise_for_status()
        body = r.json()
        return body.get("status"), body.get("error_code")


NO_SESSION = ("no open WhatsApp session with this number - from that phone, send the sandbox join code "
              "(Twilio console > Messaging > Try it out) to +1 415 523 8886, then try again within 24 hours")
TWILIO_ERRORS = {
    21654: NO_SESSION,  # Twilio wants a template because the number has not messaged the sandbox
    63016: NO_SESSION,
    63015: "this number has not joined the Twilio sandbox - send the join code to +1 415 523 8886 first",
    63003: "the number is not on WhatsApp",
    21211: "invalid phone number",
    21408: "WhatsApp to this country is not enabled on the Twilio account",
}
FINAL_DELIVERY = {"delivered", "read", "undelivered", "failed"}


def provider(channel):
    """The configured provider for a channel, or None."""
    if channel == "email":
        for p in (BrevoEmail(), SMTPEmail()):
            if p.configured():
                return p
        return None
    p = TwilioWhatsApp()
    return p if p.configured() else None


def simulated_only():
    return _env("CLAIMSURE_NOTIFY_MODE").lower() == "simulate"


# --------------------------------------------------------------------------- #
# Settings (copy recipients) - edited by the Head Admin
# --------------------------------------------------------------------------- #
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+[1-9][0-9]{9,14}$")


def get_settings(db):
    doc = db.settings.find_one({"_id": "notifications"}) or {}
    return {ch: {"enabled": doc.get(ch, {}).get("enabled", True), "recipients": doc.get(ch, {}).get("recipients", [])}
            for ch in CHANNELS} | {"updated_by": doc.get("updated_by"), "updated_at": doc.get("updated_at")}


def parse_recipients(channel, raw):
    items = [x.strip() for x in re.split(r"[,\n;]+", raw or "") if x.strip()]
    out = []
    for item in items:
        if channel == "whatsapp":
            item = re.sub(r"[\s\-()]", "", item)
            if re.fullmatch(r"[6-9][0-9]{9}", item):
                item = "+91" + item  # an Indian mobile number typed without the country code
            if not PHONE_RE.fullmatch(item):
                raise ValueError(f"'{item}' is not a WhatsApp number in international format, e.g. +919876543210")
        elif not EMAIL_RE.fullmatch(item):
            raise ValueError(f"'{item}' is not a valid email address")
        if item not in out:
            out.append(item)
    if len(out) > 5:
        raise ValueError("At most 5 recipients per channel")
    return out


def save_settings(db, user, email_enabled, email_raw, whatsapp_enabled, whatsapp_raw):
    doc = {"email": {"enabled": email_enabled, "recipients": parse_recipients("email", email_raw)},
           "whatsapp": {"enabled": whatsapp_enabled, "recipients": parse_recipients("whatsapp", whatsapp_raw)},
           "updated_by": user["user_id"], "updated_at": datetime.now()}
    db.settings.update_one({"_id": "notifications"}, {"$set": doc}, upsert=True)
    return doc


def channel_state(db, settings=None):
    """For each channel: can it send (live), which provider, and why not if it cannot."""
    settings = settings or get_settings(db)
    out = {}
    for ch in CHANNELS:
        p = provider(ch)
        reason = None
        if simulated_only():
            reason = "simulation mode (CLAIMSURE_NOTIFY_MODE=simulate)"
        elif not p and ch == "email" and smtp_blocked() and _env("CLAIMSURE_SMTP_USER"):
            reason = "this host blocks SMTP (Render free plan) - set CLAIMSURE_BREVO_API_KEY and CLAIMSURE_EMAIL_FROM"
        elif not p:
            reason = "provider not configured"
        elif not settings[ch]["enabled"]:
            reason = "switched off by the Head Admin"
        out[ch] = {"live": reason is None, "provider": p.label if p else None, "reason": reason,
                   "recipients": settings[ch]["recipients"], "enabled": settings[ch]["enabled"]}
    return out


def parse_contact(name, whatsapp, email, patient_name):
    """The alert contact typed on a new claim (all optional). Raises ValueError on a bad number/email."""
    whatsapp = parse_recipients("whatsapp", whatsapp)[:1]
    email = parse_recipients("email", email)[:1]
    if not whatsapp and not email:
        return None
    return {"name": (name or "").strip()[:80] or patient_name, "whatsapp": whatsapp[0] if whatsapp else None,
            "email": email[0] if email else None, "consent_at": datetime.now()}


# --------------------------------------------------------------------------- #
# Message text
# --------------------------------------------------------------------------- #
def mask_phone(phone):
    digits = re.sub(r"\D", "", phone or "")
    return f"+{digits[:2]}******{digits[-4:]}" if len(digits) >= 8 else "-"


def mask_email(email):
    if not email or "@" not in email:
        return "-"
    name, domain = email.split("@", 1)
    return f"{name[0]}***@{domain}"


def compose(event, claim):
    """(subject, text) for a claim event. WhatsApp renders *bold*; email strips it."""
    snap = claim.get("snapshot") or {}
    who, number = snap.get("member_name", "the patient"), claim["claim_number"]
    where = snap.get("hospital_name", "the hospital")
    adj = claim.get("adjudication") or {}
    greeting = f"Dear {(claim.get('contact') or {}).get('name') or 'policyholder'},"
    head = f"{greeting}\nUpdate on claim *{number}* for {who} at {where}."
    if event == "registered":
        admitted = claim.get("admission_date")
        lines = [greeting, f"Your claim *{number}* has been registered with ClaimSure.",
                 f"Patient: {who}", f"Hospital: {where}" + (f" (admitted {admitted:%d %b %Y})" if admitted else ""),
                 f"Claim type: {claim.get('claim_type', '-')} - amount claimed *{inr(claim.get('claimed_amount', 0))}*.",
                 "Our team will now verify the documents. You will receive a message at every step: "
                 "approval or rejection, and payment.",
                 f"Please quote {number} in all communication."]
        subject = f"Claim {number} registered"
    elif event in ("approved", "partially_approved"):
        payable, claimed = adj.get("payable_amount", claim.get("approved_amount", 0)), claim.get("claimed_amount", 0)
        top = sorted(adj.get("deductions", []), key=lambda d: -d.get("amount", 0))[:3]
        lines = [head, f"Status: *{'APPROVED' if event == 'approved' else 'PARTIALLY APPROVED'}*",
                 f"Approved amount: *{inr(payable)}* (claimed {inr(claimed)})."]
        if top:
            lines.append("Main deductions: " + "; ".join(f"{d['category']} {inr(d['amount'])}" for d in top) + ".")
        lines.append("Payment will be released within 7 working days. You will get another message when it is paid.")
        subject = f"Claim {number} {'approved' if event == 'approved' else 'partially approved'}"
    elif event == "rejected":
        lines = [head, "Status: *REJECTED*",
                 f"Reason: {adj.get('rejection_reason') or 'not payable under the policy terms'}.",
                 "If you disagree, you can raise a grievance within 30 days with our grievance cell or "
                 "through the Bima Bharosa portal."]
        subject = f"Claim {number} rejected"
    elif event == "settled":
        pay = claim.get("payment") or {}
        payee = "the hospital" if pay.get("payee_type") == "Hospital" else "your bank account"
        lines = [head, "Status: *PAID*",
                 f"{inr(claim.get('settled_amount', 0))} has been paid to {payee} by {pay.get('mode', 'NEFT')}"
                 f" (UTR {pay.get('utr', '-')}).", "Thank you for your patience."]
        subject = f"Claim {number} paid"
    elif event == "query_raised":
        question = ((claim.get("queries") or [{}])[-1] or {}).get("question", "Please contact us")
        lines = [head, "Status: *INFORMATION NEEDED*", f"Our medical team asks: {question}",
                 "Please send this within 15 days so we can continue processing your claim."]
        subject = f"Claim {number}: information needed"
    else:
        raise ValueError(f"Unknown event {event}")
    lines.append("- ClaimSure Claims Team")
    return subject, "\n".join(lines)


# --------------------------------------------------------------------------- #
# Outbox
# --------------------------------------------------------------------------- #
def build(db, event, claim, version, state=None):
    """Outbox documents for a claim event (to be inserted by the caller's transaction).

    Recipients per channel: the claim's alert contact (if one was given), then a
    copy to each Head Admin-configured recipient. The member contact on file is
    synthetic and is only recorded, masked, never messaged.
    """
    member = db.members.find_one({"member_id": claim["member_id"]}, {"contact": 1}) or {}
    on_file = member.get("contact") or {}
    masked = {"email": mask_email(on_file.get("email")), "whatsapp": mask_phone(on_file.get("phone"))}
    contact = claim.get("contact") or {}
    subject, text = compose(event, claim)
    state = state or channel_state(db)
    now = datetime.now()
    docs = []
    for ch in CHANNELS:
        base = {"claim_number": claim["claim_number"], "member_id": claim["member_id"], "event": event,
                "channel": ch, "subject": subject, "text": text, "attempts": 0,
                "created_at": now, "next_attempt_at": now, "sent_at": None, "provider": None,
                "provider_id": None, "last_error": None}
        recipients = []
        if contact.get(ch):
            recipients.append((contact[ch], f"claim contact {contact.get('name', '')}".strip()))
        recipients += [(r, "copy") for r in state[ch]["recipients"] if r != contact.get(ch)]
        if state[ch]["live"] and recipients:
            for to, role in recipients:
                docs.append({**base, "to": to, "status": "queued", "provider": state[ch]["provider"],
                             "intended_for": role,
                             "dedupe_key": f"{claim['claim_number']}:{event}:v{version}:{ch}:{to}"})
        else:
            to = contact.get(ch) or masked[ch]
            docs.append({**base, "to": to, "status": "simulated", "intended_for": "claim contact" if contact.get(ch)
                         else "member on file (masked)", "last_error": state[ch]["reason"] or "no recipient",
                         "dedupe_key": f"{claim['claim_number']}:{event}:v{version}:{ch}:simulated"})
    return docs


def enqueue(tx, db, event, claim, version):
    """Write the outbox documents through ``tx`` (part of the caller's transaction)."""
    docs = build(db, event, claim, version)
    for doc in docs:
        tx.insert_one("notifications", doc)
    return docs


_wake = threading.Event()


def wake():
    _wake.set()


def _claim_next(db, now):
    return db.notifications.find_one_and_update(
        {"status": "queued", "next_attempt_at": {"$lte": now}},
        {"$set": {"status": "sending", "locked_at": now}, "$inc": {"attempts": 1}},
        sort=[("next_attempt_at", 1)], return_document=ReturnDocument.AFTER)


def deliver(db, doc, providers=None):
    """Send one claimed message and record the outcome."""
    p = (providers or {}).get(doc["channel"]) or provider(doc["channel"])
    now = datetime.now()
    try:
        if not p:
            raise DeliveryError(f"{doc['channel']} provider is not configured", True)
        provider_id = p.send(doc["to"], doc.get("subject") or EVENTS[doc["event"]], doc["text"])
    except DeliveryError as exc:
        final = exc.permanent or doc["attempts"] >= MAX_ATTEMPTS
        backoff = timedelta(minutes=2 ** (doc["attempts"] - 1))
        db.notifications.update_one({"_id": doc["_id"]}, {"$set": {
            "status": "failed" if final else "queued", "last_error": str(exc)[:300],
            "next_attempt_at": now + backoff, "locked_at": None}})
        return "failed" if final else "retry"
    db.notifications.update_one({"_id": doc["_id"]}, {"$set": {
        "status": "sent", "sent_at": now, "provider": p.label, "provider_id": str(provider_id)[:120],
        "last_error": None, "locked_at": None}})
    return "sent"


def process_due(db, limit=25, providers=None):
    """Send every due message (each claimed atomically). Returns a count per outcome."""
    now = datetime.now()
    db.notifications.update_many({"status": "sending", "locked_at": {"$lt": now - STALE_SENDING}},
                                 {"$set": {"status": "queued", "locked_at": None}})  # a sender died mid-send
    counts = {"sent": 0, "retry": 0, "failed": 0}
    for _ in range(limit):
        doc = _claim_next(db, datetime.now())
        if not doc:
            break
        counts[deliver(db, doc, providers)] += 1
    return counts


def refresh_delivery(db, limit=20):
    """Ask Twilio what happened to recent WhatsApp messages (delivered, read, undelivered...)."""
    p = provider("whatsapp")
    if not p or not hasattr(p, "status"):
        return 0
    since = datetime.now() - timedelta(hours=6)
    checked = 0
    for doc in db.notifications.find({"channel": "whatsapp", "status": "sent", "sent_at": {"$gte": since},
                                      "delivery_status": {"$nin": list(FINAL_DELIVERY)}}).limit(limit):
        try:
            status, code = p.status(doc["provider_id"])
        except Exception:
            continue
        error = TWILIO_ERRORS.get(code, f"WhatsApp error {code}") if code else None
        db.notifications.update_one({"_id": doc["_id"]}, {"$set": {
            "delivery_status": status, "delivery_error": error, "delivery_checked_at": datetime.now()}})
        checked += 1
    return checked


def resend(db, notification_id):
    from bson import ObjectId
    res = db.notifications.update_one({"_id": ObjectId(notification_id), "status": "failed"}, {"$set": {
        "status": "queued", "attempts": 0, "next_attempt_at": datetime.now(), "last_error": None}})
    wake()
    return res.modified_count


def send_test(db, user):
    """Send a test message straight away to every configured recipient."""
    state = channel_state(db)
    results = []
    for ch in CHANNELS:
        if not state[ch]["live"]:
            results.append((ch, None, f"not sent: {state[ch]['reason']}"))
            continue
        for to in state[ch]["recipients"]:
            now = datetime.now()
            doc = {"claim_number": None, "member_id": None, "event": "test", "channel": ch, "to": to,
                   "subject": "Test message", "intended_for": "-", "status": "sending", "attempts": 1,
                   "text": f"This is a test alert from ClaimSure, sent by {user['name']} at {now:%d %b %Y, %H:%M}. "
                           f"Claim decisions and payments will arrive on this {ch} like this message.",
                   "created_at": now, "next_attempt_at": now, "sent_at": None, "provider": state[ch]["provider"],
                   "provider_id": None, "last_error": None, "locked_at": now,
                   "dedupe_key": f"test:{now.timestamp()}:{ch}:{to}"}
            doc["_id"] = db.notifications.insert_one(doc).inserted_id
            outcome = deliver(db, doc)
            after = db.notifications.find_one({"_id": doc["_id"]}, {"last_error": 1})
            results.append((ch, to, "sent" if outcome == "sent" else f"failed: {after.get('last_error')}"))
    if any(ch == "whatsapp" and r == "sent" for ch, _, r in results):
        time.sleep(4)  # give WhatsApp a moment, then report delivery
        refresh_delivery(db)
        for i, (ch, to, r) in enumerate(results):
            if ch == "whatsapp" and r == "sent":
                d = db.notifications.find_one({"to": to, "event": "test"}, sort=[("created_at", -1)]) or {}
                if d.get("delivery_error"):
                    results[i] = (ch, to, f"accepted by Twilio but not delivered: {d['delivery_error']}")
                elif d.get("delivery_status"):
                    results[i] = (ch, to, f"sent (WhatsApp status: {d['delivery_status']})")
    return results


def summary(db):
    since = datetime.now() - timedelta(days=1)
    by = {(r["_id"]["status"], r["_id"]["channel"]): r["n"] for r in db.notifications.aggregate([
        {"$group": {"_id": {"status": "$status", "channel": "$channel"}, "n": {"$sum": 1}}}])}
    return {"by": by, "total": sum(by.values()),
            "sent_24h": db.notifications.count_documents({"status": "sent", "sent_at": {"$gte": since}}),
            "queued": sum(v for (s, _), v in by.items() if s in ("queued", "sending")),
            "failed": sum(v for (s, _), v in by.items() if s == "failed"),
            "simulated": sum(v for (s, _), v in by.items() if s == "simulated")}


# --------------------------------------------------------------------------- #
# Set-up and the background sender
# --------------------------------------------------------------------------- #
def ensure_setup(db):
    names = set(db.list_collection_names())
    if not is_mock():
        options = {"validator": {"$jsonSchema": SCHEMA}, "validationLevel": "strict", "validationAction": "error"}
        if "notifications" in names:
            db.command("collMod", "notifications", **options)
        else:
            db.create_collection("notifications", **options)
    for keys, opts in INDEXES:
        try:
            db.notifications.create_index(keys, **opts)
        except Exception:  # mongomock lacks some index options
            db.notifications.create_index(keys, **{k: v for k, v in opts.items()
                                                    if k not in ("partialFilterExpression", "expireAfterSeconds")})


_worker = {"thread": None}


def start_worker(get_db, interval=15):
    """Background sender in the web process; enqueue() wakes it immediately."""
    if _env("CLAIMSURE_NOTIFY_WORKER", "1") == "0" or (_worker["thread"] and _worker["thread"].is_alive()):
        return None

    def loop():
        while True:
            try:
                process_due(get_db())
                refresh_delivery(get_db())
            except Exception as exc:  # keep the sender alive; the next pass retries
                print(f"  [!] notification sender: {exc}")
            _wake.wait(interval)
            _wake.clear()

    _worker["thread"] = threading.Thread(target=loop, name="claimsure-notify", daemon=True)
    _worker["thread"].start()
    return _worker["thread"]
