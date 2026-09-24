# IoT++ Smart Room Occupancy + Environment Monitor

This Raspberry Pi 5 project uses a physical PIR as an inexpensive event trigger.
Motion wakes the camera path, a small object detector counts people locally, and
an optional BME280 supplies room conditions. Every event is saved to TinyDB
before its metadata is sent to AWS IoT Core over MQTT/TLS. Images remain on the
Pi.

The lab question is: **Can event-triggered edge AI reduce compute use while
maintaining useful occupancy detection?** The event records already include
inference latency and can later support a comparison with continuous inference.
The initial system deliberately stays small: one Python project, one local JSON
database, and no cloud video stream, database server, container, or accelerator.

```text
HC-SR501 rising edge
        |
        v
Pi camera -> local NanoDet person count
        |                |
        +---- BME280 ----+
                 |
                 v
       TinyDB data/events.json
          |              |
          v              v
local dashboard    AWS IoT MQTT metadata
```

## Current project progress (September 23, 2026)

This project is past the architecture-only stage. The Raspberry Pi now runs the
Python application, the local database, the person detector, the MQTT client,
and the dashboard. The Pi has also been provisioned as an AWS IoT device and can
authenticate with its own X.509 device certificate. The remaining work is the
physical sensor path: Raspberry Pi OS currently sees no CSI camera, the PIR
signal is not reaching GPIO17, and header I2C must be enabled before a connected
BME280 can be tested.

The distinction matters when describing the progress. We have proved each major
software and network component, including the real AWS broker connection. We
have not claimed a successful physical room event, because the current wiring
cannot yet provide a real camera frame, PIR edge, or BME280 measurement. The
application deliberately refuses to replace missing hardware data with made-up
values.

| Area | What has been completed | Evidence on this Pi |
|---|---|---|
| Raspberry Pi platform | Raspberry Pi OS updated; Chromium, Wi-Fi, DNS, HTTPS, SSH, mDNS, Python, and required libraries work | No failed services; dashboard reachable through `raspberrypi.local` |
| Edge AI | Pretrained NanoDet-Plus model downloaded, checksum-verified, integrated through OpenCV DNN, and restricted to the COCO `person` class | Correctly counted two people in a person image and zero in a no-person image |
| Local persistence | TinyDB event log and MQTT outbox implemented with locking, atomic replacement, and restart recovery | 500 writes plus 800 concurrent dashboard-style reads retained all 500 events |
| AWS IoT | Thing, exact-topic policy, X.509 device identity, TLS, QoS 1 publishing, and acknowledgements configured | 12 of 12 soak messages acknowledged; connection and outbox tests pass |
| Dashboard | Flask page and JSON endpoints read directly from TinyDB | 180 of 180 requests succeeded during a five-minute soak |
| Reliability | Hardware errors are retained with the motion event; interrupted JSON/JPEG/config writes preserve the last complete file | 22 automated tests pass |
| Physical camera | Software stack is installed, but no CSI sensor enumerates | `rpicam-hello --list-cameras` reports no cameras |
| Physical PIR | GPIO17 and its Linux driver work, but the HC-SR501 signal path appears floating | GPIO17 stayed LOW during separate two- and five-minute tests; internal pull test passed |
| Physical BME280 | Driver and compensation code are ready | Header bus `/dev/i2c-1` is currently disabled, so the sensor cannot yet be probed |

The detailed command output and hardware reasoning are recorded in
[the Raspberry Pi bring-up report](docs/pi-bringup-status.md).

## What happens during one room event

The final event path is intentionally sequential and local-first:

1. The HC-SR501 monitors the room using almost no Pi compute. A LOW-to-HIGH
   transition on BCM GPIO17 starts one event.
2. Picamera2 captures one still image. The JPEG is written locally under
   `images/` using a temporary file and atomic replacement, so an interrupted
   save is not mistaken for a valid photograph.
3. The Pi passes the image to the NanoDet person detector. Inference happens on
   the Pi 5 CPU; the image is never sent to AWS for analysis.
4. The detector returns a count, confidence values, and measured inference
   time. For example, one accepted person box means `people_count: 1`. A cloud
   or dashboard consumer can interpret any count above zero as “person
   detected” or “room occupied.”
