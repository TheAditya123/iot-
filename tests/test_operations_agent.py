"""Offline tests for the bounded Smart Room operations agent."""
from datetime import datetime
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from src.database import EventStore
from src.operations_agent import OpenAIPlanner, OperationsAgent, OperationsTools, fallback_plan
from src.runtime_settings import get_person_threshold, set_person_threshold


class AgentTests(unittest.TestCase):
    def make_tools(self, root, now):
        path = root / "data" / "events.json"
        return path, OperationsTools(path, root=root, now_fn=lambda: now)

    def add(self, path, event, published=False):
        store = EventStore(path)
        try:
            store.add(event)
            if published:
                doc_id = store.pending(event["device_id"])[0][0]
                store.mark_published(doc_id)
        finally:
            store.close()

    def test_time_queries_and_day_comparison_use_real_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime.fromisoformat("2026-11-19T15:00:00-06:00")
            path, tools = self.make_tools(root, now)
            self.add(path, {
                "device_id": "pi", "event_id": "a",
                "timestamp": "2026-11-19T20:30:00Z", "people_count": 2,
            }, True)
            self.add(path, {
                "device_id": "pi", "event_id": "b",
                "timestamp": "2026-11-19T20:50:00Z", "people_count": 1,
            })
            self.add(path, {
                "device_id": "pi", "event_id": "c",
                "timestamp": "2026-11-18T20:30:00Z", "people_count": 1,
            }, True)
            last_hour = tools.occupancy_summary("last_hour")
            self.assertEqual(last_hour["event_count"], 2)
            self.assertEqual(last_hour["detection_sum"], 3)
            self.assertEqual(last_hour["peak_people"], 2)
            comparison = tools.compare_days()
            self.assertEqual(comparison["today"]["detection_sum"], 3)
            self.assertEqual(comparison["yesterday"]["detection_sum"], 1)
            self.assertEqual(comparison["detection_delta"], 2)

    def test_filter_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime.fromisoformat("2026-11-19T15:00:00-06:00")
            path, tools = self.make_tools(root, now)
            self.add(path, {
                "device_id": "pi", "event_id": "a",
                "timestamp": "2026-11-19T20:00:00Z", "people_count": 4,
            })
            self.add(path, {
                "device_id": "pi", "event_id": "b",
                "timestamp": "2026-11-19T20:10:00Z", "people_count": 2,
            })
            matches = tools.events_with_people(4, "today")
            self.assertEqual(matches["count"], 1)
            csv_text, count = tools.report_csv("today")
            self.assertEqual(count, 2)
            self.assertIn("event_id", csv_text)
            self.assertIn("2026-11-19T20:00:00Z", csv_text)

    def test_runtime_threshold_is_bounded_and_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "runtime-settings.json"
            self.assertEqual(get_person_threshold(0.5, path), 0.5)
            self.assertEqual(set_person_threshold(0.6, path), 0.6)
            self.assertEqual(get_person_threshold(0.5, path), 0.6)
            with self.assertRaises(ValueError):
                set_person_threshold(0.95, path)

    def test_fallback_planner_covers_classroom_prompts(self):
        self.assertEqual(
            fallback_plan("How many people were detected in the last hour?")["tool"],
            "occupancy_summary",
        )
        self.assertEqual(
            fallback_plan("Was the room busier today than yesterday?")["tool"],
            "compare_days",
        )
        plan = fallback_plan("Show events with more than 3 people")
        self.assertEqual(plan["tool"], "events_with_people")
        self.assertEqual(plan["arguments"]["min_people"], 4)
        self.assertEqual(
            fallback_plan("Run a sensor health check")["tool"], "sensor_health"
        )
        self.assertEqual(fallback_plan("Why is AWS offline?")["tool"], "cloud_health")
        self.assertEqual(
            fallback_plan("Capture one image and run inference now")["tool"],
            "capture_and_infer",
        )
        self.assertEqual(
            fallback_plan("Change the person confidence threshold to 0.6"),
            {"tool": "set_threshold", "arguments": {"value": 0.6}},
        )
        self.assertEqual(
            fallback_plan("Export today's occupancy report")["tool"],
            "export_report",
        )

    def test_agent_falls_back_when_ai_planner_fails(self):
        class BrokenPlanner:
            available = True
            model = "broken"
            def plan(self, message, history=None):
                raise RuntimeError("offline")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime.fromisoformat("2026-11-19T15:00:00-06:00")
            path, tools = self.make_tools(root, now)
            self.add(path, {
                "device_id": "pi", "event_id": "a",
                "timestamp": "2026-11-19T20:30:00Z", "people_count": 2,
            })
            response = OperationsAgent(
                tools, planner=BrokenPlanner()
            ).chat("How many people were detected in the last hour?")
            self.assertEqual(response["tool"], "occupancy_summary")
            self.assertEqual(response["planner"], "local-fallback-after-ai-error")
            self.assertIn("2 total person detections", response["answer"])

    def test_capture_prefers_running_monitor_command_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, tools = self.make_tools(
                root, datetime.fromisoformat("2026-11-19T15:00:00-06:00")
            )
            control = MagicMock()
            control.is_alive.return_value = True
            control.request.return_value = {
                "ok": True,
                "people_count": 2,
                "inference_ms": 101.0,
                "image_path": "images/agent.jpg",
            }
            with patch("src.operations_agent.MonitorControl", return_value=control), \
                 patch("src.operations_agent.Config.load") as config_load:
                result = tools.capture_and_infer()
            self.assertTrue(result["ok"])
            self.assertEqual(result["people_count"], 2)
            control.request.assert_called_once_with("capture_and_infer", timeout=8.0)
            config_load.assert_not_called()

    def test_capture_failure_is_truthfully_returned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, tools = self.make_tools(
                root, datetime.fromisoformat("2026-11-19T15:00:00-06:00")
            )
            fake = SimpleNamespace(
                camera_backend="pi",
                model_path=root / "model.onnx",
                inference_backend="nanodet",
            )
            fake.model_path.write_bytes(b"x")
            with patch("src.operations_agent.Config.load", return_value=fake), \
                 patch(
                     "src.operations_agent.make_camera",
                     side_effect=RuntimeError("camera busy"),
                 ):
                result = tools.capture_and_infer()
            self.assertFalse(result["ok"])
            self.assertIn("camera busy", result["detail"])

    def test_openai_planner_parses_only_supported_tool_selection(self):
        planner = OpenAIPlanner()
        planner.api_key = "test-key"
        planner.model = "gpt-6-luna"
        response = MagicMock()
        response.output_text = '{"tool":"occupancy_summary","arguments":{"window":"last_hour"}}'
        client = MagicMock()
        client.responses.create.return_value = response
        with patch("openai.OpenAI", return_value=client):
            plan = planner.plan("How many people were detected in the last hour?")
        self.assertEqual(
            plan,
            {"tool": "occupancy_summary", "arguments": {"window": "last_hour"}},
        )
        call = client.responses.create.call_args.kwargs
        self.assertEqual(call["model"], "gpt-6-luna")
        self.assertEqual(call["input"][-1]["content"], "How many people were detected in the last hour?")

    def test_cloud_health_without_credentials_is_truthful_and_offline_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime.fromisoformat("2026-11-19T15:00:00-06:00")
            path, tools = self.make_tools(root, now)
            result = tools.cloud_health()
            self.assertFalse(result["configured"])
            self.assertFalse(result["tls_reachable"])
            self.assertIn("MQTT_ENDPOINT", result["detail"])
            self.assertEqual(result["pending_events"], 0)


if __name__ == "__main__":
    unittest.main()
