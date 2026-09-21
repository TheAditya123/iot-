"""Physical PIR motion and camera -> TinyDB -> AWS IoT MQTT (optional ML)."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import logging
import time
import uuid

from .camera import make_camera
from .config import Config
from .database import EventStore
from .inference import make_inference
from .mqtt_client import MQTTPublisher, flush_outbox
from .pir import open_pir

LOG = logging.getLogger(__name__)


def run(config, count=0):
    with ExitStack() as stack:
        store = EventStore(config.data_path)
        stack.callback(store.close)
        camera = make_camera(config)
        if camera:
            stack.callback(camera.close)
        pir = open_pir(config.gpio)
        stack.callback(pir.close)
        inference = make_inference(config)
        publisher = MQTTPublisher(config) if config.mqtt_enabled else None
        if publisher:
            stack.callback(publisher.close)
            publisher.start()
            if not publisher.connected.wait(config.timeout):
                LOG.warning("AWS is not connected yet; motion events will remain in TinyDB for retry")
        LOG.info("PIR GPIO%d warming up for %.0f seconds", config.gpio, config.warmup)
        time.sleep(config.warmup)
        LOG.info("Ready: real PIR on GPIO%d; camera=%s; model=%s; AWS MQTT=%s",
                 config.gpio, config.camera_backend, config.inference_backend, config.mqtt_enabled)
        produced, next_flush, last_trigger = 0, 0.0, float("-inf")
        was_active = pir.motion_detected
        while count == 0 or produced < count:
            if time.monotonic() >= next_flush:
                flush_outbox(store, publisher, config.device_id)
                next_flush = time.monotonic() + 2
            active = pir.motion_detected
            rising = active and not was_active
            was_active = active
            if not rising or time.monotonic() - last_trigger < config.cooldown:
                time.sleep(0.1)
                continue
            last_trigger = time.monotonic()
            event = {
                "device_id": config.device_id, "event_id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "motion": True, "pir_gpio": config.gpio,
            }
            if camera:
                try:
                    image = camera.capture()
                    config.image_dir.mkdir(parents=True, exist_ok=True)
                    image_path = config.image_dir / f"{event['event_id']}.jpg"
                    image.save(image_path, "JPEG")
                    event["image_path"] = str(image_path)
                    LOG.info("Camera image saved: %s", image_path)
                    if inference:
                        event.update(inference.predict(image))
                except Exception as exc:
                    LOG.exception("Camera/model failed; saving the real PIR event")
                    event["capture_error"] = str(exc)
            store.add(event)  # Persist before any network operation.
            produced += 1
            LOG.info("Motion detected and saved: %s", event["event_id"])
            flush_outbox(store, publisher, config.device_id)
        if publisher:
            # Give short finite runs time to establish the TLS session.
            publisher.connected.wait(config.timeout)
            flush_outbox(store, publisher, config.device_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=0, help="Exit after N events; 0 runs until Ctrl+C")
    parser.add_argument("--env", help="Optional .env file path")
    parser.add_argument("--local-only", action="store_true", help="Use the real PIR and TinyDB without AWS")
    args = parser.parse_args()
    if args.count < 0:
        parser.error("--count cannot be negative")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run(Config.load(args.env, args.local_only), args.count)
    except KeyboardInterrupt:
        LOG.info("Stopped; unpublished events remain in TinyDB")
    except Exception:
        LOG.exception("Application could not continue")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
