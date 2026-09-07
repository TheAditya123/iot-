# AWS IoT Core and MQTT setup

The repository does not create AWS resources merely by being installed. The
bootstrap previews by default; `--apply` creates resources and may incur usage
charges. No credentials belong in source control.

## 1. Configure your account once

Install AWS CLI v2 from https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html.
Use your lab/account's provided access method. For IAM Identity Center:

```powershell
aws configure sso --profile iot-dev
aws sso login --profile iot-dev
aws sts get-caller-identity --profile iot-dev
```

For an IAM access-key profile instead, use `aws configure --profile iot-dev`.
Do not use root-account keys. The profile stays in your local AWS configuration,
outside the repository. Pick one region for IoT Core, the rule, and DynamoDB.
The examples use `us-east-1`; change every command consistently if necessary.

The setup identity needs these AWS API permissions (your lab admin may need to
grant them). AWS IoT policy permissions are separate from IAM setup permissions.

| Service | Setup operations |
|---|---|
| STS | `sts:GetCallerIdentity` |
| IoT | `iot:CreateThing`, `iot:GetPolicy`, `iot:CreatePolicy`, `iot:CreateKeysAndCertificate`, `iot:DescribeCertificate`, `iot:AttachPolicy`, `iot:AttachThingPrincipal`, `iot:UpdateCertificate`, `iot:DescribeEndpoint`, `iot:GetTopicRule`, `iot:CreateTopicRule` |
| DynamoDB | `dynamodb:DescribeTable`, `dynamodb:CreateTable` |
| IAM | `iam:GetRole`, `iam:CreateRole`, `iam:GetRolePolicy`, `iam:PutRolePolicy`, `iam:PassRole` |

Scope `iam:PassRole` to `CameraPirIoTRuleRole` with
`iam:PassedToService = iot.amazonaws.com`. Scope other operations to the named
resources wherever the operation supports it; creation/discovery operations may
require `Resource: "*"`. The console MQTT test client and table viewer require
additional read/test permissions; these are not used by the bootstrap itself.

## 2. Preview and create

From `C:\iot++`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-aws.txt
.\.venv\Scripts\python.exe scripts/aws_bootstrap.py --profile iot-dev --region us-east-1
.\.venv\Scripts\python.exe scripts/aws_bootstrap.py --profile iot-dev --region us-east-1 --apply
```

This creates or reuses matching resources:

| Resource | Default / behavior |
|---|---|
| IoT Thing and MQTT client ID | `room-monitor-pi` |
| Device policy | `CameraPirDevicePolicy`: connect as that client, publish only to `iot/room/events` |
| X.509 certificate | Created inactive, saved locally, attached, then activated |
| ATS endpoint | Discovered for the selected account/region |
| DynamoDB table | `IoTEdgeEvents`, on-demand capacity; string partition key `device_id`, string sort key `event_id` |
| IAM rule role | `CameraPirIoTRuleRole`, `dynamodb:PutItem` on this table only; trust restricted to account and rule ARN |
| IoT rule | `camera_pir_to_dynamodb`, SQL `SELECT * FROM 'iot/room/events'`, DynamoDBv2 action |

The script saves `certs/device.cert.pem`, `certs/device.private.key`,
`certs/AmazonRootCA1.pem`, `certs/aws-bootstrap-state.json`, and `.env.aws`.
All are ignored by Git. Keep the private key and state secure and retain them
for reruns. Unix key files use mode 0600; on Windows they inherit directory ACLs,
so keep the directory accessible only to your account as appropriate.

Rerunning with identical configuration reuses the saved certificate. Existing
policy/rule/schema/trust mismatches cause an error for review. The script does
not roll back partial AWS setup or delete resources. Allow a short delay for IAM
and IoT policy propagation, then rerun the same command after transient failures.
Keep resource names unique to this project; do not point the script at shared
production resources.

If creation stops after a certificate ID is saved but before both credential
files are saved, the private key cannot be downloaded again. In AWS IoT,
inspect the certificate ID in state, detach any policies and Thing association,
deactivate and delete that incomplete certificate. Then remove only that
certificate's local files and the `certificate_arn`/`certificate_id` fields from
state, and rerun. Do not discard working credentials or unrelated resources.

## 3. Verify MQTT and DynamoDB

1. In the same region, open **AWS IoT Core > MQTT test client** and subscribe to
   `iot/room/events` before running the app. No separate broker is needed.
2. Run:

   ```powershell
   .\.venv\Scripts\python.exe -m src.main --env .env.aws --count 3
   ```

   This uses simulated PIR/camera and stub inference unless environment variables
   override those defaults. `--env .env.aws` loads that file instead of `.env`.
   For ongoing webcam use, merge the generated MQTT settings into `.env` and set
   `CAMERA_BACKEND=webcam`, then run without `--env`.
3. Confirm received JSON contains `device_id`, `event_id`, `timestamp`, `motion`,
   `prediction`, `confidence`, and simulation/backend markers.
4. Open **DynamoDB > Tables > IoTEdgeEvents > Explore table items**. Locate the
   same `event_id`. Only metadata is sent; `image_path` refers to a local file.
5. Stop with Ctrl+C. Disconnect Wi-Fi during a longer run to check that events
   remain locally pending and replay after connectivity returns.

TLS uses the ATS hostname, port **8883**, Amazon root CA, and a device certificate
and private key. Hostname and certificate verification remain enabled. Your
network must permit outbound TCP 8883 and DNS; bootstrap also needs HTTPS 443.
The device ID must equal the policy's permitted client ID. Two concurrent clients
with that same ID can disconnect each other.

MQTT QoS 1 acknowledges delivery to the broker, **not successful DynamoDB writes**.
The app marks records published after broker acknowledgment. Rule/IAM failures
must be diagnosed in AWS; enable IoT logging/monitoring if needed. A replay uses
the original event ID, so duplicate deliveries overwrite the same table item.
For guaranteed cloud processing acknowledgment, add an application ACK topic
and error handling before relying on this starter for production.

## Troubleshooting and cleanup

- No MQTT messages: check region/ATS hostname, active certificate and attachments,
  matching client ID/topic, local clock, certificate paths, firewall, and Wi-Fi.
- MQTT works but table stays empty: check the rule is enabled, SQL topic and key
  fields match, and the role trust and `PutItem` permission are correct.
- AccessDenied in bootstrap: the IAM setup profile lacks an operation above;
  a device certificate cannot create cloud resources.
- Backlog/image growth: TinyDB and images have no retention policy in this
  starter. Archive/remove old local data while the app is stopped, after deciding
  what must be retained. Run only one writer per local TinyDB file.
- When finished with the lab: stop publishers, disable/delete the IoT rule,
  detach the device policy and Thing principal, deactivate/delete the certificate,
  delete the dedicated Thing/policy, and delete the dedicated IAM inline policy
  and role. Delete the DynamoDB table only after exporting anything needed.
  Consult saved state for exact names/IDs. Deletion is intentionally manual.

## Official references

- AWS DynamoDBv2 rule action: https://docs.aws.amazon.com/iot/latest/developerguide/dynamodb-v2-rule-action.html
- AWS rule API: https://docs.aws.amazon.com/boto3/latest/reference/services/iot/client/create_topic_rule.html
- AWS device authentication: https://docs.aws.amazon.com/iot/latest/developerguide/x509-client-certs.html
- Paho MQTT client/TLS API: https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html
