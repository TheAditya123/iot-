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

`src/inference.py` runs OpenCV Zoo NanoDet on the Pi CPU. NanoDet is trained on
COCO; class zero is `person`. The adapter letterboxes each camera image to
416x416, decodes only person candidates, performs non-maximum suppression, and
returns:

- `people_count`
- `person_confidences`
- `inference_ms`
- `inference_backend=nanodet`

Set `PERSON_CONFIDENCE_THRESHOLD` between 0 and 1. A lower value finds less
obvious people but increases false positives. No face or identity recognition is
performed. The application runs inference once per accepted PIR event rather
than continuously.

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

```text
src/main.py            PIR-triggered event loop
src/camera.py          Picamera2 and optional USB camera adapters
src/pir.py             physical GPIO Zero PIR adapter
src/inference.py       OpenCV NanoDet person counting
src/environmental.py   BME280 I2C driver
src/database.py        TinyDB and persistent MQTT outbox
src/mqtt_client.py     AWS MQTT/TLS publisher
src/dashboard.py       local Flask dashboard and JSON API
scripts/hardware_test.py
scripts/inference_test.py
scripts/download_model.py
scripts/mqtt_test.py
scripts/outbox_test.py
scripts/aws_bootstrap.py
```

Add a systemd service only after camera, PIR, BME280, local inference, and MQTT
have each passed manually. That keeps initial hardware debugging visible.
