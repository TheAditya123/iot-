"""Load Pi hardware settings from .env and AWS credentials from .env.aws."""
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Config:
    device_id: str
    client_id: str
    gpio: int
    warmup: float
    cooldown: float
    data_path: Path
    camera_backend: str
    webcam_index: int
    image_dir: Path
    inference_backend: str
    model_path: Path
    labels_path: Path
    input_mean: float
    input_std: float
    output_logits: bool
    mqtt_enabled: bool
    endpoint: str
    port: int
    topic: str
    ca_cert: Path
    client_cert: Path
    private_key: Path
    timeout: float

    @classmethod
    def load(cls, env_file=None, local_only=False):
        # AWS settings win when both files define a value; process environment wins last.
        values = {**dotenv_values(ROOT / ".env"), **dotenv_values(ROOT / ".env.aws")}
        if env_file:
            values.update(dotenv_values(env_file))
        values.update(os.environ)

        def get(name, default=""):
            return str(values.get(name) or default).strip()

        def path(name, default):
            value = Path(get(name, default)).expanduser()
            return value if value.is_absolute() else ROOT / value

        def boolean(name, default):
            value = get(name, default).lower()
            if value not in {"true", "false"}:
                raise ValueError(f"{name} must be true or false")
            return value == "true"

        device_id = get("DEVICE_ID", "room-monitor-pi")
        config = cls(
            device_id=device_id,
            client_id=get("MQTT_CLIENT_ID", device_id),
            gpio=int(get("PIR_GPIO", "17")),
            warmup=float(get("PIR_WARMUP_SECONDS", "60")),
            cooldown=float(get("PIR_COOLDOWN_SECONDS", "5")),
            data_path=path("DATA_PATH", "data/events.json"),
            camera_backend=get("CAMERA_BACKEND", "off"),
            webcam_index=int(get("WEBCAM_INDEX", "0")),
            image_dir=path("IMAGE_DIR", "images"),
            inference_backend=get("INFERENCE_BACKEND", "off"),
            model_path=path("MODEL_PATH", "models/model.tflite"),
            labels_path=path("LABELS_PATH", "models/labels.txt"),
            input_mean=float(get("MODEL_INPUT_MEAN", "0")),
            input_std=float(get("MODEL_INPUT_STD", "255")),
            output_logits=boolean("MODEL_OUTPUT_LOGITS", "false"),
            mqtt_enabled=not local_only,
            endpoint=get("MQTT_ENDPOINT"),
            port=int(get("MQTT_PORT", "8883")),
            topic=get("MQTT_TOPIC", "iot/setup/test"),
            ca_cert=path("MQTT_CA_CERT", "certs/AmazonRootCA1.pem"),
            client_cert=path("MQTT_CLIENT_CERT", "certs/device.cert.pem"),
            private_key=path("MQTT_PRIVATE_KEY", "certs/device.private.key"),
            timeout=float(get("MQTT_TIMEOUT_SECONDS", "10")),
        )
        for name, value in (("DEVICE_ID", config.device_id), ("MQTT_CLIENT_ID", config.client_id)):
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
                raise ValueError(f"{name} must be 1-128 letters, digits, underscores or hyphens")
        if config.gpio not in range(0, 28):
            raise ValueError("PIR_GPIO must be a BCM GPIO number from 0 to 27")
        if not all(math.isfinite(v) for v in (config.warmup, config.cooldown, config.timeout,
                                               config.input_mean, config.input_std)):
            raise ValueError("Numeric settings must be finite")
        if config.warmup < 0 or config.cooldown < 0 or config.timeout <= 0 or config.input_std <= 0:
            raise ValueError("Warmup/cooldown must be nonnegative; timeout/std must be positive")
        if config.camera_backend not in {"off", "pi", "webcam"}:
            raise ValueError("CAMERA_BACKEND must be off, pi or webcam")
        if config.inference_backend not in {"off", "tflite"}:
            raise ValueError("INFERENCE_BACKEND must be off or tflite")
        if config.inference_backend != "off" and config.camera_backend == "off":
            raise ValueError("INFERENCE_BACKEND=tflite requires a camera")
        if config.mqtt_enabled:
            if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", config.endpoint):
                raise ValueError("Set MQTT_ENDPOINT in .env.aws to the AWS IoT Data-ATS hostname")
            if config.port != 8883:
                raise ValueError("This X.509 MQTT connection uses port 8883")
            if not re.fullmatch(r"[A-Za-z0-9_/-]{1,256}", config.topic):
                raise ValueError("MQTT_TOPIC must be one exact topic without wildcards")
            for cert in (config.ca_cert, config.client_cert, config.private_key):
                if not cert.is_file():
                    raise ValueError(f"Missing TLS file: {cert}")
        return config
