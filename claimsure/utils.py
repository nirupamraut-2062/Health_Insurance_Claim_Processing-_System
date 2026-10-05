"""Small helpers: money formatting, dates and safe JSON conversion."""
from datetime import datetime, date


def inr(value, decimals=0):
    """Format a number using the Indian digit grouping: 12,34,567."""
    if value is None:
        return "-"
    negative = value < 0
    value = abs(float(value))
    whole = int(value)
    frac = f"{value - whole:.{decimals}f}"[1:] if decimals else ""
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return ("-" if negative else "") + "₹" + s + frac


def inr_short(value):
    """Compact rupee format: 4.2 K, 12.5 L, 3.1 Cr."""
    if value is None:
        return "-"
    v = float(value)
    if abs(v) >= 1e7:
        return f"₹{v / 1e7:.2f} Cr"
    if abs(v) >= 1e5:
        return f"₹{v / 1e5:.2f} L"
    if abs(v) >= 1e3:
        return f"₹{v / 1e3:.1f} K"
    return f"₹{v:.0f}"


def age_on(dob, on=None):
    on = on or datetime.now()
    if dob is None:
        return None
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def months_between(start, end):
    return (end.year - start.year) * 12 + (end.month - start.month) - (1 if end.day < start.day else 0)


def fmt_date(value, with_time=False):
    if not value:
        return "-"
    if isinstance(value, (datetime, date)):
        return value.strftime("%d %b %Y, %H:%M" if with_time and isinstance(value, datetime) else "%d %b %Y")
    return str(value)


def parse_date(value):
    """Parse YYYY-MM-DD (from HTML forms) into a datetime, or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d")


def to_jsonable(obj):
    """Convert Mongo documents (ObjectId, datetime) into plain JSON values."""
    from bson import ObjectId
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj
