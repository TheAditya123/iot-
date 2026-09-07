"""Preview by default; --apply creates AWS resources and saves device credentials."""
import argparse
import json
import os
from pathlib import Path
import re
import ssl
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def secret_file(path, content):
    # Exclusive create avoids overwriting an existing device identity.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def policy_documents(partition, region, account, thing, topic, table, rule):
    prefix = f"arn:{partition}"
    table_arn = f"{prefix}:dynamodb:{region}:{account}:table/{table}"
    rule_arn = f"{prefix}:iot:{region}:{account}:rule/{rule}"
    device = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "iot:Connect", "Resource": f"{prefix}:iot:{region}:{account}:client/{thing}"},
        {"Effect": "Allow", "Action": "iot:Publish", "Resource": f"{prefix}:iot:{region}:{account}:topic/{topic}"},
    ]}
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "iot.amazonaws.com"}, "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"aws:SourceAccount": account}, "ArnEquals": {"aws:SourceArn": rule_arn}},
    }]}
    writer = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Action": "dynamodb:PutItem", "Resource": table_arn,
    }]}
    return device, trust, writer


def ensure_equal(actual, desired, name):
    if actual != desired:
        raise RuntimeError(f"Existing {name} differs from the requested configuration. Review it or choose new resource names.")


def apply(args):
    import boto3
    from botocore.exceptions import ClientError

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    iot, iam, dynamo = session.client("iot"), session.client("iam"), session.client("dynamodb")
    identity = session.client("sts").get_caller_identity()
    account, partition = identity["Account"], identity["Arn"].split(":")[1]
    expected = {"account": account, "region": args.region, "thing": args.thing,
                "topic": args.topic, "table": args.table, "rule": args.rule,
                "role": args.role, "policy": args.policy}
    state_path = ROOT / "certs" / "aws-bootstrap-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        ensure_equal(state["configuration"], expected, "bootstrap state")
    else:
        state = {"configuration": expected}
        write_json(state_path, state)
    device_policy, trust, writer = policy_documents(partition, args.region, account,
                                                   args.thing, args.topic, args.table, args.rule)
    try:
        table = dynamo.describe_table(TableName=args.table)["Table"]
    except dynamo.exceptions.ResourceNotFoundException:
        table = dynamo.create_table(
            TableName=args.table, BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": "device_id", "AttributeType": "S"},
                                  {"AttributeName": "event_id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "device_id", "KeyType": "HASH"},
                       {"AttributeName": "event_id", "KeyType": "RANGE"}],
        )["TableDescription"]
    ensure_equal(sorted(table["KeySchema"], key=lambda x: x["AttributeName"]),
                 [{"AttributeName": "device_id", "KeyType": "HASH"}, {"AttributeName": "event_id", "KeyType": "RANGE"}],
                 "DynamoDB key schema")
    attributes = {entry["AttributeName"]: entry["AttributeType"] for entry in table["AttributeDefinitions"]}
    ensure_equal({key: attributes.get(key) for key in ("device_id", "event_id")},
                 {"device_id": "S", "event_id": "S"}, "DynamoDB key types")
    dynamo.get_waiter("table_exists").wait(TableName=args.table)
    iot.create_thing(thingName=args.thing)
    try:
        existing = iot.get_policy(policyName=args.policy)
        ensure_equal(json.loads(existing["policyDocument"]), device_policy, "IoT policy")
    except iot.exceptions.ResourceNotFoundException:
        iot.create_policy(policyName=args.policy, policyDocument=json.dumps(device_policy))
    try:
        role = iam.get_role(RoleName=args.role)["Role"]
        ensure_equal(role["AssumeRolePolicyDocument"], trust, "IAM role trust policy")
    except iam.exceptions.NoSuchEntityException:
        role = iam.create_role(RoleName=args.role, AssumeRolePolicyDocument=json.dumps(trust),
                               Description="Camera PIR IoT rule writes event metadata to DynamoDB")["Role"]
    try:
        existing = iam.get_role_policy(RoleName=args.role, PolicyName="WriteIoTEvents")
        ensure_equal(existing["PolicyDocument"], writer, "IAM inline policy")
    except iam.exceptions.NoSuchEntityException:
        iam.put_role_policy(RoleName=args.role, PolicyName="WriteIoTEvents", PolicyDocument=json.dumps(writer))
    payload = {"sql": f"SELECT * FROM '{args.topic}'", "awsIotSqlVersion": "2016-03-23",
               "description": "Camera + PIR event metadata to DynamoDB", "ruleDisabled": False,
               "actions": [{"dynamoDBv2": {"roleArn": role["Arn"], "putItem": {"tableName": args.table}}}]}
    try:
        rule = iot.get_topic_rule(ruleName=args.rule)["rule"]
        ensure_equal({key: rule[key] for key in payload}, payload, "IoT rule")
    except iot.exceptions.ResourceNotFoundException:
        for attempt in range(6):
            try:
                iot.create_topic_rule(ruleName=args.rule, topicRulePayload=payload)
                break
            except ClientError as exc:
                # IAM role visibility can lag briefly after creation.
                if attempt == 5 or exc.response["Error"]["Code"] not in {"InvalidRequestException", "ServiceUnavailableException"}:
                    raise
                time.sleep(5)
    cert_path = ROOT / "certs" / "device.cert.pem"
    key_path = ROOT / "certs" / "device.private.key"
    if "certificate_arn" not in state:
        if cert_path.exists() or key_path.exists():
            raise RuntimeError("Certificate files exist without matching state; preserve them and review before rerunning")
        certificate = iot.create_keys_and_certificate(setAsActive=False)
        # Save the ID first so an interrupted write never silently creates another certificate.
        state.update(certificate_arn=certificate["certificateArn"], certificate_id=certificate["certificateId"])
        write_json(state_path, state)
        secret_file(key_path, certificate["keyPair"]["PrivateKey"])
        secret_file(cert_path, certificate["certificatePem"])
    if not cert_path.is_file() or not key_path.is_file():
        raise RuntimeError("Incomplete certificate files. See docs/aws-setup.md recovery steps; do not discard state blindly.")
    # Verify PEM parsing and that the public certificate matches the private key.
    ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_cert_chain(str(cert_path), str(key_path))
    remote_cert = iot.describe_certificate(certificateId=state["certificate_id"])["certificateDescription"]
    ensure_equal(remote_cert["certificatePem"].strip(), cert_path.read_text(encoding="utf-8").strip(), "device certificate")
    ca_path = ROOT / "certs" / "AmazonRootCA1.pem"
    if not ca_path.exists():
        with urllib.request.urlopen("https://www.amazontrust.com/repository/AmazonRootCA1.pem", timeout=30) as response:
            ca = response.read().decode("ascii")
        ssl.create_default_context(cadata=ca)
        secret_file(ca_path, ca)
    ssl.create_default_context(cafile=str(ca_path))
    iot.attach_policy(policyName=args.policy, target=state["certificate_arn"])
    iot.attach_thing_principal(thingName=args.thing, principal=state["certificate_arn"])
    iot.update_certificate(certificateId=state["certificate_id"], newStatus="ACTIVE")
    endpoint = iot.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
    state.update(endpoint=endpoint, complete=True)
    write_json(state_path, state)
    env = (f"DEVICE_ID={args.thing}\nMQTT_ENABLED=true\nMQTT_ENDPOINT={endpoint}\n"
           f"MQTT_PORT=8883\nMQTT_TOPIC={args.topic}\n"
           "MQTT_CA_CERT=certs/AmazonRootCA1.pem\nMQTT_CLIENT_CERT=certs/device.cert.pem\n"
           "MQTT_PRIVATE_KEY=certs/device.private.key\n")
    (ROOT / ".env.aws").write_text(env, encoding="utf-8")
    print(f"Ready in account {account}, region {args.region}. Credentials saved locally; no private key printed.")
    print("Use python -m src.main --env .env.aws --count 3, then verify DynamoDB in the AWS console.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile", help="Local AWS profile, including an SSO profile")
    parser.add_argument("--thing", default="room-monitor-pi")
    parser.add_argument("--topic", default="iot/room/events")
    parser.add_argument("--table", default="IoTEdgeEvents")
    parser.add_argument("--rule", default="camera_pir_to_dynamodb")
    parser.add_argument("--role", default="CameraPirIoTRuleRole")
    parser.add_argument("--policy", default="CameraPirDevicePolicy")
    parser.add_argument("--apply", action="store_true", help="Create resources in the selected AWS account")
    args = parser.parse_args()
    for name, value, pattern in [
        ("thing", args.thing, r"[A-Za-z0-9_-]{1,128}"),
        ("rule", args.rule, r"[A-Za-z0-9_]{1,128}"),
        ("role", args.role, r"[A-Za-z0-9_-]{1,64}"),
        ("policy", args.policy, r"[A-Za-z0-9_-]{1,128}"),
        ("table", args.table, r"[A-Za-z0-9_.-]{3,255}"),
        ("region", args.region, r"[a-z]{2}(?:-[a-z]+)+-\d+"),
        ("topic", args.topic, r"[A-Za-z0-9_/-]{1,256}"),
    ]:
        if not re.fullmatch(pattern, value):
            parser.error(f"Invalid {name}")
    if not args.apply:
        print(json.dumps({"mode": "PREVIEW - no AWS calls or resource changes",
                          "region": args.region, "thing": args.thing, "topic": args.topic,
                          "table": args.table, "rule": args.rule, "role": args.role,
                          "policy": args.policy, "port": 8883}, indent=2))
        print("Add --apply to create these resources. See docs/aws-setup.md for required permissions.")
        return
    apply(args)


if __name__ == "__main__":
    main()
