"""Verify that one known MQTT event was persisted in DynamoDB."""
import argparse
import json
import time

from boto3.dynamodb.types import TypeDeserializer


def deserialize_item(item):
    decoder = TypeDeserializer()
    return {key: decoder.deserialize(value) for key, value in item.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile", help="AWS CLI/SSO profile")
    parser.add_argument("--table", default="IoTEdgeEvents")
    parser.add_argument("--device-id", default="room-monitor-pi")
    parser.add_argument("--event-id", required=True,
                        help="event_id printed by the Pi for the event being checked")
    parser.add_argument("--wait-seconds", type=float, default=15)
    args = parser.parse_args()

    import boto3
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    dynamodb = session.client("dynamodb")
    deadline = time.monotonic() + max(args.wait_seconds, 0)

    while True:
        result = dynamodb.get_item(
            TableName=args.table,
            Key={
                "device_id": {"S": args.device_id},
                "event_id": {"S": args.event_id},
            },
            ConsistentRead=True,
        )
        if "Item" in result:
            print("PASS: event is present in DynamoDB.")
            print(json.dumps(deserialize_item(result["Item"]), indent=2, default=str))
            return 0
        if time.monotonic() >= deadline:
            print("FAIL: event was not found in DynamoDB before the timeout.")
            return 1
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
