"""Append-only audit trail of every change made through the application."""
from datetime import datetime


def log(db, actor, action, entity_type, entity_id, summary, details=None, session=None):
    entry = {
        "timestamp": datetime.now(),
        "actor_id": actor["user_id"],
        "actor_name": actor["name"],
        "actor_role": actor.get("role", "System"),
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "summary": summary,
        "details": details or {},
    }
    if session is not None:
        return session.insert_one("audit_logs", entry)
    return db.audit_logs.insert_one(entry)


SYSTEM_USER = {"user_id": "SYSTEM", "name": "ClaimSure System", "role": "System"}