5. If the physical BME280 is enabled, the Pi takes one fresh forced-mode
   temperature, humidity, and pressure measurement.
6. The complete event is written to TinyDB before networking begins.
7. The MQTT publisher sends only the JSON metadata to AWS IoT Core on
   `iot/room/events`. The local JPEG stays on the Pi.
8. TinyDB marks the event published only after the MQTT client receives the QoS
   1 publish acknowledgement. If Wi-Fi or AWS is unavailable, the row remains
   pending and is retried after reconnection or process restart.

This ordering is the main reliability guarantee: a network failure cannot erase
the locally observed event.

## What AWS communication can do now

The Raspberry Pi is configured as the AWS IoT Thing `room-monitor-pi`. It opens
an encrypted MQTT 3.1.1 connection to the account-specific AWS IoT Data-ATS
endpoint on port 8883. AWS authenticates the Pi with its device certificate and
private key; no AWS account password or general-purpose access key is embedded
in the application.

The Pi currently uses two exact MQTT topics:

- `iot/setup/test` is a diagnostic loopback topic. The test subscribes,
  publishes a unique message, waits for AWS to acknowledge it, and confirms the
  same message returns through the broker.
- `iot/room/events` is the application topic. The production publisher sends
  motion, person-count, inference, environmental, and timestamp metadata here.

The live tests prove that this Pi can connect securely, act as an MQTT
publisher, and receive a QoS 1 acknowledgement from AWS. The outbox test also
proves that the same production `MQTTPublisher` and TinyDB code can send a local
row and change it from pending to published only after AWS accepts the message.
A 5.5-minute connection soak sent 12 messages at 30-second intervals, and AWS
acknowledged all 12.

A future real room message will have this shape; the numbers below illustrate
the fields and are not claimed sensor readings:

```json
{
  "device_id": "room-monitor-pi",
  "event_id": "generated UUID",
  "timestamp": "UTC ISO-8601 time",
  "motion": true,
  "pir_gpio": 17,
  "image_path": "/local/path/to/image.jpg",
  "people_count": 1,
  "person_confidences": [0.82],
  "inference_ms": 120.4,
  "temperature_c": 23.71,
  "humidity_pct": 46.18,
  "pressure_hpa": 1007.82
}
```

AWS IoT Core is currently the secure MQTT broker, not the long-term cloud
database. No DynamoDB table or IoT Rule has been created in this milestone. If
cloud history becomes a course requirement, the next cloud step is an AWS IoT
Rule that writes `iot/room/events` into DynamoDB. That keeps AWS database
credentials off the Raspberry Pi.

## What TinyDB is used for

TinyDB is the Pi's local event history and durable MQTT outbox. It is an embedded
JSON document database, so it does not require a database server, Docker, a
login, or another machine. Its runtime file is `data/events.json`.

Each database document contains two parts:

```json
{
  "event": {
    "device_id": "room-monitor-pi",
    "event_id": "...",
    "timestamp": "...",
    "motion": true,
    "people_count": 1
  },
  "published": false
}
```

The `event` object is the measured room metadata. The `published` flag is local
delivery state. A new record begins as `false`. After AWS acknowledges the MQTT
publish, the application changes it to `true`. If publishing fails, the record
stays `false`, survives a restart, and is selected by the next retry. Records
belonging to another device ID are not replayed accidentally.

The dashboard reads this same database, which means it still shows local room
history when AWS or the internet is unavailable. TinyDB does not contain image
bytes; the event stores only a path to the locally retained JPEG. Database
writes are protected by a process lock and use sync plus atomic replacement, so
the dashboard cannot read a half-written JSON file and a power interruption is
less likely to corrupt the last valid history.

## Remaining work before the physical MVP is complete

These tasks are deliberately listed as incomplete rather than hidden behind
software simulations:

1. Power off the Pi and correct the camera ribbon/cable path until
   `rpicam-hello --list-cameras` identifies the actual sensor and
   `rpicam-still` creates a nonempty JPEG.
2. Verify the HC-SR501 labels and wiring: VCC to physical pin 2, GND to pin 6,
   and OUT to physical pin 11 (BCM17). Warm it up and capture a real rising edge.
3. Enable header I2C, reboot, wire the BME280 to 3.3 V/GND/SDA/SCL, and confirm
   address `0x76` or `0x77` with `i2cdetect -y 1`.
