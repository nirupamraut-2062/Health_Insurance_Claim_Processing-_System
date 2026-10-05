"""Multi-document ACID transactions.

Real MongoDB (replica set / Atlas): a client session with
``readConcern: snapshot`` and ``writeConcern: majority``; MongoDB commits all
writes together or none of them.

Mock mode: mongomock has no sessions, so ``_MockTx`` emulates the same
guarantee - it serialises transactions with a lock, records a before-image of
every document it changes and restores them if anything fails.
"""
from contextlib import contextmanager

from .db import is_mock, write_lock


class _RealTx:
    native = True

    def __init__(self, db, session):
        self.db, self.session = db, session

    def find_one(self, coll, flt, projection=None):
        return self.db[coll].find_one(flt, projection, session=self.session)

    def update_one(self, coll, flt, update):
        return self.db[coll].update_one(flt, update, session=self.session)

    def insert_one(self, coll, doc):
        from .schema import insert_validated
        return insert_validated(self.db, coll, doc, session=self.session)


class _MockTx:
    native = False

    def __init__(self, db):
        self.db = db
        self._undo = []

    def find_one(self, coll, flt, projection=None):
        return self.db[coll].find_one(flt, projection)

    def update_one(self, coll, flt, update):
        before = self.db[coll].find_one(flt)
        result = self.db[coll].update_one(flt, update)
        if before is not None:
            self._undo.append(("restore", coll, before))
        return result

    def insert_one(self, coll, doc):
        from .schema import insert_validated
        result = insert_validated(self.db, coll, doc)
        self._undo.append(("delete", coll, result.inserted_id))
        return result

    def rollback(self):
        for action, coll, payload in reversed(self._undo):
            if action == "restore":
                self.db[coll].replace_one({"_id": payload["_id"]}, payload)
            else:
                self.db[coll].delete_one({"_id": payload})
        self._undo.clear()


@contextmanager
def transaction(db):
    """``with transaction(db) as tx:`` - commit on success, roll back on any exception."""
    if is_mock():
        with write_lock:
            tx = _MockTx(db)
            try:
                yield tx
            except BaseException:
                tx.rollback()
                raise
    else:
        from pymongo.read_concern import ReadConcern
        from pymongo.write_concern import WriteConcern
        with db.client.start_session() as session:
            with session.start_transaction(read_concern=ReadConcern("snapshot"),
                                           write_concern=WriteConcern("majority")):
                yield _RealTx(db, session)
