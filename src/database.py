"""TinyDB event store with a persistent MQTT outbox and reader locking."""
from contextlib import contextmanager
import fcntl

from tinydb import TinyDB, Query


class EventStore:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file = path.with_suffix(path.suffix + ".lock").open("a+")
        with self._locked(exclusive=True):
            self.db = TinyDB(path)

    @contextmanager
    def _locked(self, exclusive):
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(self.lock_file, operation)
        try:
            yield
        finally:
            fcntl.flock(self.lock_file, fcntl.LOCK_UN)

    def add(self, event):
        with self._locked(exclusive=True):
            self.db.insert({"event": event, "published": False})

    def pending(self, device_id, limit=20):
        # A changed device identity must not replay another device's records.
        with self._locked(exclusive=False):
            records = self.db.search((Query().published == False) & (Query().event.device_id == device_id))
        return [(record.doc_id, record["event"]) for record in records[:limit]]

    def mark_published(self, doc_id):
        with self._locked(exclusive=True):
            self.db.update({"published": True}, doc_ids=[doc_id])

    def recent(self, limit=50):
        with self._locked(exclusive=False):
            records = self.db.all()[-limit:]
        return [dict(record) for record in reversed(records)]

    def close(self):
        self.db.close()
        self.lock_file.close()
