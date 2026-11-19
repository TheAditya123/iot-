"""Offline checks for the local dashboard and agent API."""
from pathlib import Path
import tempfile
import unittest

from src.dashboard import create_app
from src.database import EventStore


class FakeTools:
    def report_csv(self, window):
        return "timestamp,people_count\n2026-11-19T00:00:00Z,1\n", 1


class FakeAgent:
    def __init__(self):
        self.tools = FakeTools()
    def status(self):
        return {
            "mode": "local-demo-planner",
            "model": None,
            "tools": ["current_status"],
            "note": "test",
        }
    def chat(self, message, history):
        if not message:
            raise ValueError("message is required")
        return {
            "answer": "One real event is stored.",
            "tool": "current_status",
            "arguments": {},
            "planner": "local-demo-planner",
        }


class DashboardTests(unittest.TestCase):
    def test_frontend_metrics_and_agent_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            store = EventStore(path)
            try:
                store.add({
                    "device_id": "room-monitor-pi", "event_id": "one",
                    "timestamp": "2026-11-19T14:00:00Z",
                    "motion": True, "pir_gpio": 17, "people_count": 2,
                    "inference_ms": 100.0, "inference_backend": "nanodet",
                    "temperature_c": 23.4, "humidity_pct": 45.0,
                    "pressure_hpa": 1008.2,
                })
                first = store.pending("room-monitor-pi")[0][0]
                store.mark_published(first)
                store.add({
                    "device_id": "room-monitor-pi", "event_id": "two",
                    "timestamp": "2026-11-19T14:05:00Z",
                    "motion": True, "pir_gpio": 17, "people_count": 1,
                    "inference_ms": 120.0, "inference_backend": "nanodet",
                })
            finally:
                store.close()

            client = create_app(path, agent=FakeAgent()).test_client()
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Smart Room Operations Agent", page.data)
            status = client.get("/api/status").get_json()
            self.assertEqual(status["event"]["event_id"], "two")
            metrics = client.get("/api/metrics").get_json()
            self.assertEqual(metrics["total_events"], 2)
            self.assertEqual(metrics["published_events"], 1)
            self.assertEqual(metrics["pending_events"], 1)
            self.assertEqual(metrics["max_people"], 2)
            self.assertEqual(metrics["average_inference_ms"], 110.0)
            self.assertEqual(
                client.get("/api/agent/status").get_json()["mode"],
                "local-demo-planner",
            )
            chat = client.post(
                "/api/agent/chat",
                json={"message": "status", "history": []},
            )
            self.assertEqual(chat.status_code, 200)
            self.assertEqual(chat.get_json()["tool"], "current_status")
            report = client.get("/api/agent/report.csv?window=today")
            self.assertEqual(report.status_code, 200)
            self.assertIn("attachment;", report.headers["Content-Disposition"])
            self.assertIn(b"people_count", report.data)

    def test_agent_input_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(
                Path(directory) / "events.json", agent=FakeAgent()
            ).test_client()
            self.assertEqual(
                client.post(
                    "/api/agent/chat",
                    json={"message": "", "history": []},
                ).status_code,
                400,
            )
            self.assertEqual(
                client.post(
                    "/api/agent/chat",
                    json={"message": "status", "history": "bad"},
                ).status_code,
                400,
            )


if __name__ == "__main__":
    unittest.main()
