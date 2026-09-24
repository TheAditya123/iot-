"""Offline checks for configuration and the persistent MQTT outbox."""
import json
import math
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from src import config as config_module
from src.camera import save_jpeg_atomic
from src.config import Config
from src.database import EventStore
from src.dashboard import create_app
from src.environmental import BME280
from src.inference import letterbox
from src.main import run
from src.mqtt_client import flush_outbox


class PipelineTests(unittest.TestCase):
    def test_one_trigger_persists_complete_local_event_and_jpeg(self):
        class PIR:
            def __init__(self):
                self.states = iter((False, True))

            @property
            def motion_detected(self):
                return next(self.states, True)

            def close(self):
                pass

        class Camera:
            def capture(self):
                return Image.new("RGB", (16, 16), "white")

            def close(self):
                pass

        class Inference:
            def predict(self, image):
                return {
                    "people_count": 1,
                    "person_confidences": [0.8],
                    "inference_ms": 12.3,
                    "inference_backend": "nanodet",
                }

        class Environment:
            def read(self):
                return {
                    "temperature_c": 22.5,
                    "humidity_pct": 40.0,
                    "pressure_hpa": 1000.0,
                }

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = SimpleNamespace(
                data_path=root / "events.json",
                image_dir=root / "images",
                gpio=17,
                warmup=0,
                cooldown=0,
                camera_backend="pi",
                inference_backend="nanodet",
                env_sensor_enabled=True,
                mqtt_enabled=False,
                device_id="test-pi",
                timeout=0.1,
            )
            with patch("src.main.open_pir", return_value=PIR()), \
                    patch("src.main.make_camera", return_value=Camera()), \
                    patch("src.main.make_inference", return_value=Inference()), \
                    patch("src.main.make_environment", return_value=Environment()):
                run(config, count=1)

            store = EventStore(config.data_path)
            try:
                rows = store.recent()
            finally:
                store.close()
            self.assertEqual(len(rows), 1)
            event = rows[0]["event"]
            self.assertTrue(event["motion"])
            self.assertEqual(event["pir_gpio"], 17)
            self.assertEqual(event["people_count"], 1)
            self.assertEqual(event["temperature_c"], 22.5)
            self.assertEqual(event["humidity_pct"], 40.0)
            self.assertEqual(event["pressure_hpa"], 1000.0)
            self.assertTrue(Path(event["image_path"]).is_file())
            self.assertFalse(rows[0]["published"])

    def test_hardware_boundary_errors_are_persisted_with_the_motion_event(self):
        class PIR:
            def __init__(self):
                self.states = iter((False, True))

            @property
            def motion_detected(self):
                return next(self.states, True)

            def close(self):
                pass

        class BrokenCamera:
            def capture(self):
                raise OSError("camera disconnected during capture")

            def close(self):
                pass

        class BrokenEnvironment:
            def read(self):
                raise OSError("I2C device stopped responding")

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = SimpleNamespace(
                data_path=root / "events.json",
                image_dir=root / "images",
                gpio=17,
                warmup=0,
                cooldown=0,
                camera_backend="pi",
                inference_backend="nanodet",
                env_sensor_enabled=True,
                mqtt_enabled=False,
                device_id="test-pi",
                timeout=0.1,
            )
            with patch("src.main.open_pir", return_value=PIR()), \
                    patch("src.main.make_camera", return_value=BrokenCamera()), \
                    patch("src.main.make_inference"), \
                    patch("src.main.make_environment", return_value=BrokenEnvironment()), \
                    patch("src.main.LOG"):
                run(config, count=1)

            store = EventStore(config.data_path)
            try:
                rows = store.recent()
            finally:
                store.close()
            event = rows[0]["event"]
            self.assertTrue(event["motion"])
            self.assertEqual(event["capture_error"], "camera disconnected during capture")
            self.assertEqual(event["environment_error"], "I2C device stopped responding")
            self.assertNotIn("image_path", event)
            self.assertNotIn("people_count", event)
            self.assertFalse(rows[0]["published"])

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

    def test_atomic_store_preserves_last_valid_file_when_serialization_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            store = EventStore(path)
            try:
                store.add({"device_id": "pi", "event_id": "valid"})
                valid_bytes = path.read_bytes()
                with self.assertRaises(ValueError):
                    store.add({"device_id": "pi", "event_id": "invalid", "value": math.nan})
                self.assertEqual(path.read_bytes(), valid_bytes)
                self.assertFalse(path.with_suffix(".json.tmp").exists())
                json.loads(path.read_text(encoding="utf-8"))
            finally:
                store.close()
            reopened = EventStore(path)
            try:
                rows = reopened.recent()
                self.assertEqual([row["event"]["event_id"] for row in rows], ["valid"])
            finally:
                reopened.close()

    def test_atomic_store_preserves_last_valid_file_when_disk_sync_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            store = EventStore(path)
            try:
                store.add({"device_id": "pi", "event_id": "durable"})
                valid_bytes = path.read_bytes()
                with patch("src.database.os.fsync", side_effect=OSError("disk sync failed")):
                    with self.assertRaisesRegex(OSError, "disk sync failed"):
                        store.add({"device_id": "pi", "event_id": "not-durable"})
                self.assertEqual(path.read_bytes(), valid_bytes)
                self.assertFalse(path.with_suffix(".json.tmp").exists())
            finally:
                store.close()
            reopened = EventStore(path)
            try:
                rows = reopened.recent()
                self.assertEqual([row["event"]["event_id"] for row in rows], ["durable"])
            finally:
                reopened.close()

    def test_atomic_jpeg_replaces_only_after_a_complete_save(self):
        class BrokenImage:
            def save(self, path, image_format):
                Path(path).write_bytes(b"partial")
                raise OSError("capture interrupted")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.jpg"
            path.write_bytes(b"previous-valid-file")
            with self.assertRaisesRegex(OSError, "capture interrupted"):
                save_jpeg_atomic(BrokenImage(), path)
            self.assertEqual(path.read_bytes(), b"previous-valid-file")
            self.assertFalse(path.with_suffix(".jpg.tmp").exists())

            save_jpeg_atomic(Image.new("RGB", (16, 16), "white"), path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with Image.open(path) as saved:
                saved.verify()

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
