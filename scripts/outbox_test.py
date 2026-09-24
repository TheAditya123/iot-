"""Verify the real TinyDB outbox and production MQTT publisher without sensors."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import Config
from src.database import EventStore
from src.mqtt_client import MQTTPublisher, flush_outbox


def main() -> int:
    try:
        config = Config.load()
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Outbox test configuration failed: {exc}") from exc
    event = {
        "kind": "outbox_connection_test",
        "device_id": config.device_id,
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
    }
    with tempfile.TemporaryDirectory(prefix="iot-outbox-test-") as directory:
        store = EventStore(Path(directory) / "events.json")
        publisher = MQTTPublisher(config)
        try:
            store.add(event)
            publisher.start()
            if not publisher.connected.wait(config.timeout):
                raise TimeoutError("Production MQTT publisher did not connect to AWS IoT")
            flush_outbox(store, publisher, config.device_id)
            pending = store.pending(config.device_id)
            rows = store.recent(1)
            if pending or not rows or not rows[0].get("published"):
                raise RuntimeError("AWS did not acknowledge the temporary outbox record")
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            raise SystemExit(f"Outbox test failed: {exc}") from exc
        finally:
            publisher.close()
            store.close()
    print("PASS: production TinyDB outbox published and acknowledged on " + config.topic)
    print("Verified payload: " + json.dumps(event))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
