# Basic AWS IoT + MQTT setup

Goal: give the Raspberry Pi its own AWS IoT certificate and confirm MQTT works
before running the real PIR → TinyDB → AWS application. The test script uses
`iot/setup/test`; `src.main` publishes real event metadata to `iot/room/events`.

## 1. Sign in to the AWS account

Use your own or your lab's AWS account and choose one region. These examples use
`us-east-1`; use your chosen region consistently.

If your organization uses IAM Identity Center, install
[AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
and sign in:

```powershell
aws configure sso --profile iot-dev
aws sso login --profile iot-dev
aws sts get-caller-identity --profile iot-dev
```

If your account instead provides an IAM access-key profile, configure it locally
with `aws configure --profile iot-dev`. Do not paste credentials into chat or
commit them. For already-configured default credentials, omit `--profile iot-dev`
from the bootstrap commands. A browser console login alone does not authenticate
the Python bootstrap.

The setup identity needs `sts:GetCallerIdentity` and these IoT operations:
`iot:CreateThing`, `iot:GetPolicy`, `iot:CreatePolicy`,
`iot:CreateKeysAndCertificate`, `iot:DescribeCertificate`,
`iot:AttachPolicy`, `iot:AttachThingPrincipal`, `iot:UpdateCertificate`,
and `iot:DescribeEndpoint`. Your lab administrator may need to grant them.
This setup needs no DynamoDB or IAM-role creation permissions.

## 2. Create the basic resources

From `~/iot-project` **on the Pi** (after configuring an AWS CLI profile there):

```bash
.venv/bin/python -m pip install -r requirements-aws.txt
.venv/bin/python scripts/aws_bootstrap.py --profile iot-dev --region us-east-1
.venv/bin/python scripts/aws_bootstrap.py --profile iot-dev --region us-east-1 --apply
```

The equivalent commands from `C:\iot++` on Windows are:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-aws.txt
.\.venv\Scripts\python.exe scripts/aws_bootstrap.py --profile iot-dev --region us-east-1
.\.venv\Scripts\python.exe scripts/aws_bootstrap.py --profile iot-dev --region us-east-1 --apply
```

The first run is a preview. The second creates resources in your account; normal
AWS IoT usage charges may apply.

| Item | Default |
|---|---|
| IoT Thing / MQTT client ID | `room-monitor-pi` |
| Device policy | `CameraPirMqttPolicy` |
| MQTT test topic | `iot/setup/test` |
| Application event topic | `iot/room/events` |
| Transport | TLS with client certificate, port 8883 |
| Endpoint | Your account's IoT Data-ATS hostname |

The device policy grants connect permission for one client ID and publish,
receive, and subscribe permission for only the two exact topics above. The
script creates an inactive X.509 certificate, saves its private key locally,
attaches the certificate to the Thing and policy, then activates it. It saves:

- `certs/device.cert.pem`
- `certs/device.private.key`
- `certs/AmazonRootCA1.pem`
- `certs/aws-bootstrap-state.json`
- `.env.aws`

All these files are ignored by Git. Rerun with the same arguments to reuse the
saved identity. A mismatch with existing state or policy stops for review.
Partial setup is retained for retry; there is no automatic deletion.

## 3. Prove MQTT works

```bash
.venv/bin/python scripts/mqtt_test.py
```

The test loads `.env.aws`, verifies TLS, connects, waits for AWS to acknowledge
the subscription, publishes a unique non-retained message with QoS 1, and waits
for both the publish acknowledgment and the exact same message to arrive back.

Success ends with:

```text
PASS: AWS acknowledged and returned the test message on iot/setup/test.
```

Connection, subscription, publish, or receive failures return a nonzero exit code.
No images, sensor events, database rows, or ML predictions are generated.

Optionally open **AWS IoT Core > MQTT test client** in the same region and
subscribe to `iot/setup/test` before running the command to see the JSON there,
too. The browser console test client requires its own IAM test permissions.

## If you prefer AWS Console setup

1. Create a **new Pi Thing** named `room-monitor-pi` in AWS IoT Core and create/download its
   device certificate and private key. Activate the certificate.
2. Create `CameraPirMqttPolicy` with these statements, replacing REGION and
   ACCOUNT_ID with your actual values. This example is for standard AWS regions:

   | Action | Resource ARN |
   |---|---|
   | `iot:Connect` | `arn:aws:iot:REGION:ACCOUNT_ID:client/room-monitor-pi` |
   | `iot:Publish`, `iot:Receive` | `arn:aws:iot:REGION:ACCOUNT_ID:topic/iot/setup/test` |
   | `iot:Publish`, `iot:Receive` | `arn:aws:iot:REGION:ACCOUNT_ID:topic/iot/room/events` |
   | `iot:Subscribe` | `arn:aws:iot:REGION:ACCOUNT_ID:topicfilter/iot/setup/test` |
   | `iot:Subscribe` | `arn:aws:iot:REGION:ACCOUNT_ID:topicfilter/iot/room/events` |

   Each statement uses `Effect: Allow`. Attach this policy to the certificate
   and attach the certificate to the Thing.
3. Save the certificate and key at the local paths listed above. Get
   [Amazon Root CA 1](https://www.amazontrust.com/repository/AmazonRootCA1.pem)
   and save it at `certs/AmazonRootCA1.pem`.
4. In AWS IoT Core settings, copy the device data endpoint (Data-ATS hostname).
   Copy `.env.mqtt.example` to `.env.aws` on the Pi and fill in `MQTT_ENDPOINT`:

   ```bash
   cp .env.mqtt.example .env.aws
   ```

   Put the Pi certificate, private key, and Amazon Root CA 1 at the paths named
   in `.env.aws`. If you downloaded them to Windows, transfer them to the Pi
   with `scp` over SSH. Keep the private key out of Git and set its permissions
   on the Pi with `chmod 600 certs/device.private.key`.

5. Run `scripts/mqtt_test.py`. Once credentials exist, the MQTT test doesn't
   need an AWS CLI login or a setup profile. Do not run the bootstrap over
   manually downloaded certificates; keep using this manual setup.

The resulting `.env.aws` uses separate values:

```dotenv
MQTT_TEST_TOPIC=iot/setup/test
MQTT_TOPIC=iot/room/events
```

Older `.env.aws` files containing only `MQTT_TOPIC=iot/setup/test` still work
for the connectivity test, but the application needs the event topic and policy
permission before it can publish smart-room events.

If the AWS **Connect one device** wizard generated a sample command with a
different `--client_id` (for example, `basicPubSub`), set that exact value as
`MQTT_CLIENT_ID` in `.env.aws`. Keep `DEVICE_ID` as the Thing/device name.

## Troubleshooting

- Missing `.env.aws` or TLS file: finish automatic or manual setup first.
- Connection fails: check the ATS hostname/region, active certificate, policy
  attachments, matching client ID, local clock, DNS, and outbound TCP 8883.
  Bootstrap also needs outbound HTTPS 443.
- Subscription denied: `iot:Subscribe` uses a **topicfilter** ARN.
  Publish and receive permissions use a **topic** ARN.
- Publish succeeds but receive times out: check `iot:Receive` and the exact topic.
- Wait briefly after policy changes for propagation, then rerun.
- Use one running client per client ID. The Pi should have its own Thing and
  certificate, separate from the laptop test identity.
- `--env PATH` selects another settings file; `--timeout 30` increases the
  timeout per stage. Existing process environment settings override the file.
- Existing state from the earlier DynamoDB bootstrap stops with a configuration
  mismatch. Keep its credentials/state for review; this version neither changes
  nor deletes any previously provisioned DynamoDB/rule/IAM resources.
- Existing state from the earlier one-topic MQTT bootstrap also stops with a
  configuration mismatch. Preserve its certificate, add the event-topic policy
  entries in AWS, and update `.env.aws`; do not delete a working identity.
- If certificate creation was interrupted before both local credential files
  were saved, its private key cannot be downloaded again. Inspect the certificate
  ID in state, detach/deactivate/delete that incomplete certificate in AWS, remove
  only its incomplete files and its `certificate_arn`/`certificate_id` state
  fields, then rerun. Preserve working credentials and unrelated resources.
- Keep local key files private. Unix files use mode 0600; Windows files inherit
  the directory's permissions.

## Official references

- [AWS IoT publish/subscribe policies](https://docs.aws.amazon.com/iot/latest/developerguide/pub-sub-policy.html)
- [AWS X.509 device certificates](https://docs.aws.amazon.com/iot/latest/developerguide/x509-client-certs.html)
- [Paho MQTT Python client](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html)
