"""Configuration paths are relative to the repository, not the working directory."""
from dataclasses import dataclass
import os
from pathlib import Path
import re

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def flag(name, default):
    value = os.getenv(name, str(default)).lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} must be true or false")
    return value in {"true", "1", "yes"}


def path(name, default):
    value = Path(os.getenv(name, default)).expanduser()
    return value if value.is_absolute() else ROOT / value


@dataclass(frozen=True)
class Config:
    device_id: str
    pir_backend: str
    interval: float
    gpio: int
    camera_backend: str
    webcam_index: int
    inference_backend: str
    model_path: Path
    labels_path: Path
    input_mean: float
    input_std: float
    output_logits: bool
    data_path: Path
    image_dir: Path
    save_images: bool
    mqtt_enabled: bool
    endpoint: str
    port: int
    topic: str
    ca_cert: Path
    client_cert: Path
    private_key: Path
    timeout: float

    @classmethod
    def load(cls, env_file=None):
        load_dotenv(env_file or ROOT / ".env", override=False)
        config = cls(
            os.getenv("DEVICE_ID", "room-monitor-pi"),
            os.getenv("PIR_BACKEND", "simulated"),
            float(os.getenv("PIR_INTERVAL_SECONDS", "5")),
            int(os.getenv("PIR_GPIO", "17")),
            os.getenv("CAMERA_BACKEND", "simulated"),
            int(os.getenv("WEBCAM_INDEX", "0")),
            os.getenv("INFERENCE_BACKEND", "stub"),
            path("MODEL_PATH", "models/model.tflite"),
            path("LABELS_PATH", "models/labels.txt"),
            float(os.getenv("MODEL_INPUT_MEAN", "0")),
            float(os.getenv("MODEL_INPUT_STD", "255")),
            flag("MODEL_OUTPUT_LOGITS", False),
            path("DATA_PATH", "data/events.json"),
            path("IMAGE_DIR", "images"),
            flag("SAVE_IMAGES", True),
            flag("MQTT_ENABLED", False),
            os.getenv("MQTT_ENDPOINT", ""),
            int(os.getenv("MQTT_PORT", "8883")),
            os.getenv("MQTT_TOPIC", "iot/room/events"),
            path("MQTT_CA_CERT", "certs/AmazonRootCA1.pem"),
            path("MQTT_CLIENT_CERT", "certs/device.cert.pem"),
            path("MQTT_PRIVATE_KEY", "certs/device.private.key"),
            float(os.getenv("MQTT_TIMEOUT_SECONDS", "10")),
        )
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", config.device_id):
            raise ValueError("DEVICE_ID must be 1-128 letters, digits, underscores or hyphens")
        for name, value, choices in [
            ("PIR_BACKEND", config.pir_backend, {"simulated", "gpio"}),
            ("CAMERA_BACKEND", config.camera_backend, {"simulated", "webcam", "pi"}),
            ("INFERENCE_BACKEND", config.inference_backend, {"stub", "tflite"}),
        ]:
            if value not in choices:
                raise ValueError(f"{name} must be one of {sorted(choices)}")
        import math
        if not all(math.isfinite(v) for v in (config.interval, config.timeout, config.input_mean, config.input_std)):
            raise ValueError("Numeric settings must be finite")
        if config.interval <= 0 or config.timeout <= 0 or config.input_std <= 0:
            raise ValueError("Intervals, timeouts, and MODEL_INPUT_STD must be positive")
        if not 1 <= config.port <= 65535:
            raise ValueError("MQTT_PORT must be 1-65535")
        if not config.topic or any(c in config.topic for c in "#+\x00"):
            raise ValueError("MQTT_TOPIC must be a nonempty publish topic without wildcards")
        if config.mqtt_enabled:
            if not config.endpoint or "://" in config.endpoint:
                raise ValueError("Set MQTT_ENDPOINT to the AWS IoT hostname (no scheme)")
            for cert in (config.ca_cert, config.client_cert, config.private_key):
                if not cert.is_file():
                    raise ValueError(f"Missing TLS file: {cert}")
        return config
