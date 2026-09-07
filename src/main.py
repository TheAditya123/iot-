"""Run with python -m src.main from the repository root."""
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
from .pir import make_pir

LOG = logging.getLogger(__name__)


def run(config, count=0):
    with ExitStack() as stack:
        store = EventStore(config.data_path)
        stack.callback(store.close)
        camera = make_camera(config)
        stack.callback(camera.close)
        pir = make_pir(config)
        stack.callback(pir.close)
        inference = make_inference(config)
        publisher = MQTTPublisher(config) if config.mqtt_enabled else None
        if publisher:
            stack.callback(publisher.close)
            publisher.start()
        LOG.info("Ready: PIR=%s camera=%s inference=%s MQTT=%s",
                 config.pir_backend, config.camera_backend, config.inference_backend, config.mqtt_enabled)
        produced, next_flush = 0, 0.0
        while count == 0 or produced < count:
            if time.monotonic() >= next_flush:
                flush_outbox(store, publisher, config.device_id)
                next_flush = time.monotonic() + 2
            if not pir.triggered():
                time.sleep(0.1)
                continue
            event = {
                "device_id": config.device_id, "event_id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "motion": True, "pir_backend": config.pir_backend,
                "camera_backend": config.camera_backend, "image_path": None,
            }
            try:
                image = camera.capture()
                if config.save_images:
                    config.image_dir.mkdir(parents=True, exist_ok=True)
                    image_path = config.image_dir / f"{event['event_id']}.jpg"
                    image.save(image_path, "JPEG")
                    event["image_path"] = str(image_path)
                event.update(inference.predict(image))
            except Exception as exc:
                LOG.exception("Capture/inference failed; recording event with error")
                event.update(prediction="error", confidence=None, error=str(exc),
                             inference_backend=config.inference_backend)
            store.add(event)  # Persist before any network operation.
            produced += 1
            LOG.info("Saved event %s: %s", event["event_id"], event["prediction"])
            flush_outbox(store, publisher, config.device_id)
        if publisher:
            # Give short finite runs time to establish the TLS session.
            publisher.connected.wait(config.timeout)
            flush_outbox(store, publisher, config.device_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=0, help="Exit after N events; 0 runs until Ctrl+C")
    parser.add_argument("--env", help="Optional .env file path")
    args = parser.parse_args()
    if args.count < 0:
        parser.error("--count cannot be negative")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run(Config.load(args.env), args.count)
    except KeyboardInterrupt:
        LOG.info("Stopped; unpublished events remain in TinyDB")
    except Exception:
        LOG.exception("Application could not continue")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
