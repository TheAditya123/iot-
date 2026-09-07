"""One-shot AWS MQTT publish/receive test. No hardware or application code needed."""
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import ssl
import threading
import uuid

from dotenv import dotenv_values
from paho.mqtt import client as mqtt

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    device_id: str
    endpoint: str
    topic: str
    ca_cert: Path
    client_cert: Path
    private_key: Path
    timeout: float = 20
    port: int = 8883

    @classmethod
    def load(cls, env_file, timeout):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("--timeout must be a positive finite number")
        if not env_file.is_file():
            raise ValueError(f"Missing {env_file}. Run the AWS bootstrap or follow docs/aws-setup.md first.")
        values = {**dotenv_values(env_file), **os.environ}

        def setting(name, default=""):
            return (values.get(name) or default).strip()

        def local_path(name, default):
            path = Path(setting(name, default)).expanduser()
            path = path if path.is_absolute() else ROOT / path
            if not path.is_file():
                raise ValueError(f"Missing TLS file: {path}")
            return path

        device_id = setting("DEVICE_ID", "room-monitor-pi")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", device_id):
            raise ValueError("DEVICE_ID must match the IoT Thing and policy client ID")
        endpoint = setting("MQTT_ENDPOINT")
        if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", endpoint):
            raise ValueError("Set MQTT_ENDPOINT to your AWS IoT Data-ATS hostname (no https:// or port)")
        topic = setting("MQTT_TOPIC", "iot/setup/test")
        if not re.fullmatch(r"[A-Za-z0-9_/-]{1,256}", topic):
            raise ValueError("MQTT_TOPIC must be an exact topic without wildcards")
        if setting("MQTT_PORT", "8883") != "8883":
            raise ValueError("This basic AWS TLS test uses MQTT_PORT=8883")
        return cls(device_id, endpoint, topic,
                   local_path("MQTT_CA_CERT", "certs/AmazonRootCA1.pem"),
                   local_path("MQTT_CLIENT_CERT", "certs/device.cert.pem"),
                   local_path("MQTT_PRIVATE_KEY", "certs/device.private.key"), timeout)


def run_test(settings):
    connected, subscribed, received = threading.Event(), threading.Event(), threading.Event()
    errors = []
    payload = json.dumps({"kind": "mqtt_connection_test", "test_id": uuid.uuid4().hex,
                          "device_id": settings.device_id, "message": "hello from laptop",
                          "timestamp": datetime.now(timezone.utc).isoformat()}).encode("utf-8")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=settings.device_id,
                         clean_session=True, protocol=mqtt.MQTTv311, reconnect_on_failure=False)
    context = ssl.create_default_context(cafile=str(settings.ca_cert))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(settings.client_cert), str(settings.private_key))
    client.tls_set_context(context)

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            errors.append(f"AWS rejected the MQTT connection: {reason_code}")
        connected.set()

    def on_connect_fail(client, userdata):
        errors.append("Could not connect: check endpoint, certificates, DNS and outbound port 8883")
        connected.set()

    def on_subscribe(client, userdata, mid, reason_codes, properties):
        if not reason_codes or any(code.is_failure for code in reason_codes):
            errors.append("AWS rejected the subscription; check iot:Subscribe on the topicfilter ARN")
        subscribed.set()

    def on_message(client, userdata, message):
        # A unique payload prevents an old retained message or another client from passing the test.
        if message.topic == settings.topic and message.payload == payload:
            received.set()

    client.on_connect, client.on_connect_fail = on_connect, on_connect_fail
    client.on_subscribe, client.on_message = on_subscribe, on_message

    def wait(event, description):
        if not event.wait(settings.timeout):
            raise TimeoutError(f"Timed out waiting for {description}")
        if errors:
            raise RuntimeError(errors[0])

    try:
        client.connect_async(settings.endpoint, settings.port, keepalive=60)
        client.loop_start()
        wait(connected, "AWS MQTT connection")
        print("Connected securely to AWS IoT.")
        result, _ = client.subscribe(settings.topic, qos=1)
        if result != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"Could not subscribe: {mqtt.error_string(result)}")
        wait(subscribed, "subscription acknowledgment")
        info = client.publish(settings.topic, payload, qos=1, retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"Could not publish: {mqtt.error_string(info.rc)}")
        info.wait_for_publish(timeout=settings.timeout)
        if not info.is_published():
            raise TimeoutError("AWS did not acknowledge the published test message")
        wait(received, "the same test message from AWS; check iot:Receive on the topic ARN")
        print(f"PASS: AWS acknowledged and returned the test message on {settings.topic}.")
        return json.loads(payload)
    finally:
        client.disconnect()
        client.loop_stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=ROOT / ".env.aws")
    parser.add_argument("--timeout", type=float, default=20, help="Seconds allowed per connection/test stage")
    args = parser.parse_args()
    try:
        run_test(Settings.load(args.env, args.timeout))
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"MQTT test failed: {exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, "MQTT test stopped.\n")


if __name__ == "__main__":
    main()
