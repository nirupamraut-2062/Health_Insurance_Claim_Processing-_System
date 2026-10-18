"""Email / WhatsApp alerts: transactional outbox, recipients, sender with retries (mock mode, fake providers)."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from conftest import sign_in
from claimsure import notify, services
from claimsure.constants import MANUAL_REJECTION_CODES, REJECTION_REASONS


class FakeProvider:
    label = "Fake"

    def __init__(self, error=None):
        self.sent, self.error = [], error

    def configured(self):
        return True

    def send(self, to, subject, text):
        if self.error:
            raise self.error
        self.sent.append((to, subject, text))
        return f"fake-{len(self.sent)}"


@pytest.fixture()
def live(db, monkeypatch, head_admin):
    """Both channels live with a fake provider and two demo recipients."""
    fake = FakeProvider()
    monkeypatch.setattr(notify, "provider", lambda channel: fake)
    notify.save_settings(db, head_admin, True, "nirupam@example.org", True, "9876543210")
    yield fake
    db.settings.delete_one({"_id": "notifications"})


def _under_review(db, exclude=()):
    return db.claims.find_one({"status": "Under Review", "claimed_amount": {"$lt": 150000},
                               "claim_number": {"$nin": list(exclude)}})


def _decide(db, manager, **kw):
    claim = _under_review(db)
    out = services.adjudicate_claim(db, claim["claim_number"], manager, **kw)
    assert not out["escalated"]
    return db.claims.find_one({"claim_number": claim["claim_number"]})


# --------------------------------------------------------------------------- #
# Message content and recipients
# --------------------------------------------------------------------------- #
def test_recipients_are_validated():
    assert notify.parse_recipients("whatsapp", "98765 43210, +44 7700 900123") == ["+919876543210", "+447700900123"]
    assert notify.parse_recipients("email", "a@b.co; a@b.co") == ["a@b.co"]
    with pytest.raises(ValueError):
        notify.parse_recipients("whatsapp", "12345")
    with pytest.raises(ValueError):
        notify.parse_recipients("email", "not-an-email")


def test_member_contacts_are_masked():
    assert notify.mask_phone("+91-8785153387") == "+91******3387"
    assert notify.mask_email("lata.mishra936@example.com") == "l***@example.com"


def test_rejection_message_gives_the_reason(db, manager):
    code = MANUAL_REJECTION_CODES[0]
    claim = _decide(db, manager, manual_rejection=code)
    subject, text = notify.compose("rejected", claim)
    assert "rejected" in subject and "*REJECTED*" in text
    assert REJECTION_REASONS[code].split(" (")[0][:30] in text and "grievance" in text


# --------------------------------------------------------------------------- #
# Outbox written with the decision
# --------------------------------------------------------------------------- #
def test_without_providers_alerts_are_simulated(db, manager):
    claim = _decide(db, manager)
    alerts = list(db.notifications.find({"claim_number": claim["claim_number"]}))
    assert {a["channel"] for a in alerts} == {"email", "whatsapp"}
    assert all(a["status"] == "simulated" for a in alerts)
    assert all("*" in a["to"] for a in alerts)  # only the masked member contact is recorded
    assert {a["event"] for a in alerts} <= {"approved", "partially_approved"}


def test_live_alerts_go_only_to_demo_recipients(db, manager, live):
    claim = _decide(db, manager)
    alerts = list(db.notifications.find({"claim_number": claim["claim_number"]}))
    assert sorted(a["to"] for a in alerts) == ["+919876543210", "nirupam@example.org"]
    assert all(a["status"] == "queued" and a["intended_for"].count("*") for a in alerts)
    counts = notify.process_due(db)
    assert counts["sent"] >= 2
    sent = list(db.notifications.find({"claim_number": claim["claim_number"]}))
    assert all(a["status"] == "sent" and a["provider_id"].startswith("fake-") for a in sent)
    assert claim["claim_number"] in live.sent[0][2]


def test_failed_settlement_sends_nothing_and_success_queues_payment_alert(db, manager, live):
    claim = db.claims.find_one({"status": "Approved", "claim_number": {"$nin": db.notifications.distinct("claim_number")}})
    number = claim["claim_number"]
    failed = services.settle_claim(db, number, manager, simulate_failure=True)
    assert not failed["committed"] and db.notifications.count_documents({"claim_number": number}) == 0
    ok = services.settle_claim(db, number, manager)
    assert ok["committed"] and any(step[0] == "Queue alerts" for step in ok["steps"])
    paid = list(db.notifications.find({"claim_number": number, "event": "settled"}))
    assert len(paid) == 2 and "UTR" in paid[0]["text"]


def test_query_alert_carries_the_question(db, manager, live):
    claim = _under_review(db)
    services.change_status(db, claim["claim_number"], "Query Raised", manager, "Please send the discharge summary")
    alert = db.notifications.find_one({"claim_number": claim["claim_number"], "event": "query_raised"})
    assert alert and "Please send the discharge summary" in alert["text"]


def test_dedupe_key_is_unique(db, manager, live):
    claim = _decide(db, manager)
    doc = db.notifications.find_one({"claim_number": claim["claim_number"]})
    dup = {k: v for k, v in doc.items() if k != "_id"}
    with pytest.raises(Exception):
        db.notifications.insert_one(dup)


# --------------------------------------------------------------------------- #
# Sender: retries and failures
# --------------------------------------------------------------------------- #
def _queued(db, to="x@example.org"):
    now = datetime.now()
    _id = db.notifications.insert_one({
        "claim_number": "CLM-TEST", "event": "approved", "channel": "email", "to": to, "status": "queued",
        "attempts": 0, "text": "hello", "subject": "s", "created_at": now, "next_attempt_at": now,
        "dedupe_key": f"test-{now.timestamp()}-{to}"}).inserted_id
    return _id


def test_temporary_failures_back_off_then_give_up(db):
    _id = _queued(db)
    flaky = {"email": FakeProvider(notify.DeliveryError("timeout"))}
    for attempt in range(1, notify.MAX_ATTEMPTS + 1):
        db.notifications.update_one({"_id": _id}, {"$set": {"next_attempt_at": datetime.now()}})
        notify.process_due(db, providers=flaky)
        doc = db.notifications.find_one({"_id": _id})
        assert doc["attempts"] == attempt
        if attempt < notify.MAX_ATTEMPTS:
            assert doc["status"] == "queued" and doc["next_attempt_at"] > datetime.now()
    assert doc["status"] == "failed" and doc["last_error"] == "timeout"
    assert notify.resend(db, str(_id)) == 1
    notify.process_due(db, providers={"email": FakeProvider()})
    assert db.notifications.find_one({"_id": _id})["status"] == "sent"


def test_permanent_failure_is_not_retried(db):
    _id = _queued(db, "bad@example.org")
    notify.process_due(db, providers={"email": FakeProvider(notify.DeliveryError("invalid number", permanent=True))})
    doc = db.notifications.find_one({"_id": _id})
    assert doc["status"] == "failed" and doc["attempts"] == 1


def test_stuck_messages_are_requeued(db):
    _id = _queued(db, "stuck@example.org")
    db.notifications.update_one({"_id": _id}, {"$set": {"status": "sending", "locked_at": datetime.now() - timedelta(hours=1)}})
    notify.process_due(db, providers={"email": FakeProvider()})
    assert db.notifications.find_one({"_id": _id})["status"] == "sent"


# --------------------------------------------------------------------------- #
# Web
# --------------------------------------------------------------------------- #
def test_alerts_page_permissions(db):
    from web.app import app
    with TestClient(app) as c:
        sign_in(c, "USR011")  # Claims Executive
        assert c.get("/alerts").status_code == 403
        c.cookies.clear()
        sign_in(c, "USR003")  # Supervisor: may look, may not change
        assert c.get("/alerts").status_code == 200
        r = c.post("/alerts/settings", data={"email": "x@example.org"})
        assert "does not have permission" in r.text
        c.cookies.clear()
        sign_in(c, "USR023")  # Head Admin
        r = c.post("/alerts/settings", data={"email_enabled": "1", "email": "nirupam@example.org",
                                              "whatsapp_enabled": "1", "whatsapp": "12"})
        assert "not a WhatsApp number" in r.text
        r = c.post("/alerts/settings", data={"email_enabled": "1", "email": "nirupam@example.org"})
        assert "Alert settings saved" in r.text and "nirupam@example.org" in r.text
        assert "not sent: provider not configured" in c.post("/alerts/test").text
    db.settings.delete_one({"_id": "notifications"})


def test_claim_page_lists_its_messages(db, manager):
    from web.app import app
    claim = _decide(db, manager)
    with TestClient(app) as c:
        sign_in(c, "USR023")
        page = c.get(f"/claims/{claim['claim_number']}").text
        assert "Messages to the insured" in page and "simulated" in page
