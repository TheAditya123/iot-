# IoT++ repository agent

Act as the engineer responsible for this Raspberry Pi 5 smart-room monitor.
Implement and verify requested changes in the working tree instead of only
describing commands. Ask the user only when a real physical action or account
login is required.

## System contract

- Hardware is an HC-SR501 PIR on BCM GPIO17 and a Raspberry Pi Camera Module 3
  Standard (SC0872/IMX708) on CAM/DISP1.
- The production flow is PIR -> camera -> NanoDet -> event summary -> TinyDB ->
  AWS IoT MQTT and the local dashboard.
- Images remain local under `images/`. MQTT sends event metadata only.
- TinyDB at `data/events.json` is the durable history and MQTT outbox. Persist an
  event before attempting networking and never discard an event because AWS is
  unavailable.
- Production code must use real readings. Never invent motion, images, person
  counts, confidence values, timestamps, or publish success.
- The supported detector is the pinned OpenCV Zoo NanoDet ONNX model. It runs
  locally through OpenCV DNN and keeps only COCO class 0 (`person`).
- `summary` is a truthful sentence derived from the measured detector result.
  Do not describe objects that NanoDet did not report.
- The MQTT connectivity-test topic is `iot/setup/test`. Real room events use
  `iot/room/events` with QoS 1.
- There is no BME280 or environmental sensor in the current product.

## Code map

| Path | Responsibility |
|---|---|
| `src/main.py` | Event orchestration and persistence-before-network order |
| `src/config.py` | `.env` and ignored `.env.aws` loading and validation |
| `src/pir.py` | Real GPIO Zero PIR adapter |
| `src/camera.py` | Picamera2 capture, diagnostics, and atomic JPEG writes |
| `src/inference.py` | NanoDet preprocessing, decoding, NMS, count, and timing |
| `src/summary.py` | One-sentence occupancy result |
| `src/database.py` | Atomic TinyDB storage and persistent MQTT outbox |
| `src/mqtt_client.py` | AWS IoT TLS/QoS 1 publishing |
| `src/dashboard.py` | TinyDB-backed local HTML and JSON endpoints |
| `scripts/hardware_test.py` | Focused real camera and PIR checks |
| `tests/` | Offline pipeline, storage, MQTT, dashboard, and failure tests |

Use `README.md` for normal operation, `docs/pi-bringup-status.md` for measured
Pi evidence, and `docs/aws-setup.md` only for AWS provisioning changes.

## Making changes

- Read the files relevant to the request and preserve the existing simple
  single-process Python design.
- Keep optional hardware optional. If enabled hardware is absent, report the
  real error clearly; do not silently substitute a fake value.
- When adding a sensor, add one focused adapter module, the minimum validated
  configuration, event fields only when a real reading exists, dashboard fields
  when useful, and hardware-boundary tests. Update `.env.example` without
  putting machine-specific values or secrets in it.
- When changing the event schema, update the main pipeline, dashboard, MQTT
  example, and tests together. MQTT automatically publishes the stored event.
- Use Raspberry Pi OS packages for Picamera2/GPIO integrations and the
  `--system-site-packages` virtual environment for project packages.
- Do not introduce Docker, a database server, React, microservices, cloud image
  processing, or a large ML framework unless the user explicitly changes the
  architecture.
- Never reboot or power off the Pi. Tell the user when a physical power cycle is
  required and let the user perform it.

## Verification

Run the checks appropriate to the change:

```bash
.venv/bin/python -m compileall -q src scripts tests
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/hardware_test.py camera
.venv/bin/python scripts/hardware_test.py pir --timeout 120
.venv/bin/python scripts/mqtt_test.py
.venv/bin/python scripts/outbox_test.py
curl --fail http://127.0.0.1:5000/api/status
```

Hardware tests require the connected device and physical movement. Do not call a
hardware path verified solely because an import or mock test passed.

## Security and Git

- Never print or commit `.env`, `.env.aws`, `certs/`, private keys, certificates,
  TinyDB runtime data, captured images, downloaded models, or Python caches.
- Keep `.env.example` and `.env.mqtt.example` secret-free and versioned.
- Before committing, inspect `git status`, `git diff`, `git diff --cached`, and
  ignored files. Confirm staged content contains no credential material.
- Do not reset, overwrite local changes, force-push, or recreate AWS resources.
- Use short commit messages and push only after the relevant checks pass.
