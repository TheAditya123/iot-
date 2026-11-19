"""Provision AWS IoT Core -> DynamoDB event persistence; preview by default."""
import argparse
import json
import re
import time


def role_trust_policy():
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "iot.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }


def dynamodb_write_policy(table_arn):
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": "dynamodb:PutItem",
            "Resource": table_arn,
        }],
    }


def topic_rule_payload(topic, table_name, role_arn):
    return {
        "sql": f"SELECT * FROM '{topic}'",
        "description": "Persist IoT++ smart-room MQTT event metadata in DynamoDB",
        "actions": [{
            "dynamoDBv2": {
                "roleArn": role_arn,
                "putItem": {"tableName": table_name},
            }
        }],
        "ruleDisabled": False,
        "awsIotSqlVersion": "2016-03-23",
    }


def validate_table(table):
    keys = {entry["KeyType"]: entry["AttributeName"] for entry in table["KeySchema"]}
    attributes = {entry["AttributeName"]: entry["AttributeType"]
                  for entry in table["AttributeDefinitions"]}
    if keys != {"HASH": "device_id", "RANGE": "event_id"}:
        raise RuntimeError("Existing DynamoDB table has a different key schema")
    if attributes.get("device_id") != "S" or attributes.get("event_id") != "S":
        raise RuntimeError("Existing DynamoDB keys must both be strings")


def apply(args):
    import boto3

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    sts = session.client("sts")
    iot = session.client("iot")
    iam = session.client("iam")
    dynamodb = session.client("dynamodb")

    identity = sts.get_caller_identity()
    account = identity["Account"]
    partition = identity["Arn"].split(":")[1]
    table_arn = f"arn:{partition}:dynamodb:{args.region}:{account}:table/{args.table}"

    try:
        table = dynamodb.describe_table(TableName=args.table)["Table"]
        validate_table(table)
        print(f"Reusing DynamoDB table {args.table}.")
    except dynamodb.exceptions.ResourceNotFoundException:
        dynamodb.create_table(
            TableName=args.table,
            KeySchema=[
                {"AttributeName": "device_id", "KeyType": "HASH"},
                {"AttributeName": "event_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "device_id", "AttributeType": "S"},
                {"AttributeName": "event_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        print(f"Creating DynamoDB table {args.table}...")
        dynamodb.get_waiter("table_exists").wait(TableName=args.table)

    role_created = False
    try:
        role = iam.get_role(RoleName=args.role)["Role"]
    except iam.exceptions.NoSuchEntityException:
        role = iam.create_role(
            RoleName=args.role,
            AssumeRolePolicyDocument=json.dumps(role_trust_policy()),
            Description="Allows AWS IoT Core to persist IoT++ events to DynamoDB",
        )["Role"]
        role_created = True
    role_arn = role["Arn"]
    iam.put_role_policy(
        RoleName=args.role,
        PolicyName="WriteIoTEdgeEvents",
        PolicyDocument=json.dumps(dynamodb_write_policy(table_arn)),
    )
    if role_created:
        time.sleep(5)

    desired = topic_rule_payload(args.topic, args.table, role_arn)
    try:
        current = iot.get_topic_rule(ruleName=args.rule)["rule"]
    except iot.exceptions.ResourceNotFoundException:
        iot.create_topic_rule(ruleName=args.rule, topicRulePayload=desired)
        print(f"Created IoT Rule {args.rule}.")
    else:
        comparable = {
            "sql": current.get("sql"),
            "actions": current.get("actions"),
            "ruleDisabled": current.get("ruleDisabled", False),
            "awsIotSqlVersion": current.get("awsIotSqlVersion", "2016-03-23"),
        }
        expected = {key: desired[key] for key in comparable}
        if comparable != expected:
            raise RuntimeError(
                f"Existing IoT Rule {args.rule} differs from the requested configuration; review it before changing it"
            )
        print(f"Reusing IoT Rule {args.rule}.")

    print(f"Cloud persistence ready: {args.topic} -> AWS IoT Rule -> {args.table}.")
    print("TinyDB remains the Pi's local source of truth/outbox.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile", help="AWS CLI/SSO profile")
    parser.add_argument("--topic", default="iot/room/events")
    parser.add_argument("--table", default="IoTEdgeEvents")
    parser.add_argument("--role", default="IoTEdgeEventsIoTRuleRole")
    parser.add_argument("--rule", default="RoomEventsToDynamoDB")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    checks = [
        ("region", args.region, r"[a-z]{2}(?:-[a-z]+)+-\d+"),
        ("topic", args.topic, r"[A-Za-z0-9_/-]{1,256}"),
        ("table", args.table, r"[A-Za-z0-9_.-]{3,255}"),
        ("role", args.role, r"[A-Za-z0-9_+=,.@-]{1,64}"),
        ("rule", args.rule, r"[A-Za-z0-9_]{1,128}"),
    ]
    for name, value, pattern in checks:
        if not re.fullmatch(pattern, value):
            parser.error(f"Invalid {name}")

    if not args.apply:
        print(json.dumps({
            "mode": "PREVIEW - no AWS calls or changes",
            "region": args.region,
            "mqtt_topic": args.topic,
            "dynamodb_table": args.table,
            "iot_rule": args.rule,
            "iam_role": args.role,
            "keys": {"partition": "device_id", "sort": "event_id"},
        }, indent=2))
        print("Add --apply after reviewing the preview.")
        return

    from botocore.exceptions import BotoCoreError, ClientError
    try:
        apply(args)
    except (BotoCoreError, ClientError, OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"Cloud storage setup failed: {exc}\nSee docs/cloud-database.md.\n")


if __name__ == "__main__":
    main()