4. Run one complete real event: PIR edge → JPEG → person count → BME280 reading
   → TinyDB row → AWS acknowledgement → dashboard display.
5. Collect several real room scenes and tune the confidence threshold and
   camera placement. The two validation images prove that the model executes;
   they are not a complete accuracy study.
6. Run the planned experiment comparing continuous inference against
   PIR-triggered inference using inference count, latency, CPU use, and CPU
   temperature. The existing 500-inference stress run already shows why this
   comparison matters: continuous inference reached the Pi's thermal limit.
7. Add an AWS IoT Rule and DynamoDB only if cloud-side historical storage is
   required by the course. Local operation and MQTT do not depend on it.
8. Add and enable a systemd service only after the real hardware path passes
   manually, so startup automation does not hide wiring failures.

## Bill of materials

- Raspberry Pi 5 with Raspberry Pi OS, power supply, and network access
- Raspberry Pi-compatible CSI camera and the correct Pi 5 ribbon cable
- HC-SR501 PIR motion sensor
- Optional BME280 breakout with I2C support
- Female-to-female jumper wires

The code does not assume the camera model. `rpicam-hello --list-cameras` reports
the actual connected sensor. A normal 15-pin camera board needs a 15-pin to
22-pin cable for a Pi 5 CAM/DISP connector.

## Wiring (power the Pi off first)

HC-SR501:

| HC-SR501 | Raspberry Pi 5 header |
|---|---|
| VCC | 5V, physical pin 2 |
| GND | Ground, physical pin 6 |
| OUT | BCM GPIO17, physical pin 11 |

`PIR_GPIO=17` is a BCM number. Use the labels printed on the sensor; wire colors
are not reliable. Never drive a Pi GPIO input with 5 V. A normal HC-SR501 output
is approximately 3.3 V, but check an unfamiliar board before connecting it.

BME280:

| BME280 | Raspberry Pi 5 header |
|---|---|
| VIN / VCC | 3.3V, physical pin 1 |
| GND | Ground, physical pin 9 |
| SDA | GPIO2 / SDA1, physical pin 3 |
| SCL | GPIO3 / SCL1, physical pin 5 |

Do not power the BME280 from 5 V unless the exact breakout board explicitly
supports it. The software probes only the standard addresses `0x76` and `0x77`.

## Raspberry Pi installation

```bash
sudo apt update
sudo apt install -y \
  git python3-venv python3-gpiozero python3-lgpio python3-picamera2 \
  python3-numpy python3-pil python3-smbus2 i2c-tools

git clone https://github.com/TheAditya123/iot-.git ~/iot-project
cd ~/iot-project
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements-pi.txt
cp .env.example .env
.venv/bin/python scripts/download_model.py
```

Use `git pull --ff-only` in an existing clean checkout. The venv needs
`--system-site-packages` because Raspberry Pi OS supplies Picamera2, GPIO Zero,
lgpio, smbus2, NumPy, and Pillow. Project packages remain inside `.venv`.

The model downloader obtains the 3.8 MB OpenCV Zoo NanoDet model and verifies
its SHA-256 hash before placing it under `models/`. The model is ignored by Git.

Run the same detector used by the event loop against any real JPEG or PNG:

```bash
.venv/bin/python scripts/inference_test.py /path/to/image.jpg
```

The command prints the image and model SHA-256 values, measured inference time,
real person count, and confidence values. It does not insert a simulated event.

## Configuration

Hardware and local settings live in ignored `.env`. AWS identity and TLS paths
live separately in ignored `.env.aws`.

Useful `.env` values:

```dotenv
DEVICE_ID=room-monitor-pi
PIR_GPIO=17
PIR_WARMUP_SECONDS=60
PIR_COOLDOWN_SECONDS=5
DATA_PATH=data/events.json

CAMERA_BACKEND=pi
IMAGE_DIR=images

ENV_SENSOR_ENABLED=false
BME280_I2C_BUS=1
BME280_ADDRESS=auto

INFERENCE_BACKEND=nanodet
MODEL_PATH=models/object_detection_nanodet_2022nov.onnx
PERSON_CONFIDENCE_THRESHOLD=0.5

DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=5000
```

Keep the BME280 disabled until it is wired and detected. Production modes never
fall back to simulated motion, environmental values, images, or predictions.

