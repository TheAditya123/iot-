"""TinyDB event store with a persistent MQTT outbox and reader locking."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path

from tinydb import TinyDB, Query
from tinydb.storages import Storage


class AtomicJSONStorage(Storage):
    """TinyDB JSON storage that survives interruption during a write."""

    def __init__(self, path):
        self.path = Path(path)
        self.temporary = self.path.with_suffix(self.path.suffix + ".tmp")

    def read(self):
        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        with self.path.open("r", encoding="utf-8") as stream:
            return json.load(stream)

    def write(self, data):
        descriptor = os.open(
            self.temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                descriptor = -1
                json.dump(
                    data, stream, ensure_ascii=False, allow_nan=False,
                    separators=(",", ":"),
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            self.temporary.replace(self.path)
            self.path.chmod(0o600)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            self.temporary.unlink(missing_ok=True)
            raise


class EventStore:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file = path.with_suffix(path.suffix + ".lock").open("a+")
        with self._locked(exclusive=True):
            self.db = TinyDB(path, storage=AtomicJSONStorage)

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
