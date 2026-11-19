"""Offline checks for the local Smart Room dashboard APIs."""
from pathlib import Path
import tempfile
import unittest

from src.dashboard import create_app
from src.database import EventStore


class DashboardTests(unittest.TestCase):
    def test_frontend_and_metrics_use_real_tinydb_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            store = EventStore(path)
            try:
                store.add({
                    "device_id": "room-monitor-pi",
                    "event_id": "one",
                    "timestamp": "2026-11-19T14:00:00Z",
                    "motion": True,
                    "pir_gpio": 17,
                    "people_count": 2,
                    "inference_ms": 100.0,
                    "inference_backend": "nanodet",
                    "temperature_c": 23.4,
                    "humidity_pct": 45.0,
                    "pressure_hpa": 1008.2,
                })
                first = store.pending("room-monitor-pi")[0][0]
                store.mark_published(first)
                store.add({
                    "device_id": "room-monitor-pi",
                    "event_id": "two",
                    "timestamp": "2026-11-19T14:05:00Z",
                    "motion": True,
                    "pir_gpio": 17,
                    "people_count": 1,
                    "inference_ms": 120.0,
                    "inference_backend": "nanodet",
                })
            finally:
                store.close()

            client = create_app(path).test_client()
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Smart Room Control Center", page.data)

            status = client.get("/api/status").get_json()
            self.assertEqual(status["event"]["event_id"], "two")
            self.assertFalse(status["published"])

            metrics = client.get("/api/metrics").get_json()
            self.assertEqual(metrics["total_events"], 2)
            self.assertEqual(metrics["published_events"], 1)
            self.assertEqual(metrics["pending_events"], 1)
            self.assertEqual(metrics["max_people"], 2)
            self.assertEqual(metrics["average_inference_ms"], 110.0)

            events = client.get("/api/events?limit=1").get_json()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event"]["event_id"], "two")


if __name__ == "__main__":
    unittest.main()
