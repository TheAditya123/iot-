import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from src.config import Config
from src.database import EventStore
from src.inference import StubInference, quantize
from src.main import run
from src.mqtt_client import flush_outbox


class PipelineTests(unittest.TestCase):
    def test_simulation_persists_events_and_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {
                "DATA_PATH": str(root / "events.json"), "IMAGE_DIR": str(root / "images"),
                "PIR_INTERVAL_SECONDS": "0.01", "PIR_BACKEND": "simulated",
                "CAMERA_BACKEND": "simulated", "INFERENCE_BACKEND": "stub",
                "MQTT_ENABLED": "false", "SAVE_IMAGES": "true",
            }, clear=True):
                config = Config.load(root / "missing.env")
                run(config, count=2)
            with EventStoreContext(root / "events.json") as store:
                events = store.pending(config.device_id)
                self.assertEqual(len(events), 2)
                self.assertNotEqual(events[0][1]["event_id"], events[1][1]["event_id"])
                for _, event in events:
                    self.assertIsNone(event["confidence"])
                    self.assertEqual(event["prediction"], "unclassified")
                    self.assertTrue(event["timestamp"].endswith("Z"))
                    self.assertTrue(Path(event["image_path"]).is_file())
                    json.dumps(event, allow_nan=False)

    def test_failed_publish_survives_restart_then_ack_clears_outbox(self):
        class Publisher:
            def __init__(self, success):
                self.success = success
                self.events = []

            def publish(self, event):
                self.events.append(event.copy())
                return self.success

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            event = {"device_id": "pi", "event_id": "original-id"}
            with EventStoreContext(path) as store:
                store.add(event)
                store.add({"device_id": "another-device", "event_id": "other-id"})
                failed = Publisher(False)
                flush_outbox(store, failed, "pi")
                self.assertEqual(len(store.pending("pi")), 1)
            with EventStoreContext(path) as store:
                success = Publisher(True)
                flush_outbox(store, success, "pi")
                self.assertEqual(success.events, [event])
                self.assertEqual(store.pending("pi"), [])
                self.assertEqual(len(store.pending("another-device")), 1)

    def test_stub_is_not_a_fake_detection(self):
        self.assertEqual(StubInference().predict(None)["prediction"], "unclassified")

    def test_integer_quantization_uses_model_scale_and_clamps(self):
        detail = {"dtype": np.int8, "quantization_parameters": {"scales": [0.5], "zero_points": [-2]}}
        actual = quantize(np.array([-1000, 0, 1, 1000], dtype=np.float32), detail)
        np.testing.assert_array_equal(actual, [-128, -2, 0, 127])

    def test_quantization_rejects_unsupported_per_axis_input(self):
        detail = {"dtype": np.int8, "quantization_parameters": {"scales": [0.5, 0.2], "zero_points": [0, 0]}}
        with self.assertRaises(ValueError):
            quantize(np.array([0], dtype=np.float32), detail)

class EventStoreContext(EventStore):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


if __name__ == "__main__":
    unittest.main()
