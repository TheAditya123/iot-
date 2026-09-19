# Raspberry Pi PIR → TinyDB → AWS IoT

This project runs on a Raspberry Pi 5 with a **physical HC-SR501 PIR sensor**.
Each motion trigger creates one event in local TinyDB (`data/events.json`). The
Pi publishes the same event as JSON to AWS IoT Core over MQTT/TLS. If AWS is
offline, the record remains in TinyDB and the app retries it. Camera capture
and TFLite inference are optional and **off by default**; no sensor readings,
photos, or predictions are simulated.

## 1. Wire the PIR (Pi powered off)

| HC-SR501 | Raspberry Pi 5 header |
|---|---|
| VCC | 5V, physical pin 2 |
| GND | Ground, physical pin 6 |
| OUT | GPIO17, physical pin 11 |

`PIR_GPIO=17` uses **BCM GPIO numbering**, not physical pin numbering. Check
the markings on your particular sensor before connecting it. The Pi GPIO
input must never receive 5V. The PIR may need about a minute to settle after
power-on; the app waits 60 seconds by default.

## 2. Install on the Pi

Open Terminal on the Pi, or use VS Code Remote SSH (its terminal runs on the Pi):

```bash
sudo apt update
sudo apt install -y git python3-venv python3-gpiozero python3-lgpio python3-picamera2 python3-numpy python3-pil
git clone https://github.com/TheAditya123/iot-.git ~/iot-project
cd ~/iot-project
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements-pi.txt
cp .env.example .env
```

If you cloned the repo earlier, run `cd ~/iot-project && git pull` instead of
cloning again. Update an older `.env`: set `CAMERA_BACKEND=off` and
`INFERENCE_BACKEND=off` (the previous `simulated`/`stub` values are rejected).
The virtual environment must use `--system-site-packages` so Python can see
the Pi's camera and GPIO libraries.

## 3. Check the real PIR and local database first

```bash
cd ~/iot-project
.venv/bin/python -m src.main --local-only --count 1
```

After warm-up, walk in front of the PIR, then step away and return if it was
already detecting motion. The program prints `Motion detected and saved` and
exits after one real trigger. Inspect the TinyDB event:

```bash
.venv/bin/python -c "from tinydb import TinyDB; print(TinyDB('data/events.json').all())"
```

If no event appears, check VCC/GND/OUT, GPIO17, and the sensor's delay and
sensitivity knobs. `Ctrl+C` stops a continuous run (`--count` omitted).

## 4. Connect the Pi to AWS IoT Core

Use a **Pi-specific IoT Thing and certificate**. The existing laptop Thing is
only a laptop test. [docs/aws-setup.md](docs/aws-setup.md) explains the AWS
bootstrap and manual-console options. The Pi needs these three files in
`~/iot-project/certs/`:

```text
AmazonRootCA1.pem
device.cert.pem
device.private.key
```

It also needs `~/iot-project/.env.aws` with its Thing name, allowed MQTT client
ID, AWS Data-ATS endpoint, exact topic, and certificate paths. Copy
`.env.mqtt.example` to `.env.aws` and edit it if you provisioned through the
AWS console. If you used `scripts/aws_bootstrap.py --apply` **on the Pi**, it
creates these files automatically. Do not commit keys or `.env.aws` to Git.

Subscribe to the exact `MQTT_TOPIC` in the [AWS IoT MQTT test client](https://us-east-1.console.aws.amazon.com/iot/home?region=us-east-1#/test)
for the same AWS region. Check the MQTT connection first:

```bash
.venv/bin/python scripts/mqtt_test.py
```

Then run the real pipeline:

```bash
.venv/bin/python -m src.main
```

Move in front of the PIR. The event appears in `data/events.json` and in the
AWS test client. The console receives **metadata only**; local image files, if
camera capture is enabled later, are not uploaded to AWS. To see what still
needs publishing:

```bash
.venv/bin/python -c "from tinydb import TinyDB, Query; print(TinyDB('data/events.json').search(Query().published == False))"
```

The app retries unsent events while it runs. `--count 1` exits after one real
motion event. Stop a continuous run with `Ctrl+C`.

## Later: camera and model

`CAMERA_BACKEND=off` and `INFERENCE_BACKEND=off` in `.env` are intentional.
When you have a connected camera, choose `pi` for a CSI camera or `webcam` for
a USB camera (install `python3-opencv` for the webcam path). A model is not included; only set `INFERENCE_BACKEND=tflite`
after adding a compatible classifier and labels as described in
[models/README.md](models/README.md). The app never invents a classification.

## Project files

- `src/main.py`: real motion event loop.
- `src/pir.py`: HC-SR501 GPIO adapter.
- `src/database.py`: TinyDB event store and retry outbox.
- `src/mqtt_client.py`: AWS MQTT/TLS publisher.
- `src/camera.py`, `src/inference.py`: optional real camera/model adapters.
- `scripts/aws_bootstrap.py`, `scripts/mqtt_test.py`: AWS setup and connection check.
- `.env.example`: Pi hardware settings; `.env.mqtt.example`: AWS settings template.