## Verify the camera

First ask Raspberry Pi OS what is physically present:

```bash
rpicam-hello --list-cameras
rpicam-still --nopreview --immediate -o /tmp/camera-test.jpg
test -s /tmp/camera-test.jpg && file /tmp/camera-test.jpg
```

Then exercise the same Picamera2 adapter used by the application:

```bash
.venv/bin/python scripts/hardware_test.py camera
```

An import succeeding does not prove the camera works. Both enumeration and a
nonempty real JPEG are required.

## Verify the PIR

Allow about a minute after power-up for an HC-SR501 to settle. Test a real low
to high transition on GPIO17:

```bash
.venv/bin/python scripts/hardware_test.py pir --timeout 120
```

Let the sensor become inactive, then walk across its field of view. If it stays
active, reduce its delay knob, step away, and retry. The code triggers on the
rising edge and applies `PIR_COOLDOWN_SECONDS` to avoid duplicate events.

## Verify the BME280

Enable I2C once, then reboot:

```bash
sudo raspi-config nonint do_i2c 0
sudo reboot
```

After reconnecting:

```bash
i2cdetect -y 1
```

A real device should appear at `76` or `77`. Set `ENV_SENSOR_ENABLED=true` in
`.env`, leave `BME280_ADDRESS=auto`, and test it:

```bash
.venv/bin/python scripts/hardware_test.py environment
```

The driver reads the BME280's own calibration registers and returns compensated
temperature, humidity, and pressure. If sensing is enabled while I2C or the
device is absent, startup fails with a direct error instead of inventing values.

## Local person counting

The project uses the OpenCV Zoo
[`object_detection_nanodet_2022nov.onnx`](https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_nanodet)
model. OpenCV identifies this version as **NanoDet-m-plus-1.5x at 416×416**. It
was pretrained on the COCO object-detection dataset by the upstream NanoDet
project and exported to ONNX for portable inference. We did not train a new
neural network or create synthetic weights. Our contribution is the Raspberry
Pi inference adapter, person-only decoding, event integration, testing, and
performance measurement.

