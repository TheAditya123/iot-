# AWS cloud event database

The Raspberry Pi keeps **TinyDB as the local source of truth and MQTT outbox**.
AWS cloud persistence is an additional copy of event metadata, not a replacement
for local storage.

```text
PIR + Camera (+ BME280 later)
          |
          v
 Raspberry Pi + edge AI
          |
          v
       TinyDB
          |
          | MQTT/TLS QoS 1
          v
    AWS IoT Core
          |
          | IoT Rule
          v
     DynamoDB
    IoTEdgeEvents
```

Captured JPEGs remain on the Pi. The cloud database receives the JSON event
metadata published on `iot/room/events`.

## DynamoDB schema

Default table: `IoTEdgeEvents`

- partition key: `device_id` (String)
- sort key: `event_id` (String)
- billing: on-demand / PAY_PER_REQUEST

Every MQTT event already contains both keys, so the AWS IoT DynamoDB v2 action
can store the full JSON object without adding AWS database credentials to the Pi.

## Provision the cloud-storage leg

Install the AWS requirements and authenticate a local AWS profile as described
in `docs/aws-setup.md`, then preview:

```bash
.venv/bin/python scripts/cloud_storage_setup.py --profile iot-dev --region us-east-1
```

Apply after reviewing the preview:

```bash
.venv/bin/python scripts/cloud_storage_setup.py --profile iot-dev --region us-east-1 --apply
```

Defaults:

| Resource | Name |
|---|---|
| MQTT event topic | `iot/room/events` |
| DynamoDB table | `IoTEdgeEvents` |
| AWS IoT Rule | `RoomEventsToDynamoDB` |
| IoT Rule IAM role | `IoTEdgeEventsIoTRuleRole` |

The setup identity needs permission to describe/create the DynamoDB table,
get/create the IAM role, put the role's inline policy, pass the role to IoT,
and get/create the IoT topic rule. The Pi itself does **not** receive these IAM
permissions; it continues authenticating only with its scoped X.509 IoT device
certificate.

The script is intentionally conservative. It reuses compatible existing
resources and stops rather than silently changing an existing table or IoT Rule
whose configuration differs.

## Verify a real event

Run the normal application and trigger one real PIR event. Copy the printed
`event_id`, then from a machine with AWS CLI/profile access run:

```bash
.venv/bin/python scripts/cloud_db_test.py \
  --profile iot-dev \
  --region us-east-1 \
  --device-id room-monitor-pi \
  --event-id YOUR_EVENT_ID
```

A successful check prints `PASS` and the DynamoDB item.

## Reliability behavior

1. The Pi creates the sensor/AI event.
2. The event is written to TinyDB **before** network work.
3. MQTT publication is attempted with QoS 1.
4. After AWS acknowledges the MQTT publish, the TinyDB outbox row is marked
   published.
5. AWS IoT Core independently routes the event into DynamoDB.

If Wi-Fi or AWS is unavailable, the event stays pending in TinyDB and is retried
later. This keeps the device useful even without cloud connectivity.
