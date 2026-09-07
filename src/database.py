"""Single-process TinyDB store with a persistent MQTT outbox."""
from tinydb import TinyDB, Query


class EventStore:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = TinyDB(path)

    def add(self, event):
        self.db.insert({"event": event, "published": False})

    def pending(self, device_id, limit=20):
        # A changed device identity must not replay another device's records.
        records = self.db.search((Query().published == False) & (Query().event.device_id == device_id))
        return [(record.doc_id, record["event"]) for record in records[:limit]]

    def mark_published(self, doc_id):
        self.db.update({"published": True}, doc_ids=[doc_id])

    def close(self):
        self.db.close()
