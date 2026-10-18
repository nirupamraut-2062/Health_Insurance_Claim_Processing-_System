"""Concurrency-control lab.

Two adjusters pay two claims against the SAME policy at the same instant.
Starting cover is Rs 5,00,000 and each payment is Rs 1,00,000, so the correct
final cover is Rs 3,00,000. Four strategies are run with real threads that are
lined up with a barrier so their reads and writes genuinely interleave:

A. Read-modify-write ($set)      -> lost update (one payment vanishes)
B. Atomic $inc with a guard      -> correct; the server serialises the updates
C. Optimistic locking (version)  -> correct; the loser detects the conflict and retries
D. Multi-document transaction    -> correct; MongoDB raises a WriteConflict and
                                    with_transaction() retries the loser

The lab uses its own sandbox collections (lab_cover, lab_payments).
"""
import threading
import time

from .db import is_mock
from .transactions import run_transaction

START = 500_000
AMOUNT = 100_000
EXPECTED = START - 2 * AMOUNT


class _Log:
    def __init__(self):
        self.t0 = time.perf_counter()
        self.rows = []
        self.lock = threading.Lock()

    def __call__(self, who, msg):
        with self.lock:
            self.rows.append({"ms": round((time.perf_counter() - self.t0) * 1000, 1), "who": who, "msg": msg})


def _reset(db):
    db.lab_cover.drop()
    db.lab_payments.drop()
    db.lab_cover.insert_one({"_id": "POLICY-LAB", "available_cover": START, "version": 0})


def _cover(db):
    return db.lab_cover.find_one({"_id": "POLICY-LAB"})["available_cover"]


def _run_pair(worker):
    barrier = threading.Barrier(2, timeout=5)
    errors = []

    def wrap(name):
        try:
            worker(name, barrier)
        except Exception as exc:  # reported in the result, never raised
            errors.append(f"{name}: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=wrap, args=(n,)) for n in ("Adjuster A", "Adjuster B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    return errors


def lost_update(db):
    _reset(db)
    log = _Log()

    def worker(name, barrier):
        doc = db.lab_cover.find_one({"_id": "POLICY-LAB"})
        log(name, f"reads cover = {doc['available_cover']:,}")
        barrier.wait()
        new = doc["available_cover"] - AMOUNT
        db.lab_cover.update_one({"_id": "POLICY-LAB"}, {"$set": {"available_cover": new}})
        log(name, f"writes cover = {new:,}  ($set computed in the application)")

    errors = _run_pair(worker)
    return _result("A", "Read-modify-write with $set", "No protection: both read the same value, the second write "
                   "overwrites the first", db, log, errors)


def atomic_update(db):
    _reset(db)
    log = _Log()

    def worker(name, barrier):
        barrier.wait()
        res = db.lab_cover.update_one({"_id": "POLICY-LAB", "available_cover": {"$gte": AMOUNT}},
                                      {"$inc": {"available_cover": -AMOUNT}})
        log(name, f"$inc available_cover by -{AMOUNT:,} -> matched {res.matched_count}")

    errors = _run_pair(worker)
    return _result("B", "Atomic update ($inc with a $gte guard)", "The server applies each update atomically on the "
                   "current value; single-document writes are always atomic", db, log, errors)


def optimistic_lock(db):
    _reset(db)
    log = _Log()

    def worker(name, barrier):
        first = True
        while True:
            doc = db.lab_cover.find_one({"_id": "POLICY-LAB"})
            log(name, f"reads cover = {doc['available_cover']:,} at version {doc['version']}")
            if first:
                barrier.wait()
                first = False
            res = db.lab_cover.update_one({"_id": "POLICY-LAB", "version": doc["version"]},
                                          {"$set": {"available_cover": doc["available_cover"] - AMOUNT},
                                           "$inc": {"version": 1}})
            if res.matched_count == 1:
                log(name, f"update with version {doc['version']} succeeded -> version {doc['version'] + 1}")
                return
            log(name, f"CONFLICT: version {doc['version']} is stale; re-reading and retrying")

    errors = _run_pair(worker)
    return _result("C", "Optimistic locking (version field)", "Each update only matches the version it read; the "
                   "loser sees 0 matches, re-reads and retries", db, log, errors)


def transactional(db):
    _reset(db)
    log = _Log()
    mock = is_mock()

    def worker(name, barrier):
        attempt = [0]

        def body(tx):
            attempt[0] += 1
            doc = tx.find_one("lab_cover", {"_id": "POLICY-LAB"})
            log(name, f"attempt {attempt[0]}: reads cover = {doc['available_cover']:,} inside its transaction snapshot")
            if attempt[0] == 1 and not mock:
                barrier.wait()  # both transactions are now open on the same snapshot
            tx.update_one("lab_cover", {"_id": "POLICY-LAB"}, {"$set": {"available_cover": doc["available_cover"] - AMOUNT}})
            tx.db.lab_payments.insert_one({"by": name, "amount": AMOUNT}, **({"session": tx.session} if tx.native else {}))
            log(name, f"attempt {attempt[0]}: writes cover = {doc['available_cover'] - AMOUNT:,} and a payment row")

        run_transaction(db, body, on_retry=lambda n: log(name, f"WriteConflict -> transaction aborted, retry {n}"))
        log(name, "committed")

    errors = _run_pair(worker)
    note = ("Snapshot isolation: the second writer gets a WriteConflict, its transaction is rolled back and "
            "with_transaction() re-runs it on fresh data")
    if mock:
        note = "Mock mode: transactions are serialised by a lock (emulated). Run on MongoDB to see a real WriteConflict"
    res = _result("D", "Multi-document transaction", note, db, log, errors)
    res["payments"] = db.lab_payments.count_documents({})
    return res


def _result(key, title, how, db, log, errors):
    final = _cover(db)
    return {"key": key, "title": title, "how": how, "final": final, "expected": EXPECTED,
            "correct": final == EXPECTED and not errors, "lost": final - EXPECTED, "log": log.rows, "errors": errors}


def run_lab(db):
    return [lost_update(db), atomic_update(db), optimistic_lock(db), transactional(db)]
