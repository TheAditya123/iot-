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
from src.dashboard import create_app
from src.environmental import BME280
from src.inference import letterbox
from src.mqtt_client import flush_outbox


class PipelineTests(unittest.TestCase):
    def test_real_pir_is_default_and_local_mode_needs_no_aws_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(config_module, "ROOT", Path(directory)), patch.dict(os.environ, {}, clear=True):
                config = Config.load(local_only=True)
        self.assertEqual(config.gpio, 17)
        self.assertEqual(config.camera_backend, "pi")
        self.assertEqual(config.inference_backend, "off")
        self.assertFalse(config.env_sensor_enabled)
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

    def test_detector_letterbox_preserves_image_and_target_shape(self):
        image = np.full((100, 200, 3), 255, dtype=np.uint8)
        actual = letterbox(image)
        self.assertEqual(actual.shape, (416, 416, 3))
        self.assertTrue(np.all(actual[104:312] == 255))
        self.assertTrue(np.all(actual[:104] == 0))

    def test_bme280_compensates_datasheet_temperature_and_pressure(self):
        class Bus:
            def write_byte_data(self, *args):
                pass

            def read_byte_data(self, address, register):
                return 0

            def read_i2c_block_data(self, address, register, length):
                adc_p, adc_t, adc_h = 415148, 519888, 32257
                return [
                    adc_p >> 12, (adc_p >> 4) & 0xFF, (adc_p & 0x0F) << 4,
                    adc_t >> 12, (adc_t >> 4) & 0xFF, (adc_t & 0x0F) << 4,
                    adc_h >> 8, adc_h & 0xFF,
                ]

        sensor = BME280.__new__(BME280)
        sensor.bus, sensor.address = Bus(), 0x76
        sensor.calibration = {
            "T1": 27504, "T2": 26435, "T3": -1000,
            "P1": 36477, "P2": -10685, "P3": 3024, "P4": 2855,
            "P5": 140, "P6": -7, "P7": 15500, "P8": -14600, "P9": 6000,
            "H1": 75, "H2": 362, "H3": 0, "H4": 315, "H5": 50, "H6": 30,
        }
        reading = sensor.read()
        self.assertEqual(reading["temperature_c"], 25.08)
        self.assertAlmostEqual(reading["pressure_hpa"], 1006.53, places=1)
        self.assertTrue(0 <= reading["humidity_pct"] <= 100)

    def test_dashboard_reads_real_event_fields_from_tinydb(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            store = EventStore(path)
            try:
                store.add({"event_id": "one", "timestamp": "2026-01-01T00:00:00Z",
                           "motion": True, "people_count": 2, "temperature_c": 22.5})
            finally:
                store.close()
            client = create_app(path).test_client()
            status = client.get("/api/status")
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.get_json()["event"]["people_count"], 2)
            self.assertIn(b"Smart Room Monitor", client.get("/").data)


if __name__ == "__main__":
    unittest.main()
