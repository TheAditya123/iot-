"""Offline checks for configuration and the persistent MQTT outbox."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from src import config as config_module
from src.config import Config
from src.database import EventStore
from src.inference import quantize
from src.mqtt_client import flush_outbox


class PipelineTests(unittest.TestCase):
    def test_real_pir_is_default_and_local_mode_needs_no_aws_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(config_module, "ROOT", Path(directory)), patch.dict(os.environ, {}, clear=True):
                config = Config.load(local_only=True)
        self.assertEqual(config.gpio, 17)
        self.assertEqual(config.camera_backend, "pi")
        self.assertEqual(config.inference_backend, "off")
        self.assertFalse(config.mqtt_enabled)

    def test_cloud_mode_requires_device_endpoint_and_certs(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(config_module, "ROOT", Path(directory)), patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, "MQTT_ENDPOINT"):
                    Config.load()

    def test_no_simulated_sensor_or_prediction_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(config_module, "ROOT", Path(directory)), \
                    patch.dict(os.environ, {"CAMERA_BACKEND": "simulated"}, clear=True):
                with self.assertRaisesRegex(ValueError, "CAMERA_BACKEND"):
                    Config.load(local_only=True)
            with patch.object(config_module, "ROOT", Path(directory)), \
                    patch.dict(os.environ, {"INFERENCE_BACKEND": "stub"}, clear=True):
                with self.assertRaisesRegex(ValueError, "INFERENCE_BACKEND"):
                    Config.load(local_only=True)

    def test_failed_publish_survives_restart_then_ack_clears_outbox(self):
        class Publisher:
            def __init__(self, success):
                self.success = success
                self.events = []
                self.config = SimpleNamespace(topic="iot/setup/test")

            def publish(self, event):
                self.events.append(event.copy())
                return self.success

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            event = {"device_id": "pi", "event_id": "original-id", "motion": True}
            store = EventStore(path)
            try:
                store.add(event)
                store.add({"device_id": "another-device", "event_id": "other-id"})
                flush_outbox(store, Publisher(False), "pi")
                self.assertEqual(len(store.pending("pi")), 1)
            finally:
                store.close()
            store = EventStore(path)
            try:
                success = Publisher(True)
                flush_outbox(store, success, "pi")
                self.assertEqual(success.events, [event])
                self.assertEqual(store.pending("pi"), [])
                self.assertEqual(len(store.pending("another-device")), 1)
            finally:
                store.close()

    def test_integer_quantization_uses_model_scale_and_clamps(self):
        detail = {"dtype": np.int8, "quantization_parameters": {"scales": [0.5], "zero_points": [-2]}}
        actual = quantize(np.array([-1000, 0, 1, 1000], dtype=np.float32), detail)
        np.testing.assert_array_equal(actual, [-128, -2, 0, 127])


if __name__ == "__main__":
    unittest.main()
