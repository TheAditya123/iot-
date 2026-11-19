"""Offline tests for the monitor/dashboard local command channel."""
from pathlib import Path
import tempfile
import threading
import unittest

from PIL import Image

from src.main import process_control_command
from src.monitor_control import MonitorControl


class FakeCamera:
    def capture(self):
        return Image.new("RGB", (16, 16), "white")


class FakeInference:
    def predict(self, image):
        return {
            "people_count": 1,
            "person_confidences": [0.8],
            "inference_ms": 12.0,
            "inference_backend": "nanodet",
            "person_confidence_threshold": 0.5,
        }


class MonitorControlTests(unittest.TestCase):
    def test_request_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            control = MonitorControl(Path(directory))
            control.touch_heartbeat()
            self.assertTrue(control.is_alive())

            def worker():
                command = None
                while command is None:
                    command = control.consume_next()
                control.respond(command["id"], {"ok": True, "value": 7})

            thread = threading.Thread(target=worker)
            thread.start()
            result = control.request("demo", timeout=2)
            thread.join(timeout=2)
            self.assertEqual(result, {"ok": True, "value": 7})

    def test_running_monitor_executes_agent_capture_with_owned_camera(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            image_dir = root / "images"
            control = MonitorControl(data_dir)
            request_id = control.submit("capture_and_infer")
            config = type("Config", (), {"image_dir": image_dir})()

            self.assertTrue(
                process_control_command(
                    control, FakeCamera(), FakeInference(), config
                )
            )
            result = control.wait_response(request_id, timeout=1)
            self.assertTrue(result["ok"])
            self.assertEqual(result["people_count"], 1)
            self.assertTrue(Path(result["image_path"]).is_file())

    def test_unsupported_monitor_action_returns_bounded_error(self):
        with tempfile.TemporaryDirectory() as directory:
            control = MonitorControl(Path(directory))
            request_id = control.submit("run_shell")
            config = type("Config", (), {"image_dir": Path(directory) / "images"})()
            process_control_command(control, FakeCamera(), FakeInference(), config)
            result = control.wait_response(request_id, timeout=1)
            self.assertFalse(result["ok"])
            self.assertIn("Unsupported monitor action", result["detail"])


if __name__ == "__main__":
    unittest.main()
