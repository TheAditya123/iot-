# Camera + PIR project — AWS/MQTT setup first

Current milestone: get a basic MQTT connection working with AWS before the hardware arrives.

```text
Laptop -> MQTT over TLS -> AWS IoT Core -> same test message back to laptop
```

The setup creates an IoT Thing, device certificate, a policy for one test topic,
and a local connection settings file. The test subscribes, publishes one unique
"hello from laptop" message, and exits successfully only after AWS acknowledges
it and delivers that same message back.

## Get started

From PowerShell in `C:\iot++` (Python 3.11+):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-aws.txt
```

Configure your AWS login using [the setup guide](docs/aws-setup.md), then:

```powershell
# Preview: no AWS changes.
.\.venv\Scripts\python.exe scripts/aws_bootstrap.py --profile iot-dev --region us-east-1

# Create the basic AWS IoT resources and save local certificates/settings.
.\.venv\Scripts\python.exe scripts/aws_bootstrap.py --profile iot-dev --region us-east-1 --apply

# Connect, subscribe, publish, and receive one test message.
.\.venv\Scripts\python.exe scripts/mqtt_test.py
```

Expected result:

```text
Connected securely to AWS IoT.
PASS: AWS acknowledged and returned the test message on iot/setup/test.
```

An AWS account/login and valid device certificates are required for that live
result. Installing the repo or passing offline tests does not establish an AWS
connection. If you create the resources in AWS Console instead, the guide covers
the same setup using `.env.mqtt.example`.

## What's included now

- `scripts/aws_bootstrap.py`: basic AWS IoT Core setup, with a preview mode.
- `scripts/mqtt_test.py`: a standalone connection test with no sensor, model,
  image capture, or database dependencies.
- `.env.mqtt.example`: the few connection settings needed for a manual setup.
- `requirements-aws.txt`: bootstrap and MQTT dependencies.
- `requirements-mqtt.txt`: MQTT dependencies only, for already-provisioned devices.
- `docs/aws-setup.md`: login, setup, expected result, and troubleshooting.

Certificates, private keys, `.env.aws`, and local setup state stay out of Git.
The device policy permits one client ID and one exact topic. The setup doesn't
create DynamoDB, IoT rules, IAM roles, or S3 storage.

## Later, when the hardware arrives

Choose the actual camera/PIR behavior, wire the hardware, and then implement
processing, ML, local storage, and cloud storage as needed. The earlier `src/`,
`models/`, `.env.example`, and laptop/Pi/ML requirements are saved as deferred
scaffolding; they are not part of this milestone. We are not extending that logic
now. Use `scripts/mqtt_test.py` for the current connection check.

## Offline verification

```powershell
.\.venv\Scripts\python.exe -m compileall -q src scripts tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_mqtt_setup.py -v
```

These tests cover MQTT success/failure behavior and scoped setup permissions
without making AWS calls. The older pipeline tests additionally require
`requirements.txt`. Only the live MQTT test proves cloud connectivity.
