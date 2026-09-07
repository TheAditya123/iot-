# Camera + PIR IoT starter

Run the complete local pipeline on a laptop before the Raspberry Pi arrives:

```text
Simulated PIR (or GPIO PIR) -> camera -> inference -> TinyDB outbox
                                                        |
                                              MQTT / TLS, QoS 1
                                                        |
                                                  AWS IoT Core
                                                        |
                                                  DynamoDB rule
                                                        |
                                                   DynamoDB
```

The default configuration needs no hardware, AWS account, or ML model. It emits
one motion event every five seconds, generates a labeled synthetic image, reports
`unclassified` (stub inference), and saves the event locally. This is a starter,
not a trained recognition system. S3/image upload is not part of this version.

## Windows quick start

Python 3.11+ and Git are required. In PowerShell:

```powershell
Set-Location 'C:\iot++'
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-laptop.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m src.main --count 3
```

Only copy `.env.example` on first setup; preserve your edited `.env` later.
Using the venv's executable avoids PowerShell activation-policy changes.
Inspect `data/events.json` and the three JPEG files under `images/`.
Run without `--count 3` for continuous operation; Ctrl+C closes resources.
Set `PIR_INTERVAL_SECONDS=1` for faster simulation. Set `SAVE_IMAGES=false`
to keep only metadata. All paths in configuration resolve relative to this repo.
Existing process environment variables take precedence over `.env`.

## Use the laptop camera

Set `CAMERA_BACKEND=webcam` and, if necessary, `WEBCAM_INDEX=0` in `.env`.
Keep `PIR_BACKEND=simulated` until the Pi arrives. Allow the Python process
camera access in Windows privacy settings and close other apps using the camera.
The webcam remains open during the run and captures on each simulated trigger.

## Enable AWS + MQTT

Follow [the exact AWS setup instructions](docs/aws-setup.md): configure an AWS
profile, install `requirements-aws.txt`, preview the bootstrap, run it with
`--apply`, then verify the MQTT message and corresponding DynamoDB item.
The generated `.env.aws` allows an immediate cloud simulation test.

No AWS provisioning or credentials are required for local use. Certificates,
keys, `.env` files, images, the database, and local bootstrap state are excluded
from Git. The script generates a least-privilege publish-only device policy and
a table-specific rule role. Git initialization does not create a GitHub remote.

## Move to Raspberry Pi later

Use Raspberry Pi OS 64-bit and a compatible camera cable for your Pi model.
Install the OS-integrated camera/GPIO packages rather than pip-installing libcamera:

```bash
sudo apt update
sudo apt install -y python3-venv python3-picamera2 python3-gpiozero python3-lgpio python3-numpy
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements-pi.txt
cp .env.example .env
```

Set `CAMERA_BACKEND=pi`, `PIR_BACKEND=gpio`, and `PIR_GPIO=17` (BCM numbering).
Verify the sensor pinout and output voltage before wiring: Pi GPIO accepts 3.3 V
logic. With a compatible PIR, connect signal to BCM17 (physical pin 11), ground
to a Pi ground, and power per the sensor's datasheet. Let the PIR stabilize on
power-up. The GPIO adapter captures on observed low-to-high motion transitions.
GPIO setup and hardware validation must be completed on the actual Pi.

Copy the device credentials securely to `certs/` and MQTT settings into `.env`.
Stop the laptop client before using the same certificate/client ID on the Pi.
Run `.venv/bin/python -m src.main`. The camera stays initialized during the run;
capture and inference are triggered by motion.

## Add machine learning

See [models/README.md](models/README.md) for the exact classifier input/output
contract, runtime choices, labels, preprocessing, and quantization support.
The TFLite adapter requires your own model and matching labels. Do not interpret
stub results as actual detections.

## Layout and checks

```text
src/                    Config, PIR, camera, inference, TinyDB, MQTT, main loop
scripts/aws_bootstrap.py AWS resource preview/provisioning and certificate setup
tests/                  Offline storage/retry, inference and policy tests
docs/aws-setup.md        Account permissions, MQTT validation, recovery, cleanup
models/                 Model integration guide; bring your own model/labels
certs/ data/ images/     Ignored local credentials and generated output
.env.example            All configuration options
requirements*.txt       Core, laptop, Pi, AWS, optional ML dependency sets
```

```powershell
.\.venv\Scripts\python.exe -m compileall -q src scripts tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/aws_bootstrap.py
git status --short
```

Tests require only core dependencies and use temporary storage/fake publishers;
they never connect to AWS. A finite simulation checks the actual local pipeline.
Hardware, live AWS delivery, and real-model accuracy need their respective devices,
credentials, and model. TinyDB is a single-process JSON store without power-loss
durability guarantees or built-in retention. Pending records, including offline
simulation records, are sent when MQTT is enabled for the same device ID.
MQTT acknowledgment alone does not confirm the DynamoDB action succeeded.

Direct dependencies are pinned; transitive AWS/ML dependencies and Pi OS packages
are not fully locked. Record deployment-specific versions after validating
hardware and the ML runtime.