NanoDet-Plus is a lightweight, one-stage, anchor-free detector. In plain terms,
one neural-network pass examines the whole image and predicts object categories,
confidence scores, and bounding-box distances at several image scales. The
upstream architecture uses Generalized Focal Loss and lightweight multi-scale
feature fusion. That makes it much smaller than a typical desktop detector and
practical on a Pi CPU. The original training code and design are documented in
the [NanoDet repository](https://github.com/RangiLyu/nanodet); the model used
here is distributed through OpenCV Zoo under Apache 2.0.

For every accepted PIR event, `src/inference.py` performs these steps:

1. Convert the Picamera2 image to RGB.
2. Resize it into a 416×416 input while preserving its aspect ratio. Unused
   space is padded instead of stretching people wider or taller.
3. Normalize the pixels with the mean and standard deviation expected by the
   pretrained model.
4. Run the ONNX graph locally with OpenCV DNN on the Pi CPU.
5. Read the COCO class scores but keep only class zero, `person`.
6. Reject candidates below `PERSON_CONFIDENCE_THRESHOLD`, currently `0.5`.
7. Decode the predicted box distances at strides 8, 16, 32, and 64.
8. Apply non-maximum suppression with an IoU threshold of `0.6`, removing
   duplicate boxes that describe the same visible person.
9. Count the remaining boxes and measure total inference latency.

The detector returns event metadata rather than an identity:

- `people_count`
- `person_confidences`
- `inference_ms`
- `inference_backend=nanodet`

For example, two accepted boxes produce `people_count: 2` and two confidence
values. The code does not perform face recognition, identify individuals, or
send an image to a cloud vision service. The count means “people visibly
detected in this frame”; it is not a permanent occupancy state and can miss an
occluded, poorly lit, or out-of-frame person.

The downloaded model is about 3.8 MB and must match SHA-256
`4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186`.
The checksum prevents an incomplete or unexpectedly changed download from being
used. On this Pi, the detector found two people in the OpenCV basketball image
with confidences `0.831` and `0.696`, and found zero people in a separate fruit
image. A 500-call alternating endurance run produced all 250 expected positive
and 250 expected negative results, with a mean inference time of 119.7 ms.

That validation proves the model loads and the counting logic works on ARM64,
but two images are not enough to claim room-level accuracy. Real camera scenes,
lighting, distance, partial occlusion, and camera placement still need to be
tested after the CSI connection is fixed. A lower confidence threshold can find
less obvious people but will also increase false positives.

## Run the local pipeline

Use local mode before configuring AWS:

```bash
.venv/bin/python -m src.main --local-only --count 1
```

After the PIR warm-up, create one new motion transition. The application saves
the JPEG, performs local inference, reads the enabled BME280, persists the event,
and exits. Inspect the real output:

```bash
find images -maxdepth 1 -type f -name '*.jpg' -printf '%TY-%Tm-%Td %TT %p %s bytes\n'
.venv/bin/python -c "from tinydb import TinyDB; print(TinyDB('data/events.json').all())"
```

A complete event has the available real fields, for example:

```json
{
  "device_id": "room-monitor-pi",
  "event_id": "generated UUID",
  "timestamp": "UTC ISO-8601 time",
  "motion": true,
  "pir_gpio": 17,
  "image_path": "/local/path/to/image.jpg",
  "people_count": 1,
  "person_confidences": [0.82],
  "inference_ms": 120.4,
  "temperature_c": 23.71,
  "humidity_pct": 46.18,
  "pressure_hpa": 1007.82
}
```

The numbers above illustrate the schema only. Runtime records contain measured
values. A hardware read failure is logged and stored as an error field; other
real parts of the event are still retained.

## TinyDB and MQTT outbox

TinyDB stores wrappers with `event` and `published` fields in
`data/events.json`. `src.main` writes locally before any network operation. If
AWS is disconnected or rejects a publish, the record remains unpublished and
is retried in order. Images are never put in MQTT payloads; only their local path
is metadata. TinyDB writes and JPEG saves use temporary files plus atomic
replacement, and the database uses a file lock so dashboard reads cannot see a
partially written event file.

## AWS IoT Core

AWS uses MQTT 3.1.1 over TLS on port 8883 with an X.509 device certificate.
There are two exact topics:

| Topic | Purpose |
|---|---|
| `iot/setup/test` | one-shot subscribe/publish/receive connectivity test |
| `iot/room/events` | smart-room application event metadata |

See [docs/aws-setup.md](docs/aws-setup.md) for provisioning. When `.env.aws` and
the three TLS files exist, run:

The bootstrap saves the private key, certificate, state, and `.env.aws` with
mode `0600`. State/config writes are synced and atomically replaced; exclusive
key/certificate creation plus validation makes an interrupted setup fail closed
instead of silently reusing incomplete files.

```bash
.venv/bin/python scripts/mqtt_test.py
.venv/bin/python scripts/outbox_test.py
.venv/bin/python -m src.main --count 1
```

The first command must receive the exact unique message it published. The
second creates a temporary, clearly labelled non-sensor record and proves the
production TinyDB outbox is marked published only after AWS acknowledges it.
The third uses the real PIR/camera/sensor pipeline and publishes pending events
to `MQTT_TOPIC`. DynamoDB is intentionally outside this milestone. If required
later, route MQTT events with an AWS IoT Rule instead of placing database
credentials on the Pi.

On a Pi without an existing AWS profile, AWS CLI v2 can authenticate from a
browser session with `aws login --remote --profile iot-dev --region us-east-1`.
Install `requirements-aws.txt` first because that login provider requires the
pinned AWS CRT package. Never paste the resulting temporary authorization or
AWS credentials into documentation or Git.

## Local dashboard

Run the monitor and dashboard in separate terminals:

```bash
.venv/bin/python -m src.main --local-only
.venv/bin/python -m src.dashboard
```

On the Pi, open `http://127.0.0.1:5000`. From the same LAN, use
`http://PI_ADDRESS:5000`. The dashboard reads TinyDB and shows the latest motion,
people count, room conditions, timestamp, inference time, publish state, and a
recent event table. JSON is available at `/api/status` and `/api/events`.

```bash
curl http://127.0.0.1:5000/api/status
```

## Tests

```bash
.venv/bin/python -m compileall -q src scripts tests
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/aws_bootstrap.py --region us-east-1
```

These checks do not fake a successful hardware or AWS result. Fakes exist only
inside unit tests, where they verify full event assembly, hardware-error
preservation, compensation, MQTT acknowledgements, persistent retry, detector
preprocessing, and dashboard output. Use `scripts/hardware_test.py`,
`scripts/inference_test.py`, and `scripts/mqtt_test.py` for real-device checks.

## Troubleshooting

- See [the dated Pi bring-up report](docs/pi-bringup-status.md) for the observed
  device evidence behind the current camera, PIR, and I2C conclusions.
- **No cameras available:** shut down, disconnect power, and reseat both ribbon
  ends with the contacts facing the connector contacts. Confirm the Pi 5 cable
  and try the other CAM/DISP connector. If `camera_auto_detect=1` loads no sensor
  overlay for an official module, the problem is in the physical camera path.
  Use a manual overlay only after identifying a third-party sensor.
- **PIR never changes:** verify 5V, ground, and OUT to physical pin 11; allow the
  warm-up; reduce the module delay; confirm `pinctrl get 17` shows an input.
- **No `/dev/i2c-1`:** enable I2C with `raspi-config` and reboot.
- **No `76` or `77` in `i2cdetect`:** power down and recheck 3.3V, ground, SDA,
  and SCL. Confirm the board is a BME280 rather than a BMP280.
- **Model missing:** run `.venv/bin/python scripts/download_model.py`.
- **Low person count:** improve lighting and camera angle, then consider lowering
  the confidence threshold slightly. Do not substitute a fake count.
- **AWS connection failure:** check the Data-ATS hostname, certificate status,
  policy attachment, exact client ID/topics, local clock, DNS, and outbound 8883.
- **Events stay unpublished:** inspect `.env.aws` and subscribe to the exact event
  topic in the AWS IoT test client. Local data remains safe for retry.

## Files that must never be committed

The `.gitignore` excludes `.env`, `.env.aws`, `.venv/`, generated images,
TinyDB runtime data, downloaded models, AWS state, certificates, and private
keys. Never add these with `git add -f`:

- `certs/device.private.key`
- `certs/device.cert.pem`
- `.env.aws`
- AWS access keys or session credentials

Keep the private key mode at `0600`. `.env.example` and `.env.mqtt.example` are
safe templates and remain versioned.

## Project layout

| File | Responsibility |
|---|---|
| `src/main.py` | Coordinates PIR edges, capture, inference, BME280 reads, local persistence, and MQTT retry in that order |
| `src/config.py` | Loads and validates hardware settings from `.env` and AWS/TLS settings from `.env.aws` |
| `src/pir.py` | Opens the real HC-SR501 input through GPIO Zero using BCM numbering |
| `src/camera.py` | Captures through Picamera2 or an explicitly selected USB webcam and installs complete JPEGs atomically |
| `src/inference.py` | Preprocesses images, runs NanoDet through OpenCV DNN, decodes person boxes, applies NMS, and measures latency |
| `src/environmental.py` | Detects a BME280 at `0x76`/`0x77`, reads its factory calibration, and calculates compensated measurements |
| `src/database.py` | Stores TinyDB events, maintains `published` state, locks readers/writers, and atomically replaces JSON |
| `src/mqtt_client.py` | Establishes the X.509/TLS MQTT connection, publishes with QoS 1, and drains the persistent outbox |
| `src/dashboard.py` | Serves the current status, recent event table, `/api/status`, and `/api/events` directly from TinyDB |
| `scripts/hardware_test.py` | Performs focused real camera, PIR, or BME280 tests and explains the observed hardware failure mode |
| `scripts/inference_test.py` | Runs the real model on a supplied image and prints hashes, count, confidences, and latency |
| `scripts/download_model.py` | Downloads the pinned ONNX file and refuses it if the SHA-256 checksum differs |
| `scripts/mqtt_test.py` | Proves a unique QoS 1 message is acknowledged and returned through `iot/setup/test` |
| `scripts/outbox_test.py` | Proves the production TinyDB and publisher mark a temporary event only after AWS acknowledgement |
| `scripts/aws_bootstrap.py` | Creates or reuses the AWS IoT Thing, exact-topic policy, certificate, endpoint configuration, and secure local files |

Add a systemd service only after camera, PIR, BME280, local inference, and MQTT
have each passed manually. That keeps initial hardware debugging visible.
