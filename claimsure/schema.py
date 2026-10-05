"""$jsonSchema validation.

The validators live in ``schema/validators.json`` - one source of truth used
by MongoDB itself (``collMod`` / ``createCollection``), by the mongosh scripts
and by this module. mongomock ignores validators, so in mock mode the same
rules are enforced here before every insert.
"""
import json
import os
import re
from datetime import datetime

from bson import ObjectId

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema")

with open(os.path.join(SCHEMA_DIR, "validators.json"), encoding="utf-8") as _f:
    VALIDATORS = json.load(_f)


class ValidationError(Exception):
    def __init__(self, collection, errors):
        self.collection = collection
        self.errors = errors
        super().__init__(f"Document failed validation for '{collection}': " + "; ".join(errors))


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "number": _is_number,
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "long": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "double": lambda v: isinstance(v, float),
    "decimal": _is_number,
    "bool": lambda v: isinstance(v, bool),
    "date": lambda v: isinstance(v, datetime),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
    "objectId": lambda v: isinstance(v, ObjectId),
}


def _check(value, rule, path, errors):
    bson_type = rule.get("bsonType")
    if bson_type:
        types = bson_type if isinstance(bson_type, list) else [bson_type]
        if not any(_TYPE_CHECKS[t](value) for t in types):
            errors.append(f"{path}: expected {' or '.join(types)}, got {type(value).__name__}")
            return
    if "enum" in rule and value not in rule["enum"]:
        errors.append(f"{path}: '{value}' is not one of {rule['enum']}")
        return
    if _is_number(value):
        if "minimum" in rule and value < rule["minimum"]:
            errors.append(f"{path}: {value} is below the minimum {rule['minimum']}")
        if "maximum" in rule and value > rule["maximum"]:
            errors.append(f"{path}: {value} is above the maximum {rule['maximum']}")
    if isinstance(value, str):
        if "minLength" in rule and len(value) < rule["minLength"]:
            errors.append(f"{path}: shorter than {rule['minLength']} characters")
        if "pattern" in rule and not re.search(rule["pattern"], value):
            errors.append(f"{path}: '{value}' does not match pattern {rule['pattern']}")
    if isinstance(value, dict):
        for key in rule.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: required field missing" if path else f"{key}: required field missing")
        for key, sub in rule.get("properties", {}).items():
            if key in value:
                _check(value[key], sub, f"{path}.{key}" if path else key, errors)
    if isinstance(value, list):
        if "minItems" in rule and len(value) < rule["minItems"]:
            errors.append(f"{path}: needs at least {rule['minItems']} item(s)")
        if "maxItems" in rule and len(value) > rule["maxItems"]:
            errors.append(f"{path}: allows at most {rule['maxItems']} item(s)")
        if "items" in rule:
            for i, item in enumerate(value):
                _check(item, rule["items"], f"{path}[{i}]", errors)


def validate(collection, document):
    """Return a list of validation errors (empty when the document is valid)."""
    schema = VALIDATORS.get(collection)
    if not schema:
        return []
    errors = []
    _check(document, schema, "", errors)
    return errors


def insert_validated(db, collection, document, session=None):
    """Insert after validating. MongoDB re-checks server-side in real mode."""
    errors = validate(collection, document)
    if errors:
        raise ValidationError(collection, errors)
    if session is not None:
        return db[collection].insert_one(document, session=session)
    return db[collection].insert_one(document)


def apply_validators(db):
    """Create collections with their validators (real MongoDB only)."""
    existing = set(db.list_collection_names())
    applied = []
    for name, schema in VALIDATORS.items():
        options = {"validator": {"$jsonSchema": schema}, "validationLevel": "strict", "validationAction": "error"}
        if name in existing:
            db.command("collMod", name, **options)
        else:
            db.create_collection(name, **options)
        applied.append(name)
    return applied
