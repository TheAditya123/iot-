"""Prepare AWS IoT Core for a basic MQTT connection test; preview by default."""
import argparse
import json
import os
from pathlib import Path
import re
import ssl
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


def device_policy(partition, region, account, thing, topics):
    prefix = f"arn:{partition}:iot:{region}:{account}"
    if isinstance(topics, str):
        topics = [topics]
    topic_resources = [f"{prefix}:topic/{topic}" for topic in topics]
    filter_resources = [f"{prefix}:topicfilter/{topic}" for topic in topics]
    topic_resources = topic_resources[0] if len(topic_resources) == 1 else topic_resources
    filter_resources = filter_resources[0] if len(filter_resources) == 1 else filter_resources
    return {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "iot:Connect", "Resource": f"{prefix}:client/{thing}"},
        {"Effect": "Allow", "Action": ["iot:Publish", "iot:Receive"], "Resource": topic_resources},
        {"Effect": "Allow", "Action": "iot:Subscribe", "Resource": filter_resources},
    ]}


def ensure_equal(actual, desired, name):
    if actual != desired:
        raise RuntimeError(f"Existing {name} differs from the requested configuration. Review it or choose new resource names.")


def apply(args):
    import boto3
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    iot = session.client("iot")
    identity = session.client("sts").get_caller_identity()
    account, partition = identity["Account"], identity["Arn"].split(":")[1]
    expected = {"account": account, "region": args.region, "thing": args.thing,
                "test_topic": args.topic, "event_topic": args.event_topic,
                "policy": args.policy}
    state_path = ROOT / "certs" / "aws-bootstrap-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        ensure_equal(state["configuration"], expected, "bootstrap state")
    else:
        state = {"configuration": expected}
        write_json(state_path, state)
    policy = device_policy(partition, args.region, account, args.thing,
                           [args.topic, args.event_topic])
    iot.create_thing(thingName=args.thing)
    try:
        existing = iot.get_policy(policyName=args.policy)
        ensure_equal(json.loads(existing["policyDocument"]), policy, "IoT policy")
    except iot.exceptions.ResourceNotFoundException:
        iot.create_policy(policyName=args.policy, policyDocument=json.dumps(policy))
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
    env = (f"DEVICE_ID={args.thing}\nMQTT_ENDPOINT={endpoint}\n"
           f"MQTT_PORT=8883\nMQTT_TEST_TOPIC={args.topic}\n"
           f"MQTT_TOPIC={args.event_topic}\n"
           "MQTT_CA_CERT=certs/AmazonRootCA1.pem\nMQTT_CLIENT_CERT=certs/device.cert.pem\n"
           "MQTT_PRIVATE_KEY=certs/device.private.key\n")
    (ROOT / ".env.aws").write_text(env, encoding="utf-8")
    print(f"Ready in account {account}, region {args.region}. Credentials saved locally; no private key printed.")
    print("Next: python scripts/mqtt_test.py (verifies publish and receive through AWS IoT).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile", help="Local AWS profile, including an SSO profile")
    parser.add_argument("--thing", default="room-monitor-pi")
    parser.add_argument("--topic", default="iot/setup/test")
    parser.add_argument("--event-topic", default="iot/room/events")
    parser.add_argument("--policy", default="CameraPirMqttPolicy")
    parser.add_argument("--apply", action="store_true", help="Create resources in the selected AWS account")
    args = parser.parse_args()
    for name, value, pattern in [
        ("thing", args.thing, r"[A-Za-z0-9_-]{1,128}"),
        ("policy", args.policy, r"[A-Za-z0-9_-]{1,128}"),
        ("region", args.region, r"[a-z]{2}(?:-[a-z]+)+-\d+"),
        ("topic", args.topic, r"[A-Za-z0-9_/-]{1,256}"),
        ("event topic", args.event_topic, r"[A-Za-z0-9_/-]{1,256}"),
    ]:
        if not re.fullmatch(pattern, value):
            parser.error(f"Invalid {name}")
    if not args.apply:
        print(json.dumps({"mode": "PREVIEW - no AWS calls or resource changes",
                          "region": args.region, "thing": args.thing, "topic": args.topic,
                          "event_topic": args.event_topic, "policy": args.policy,
                          "port": 8883}, indent=2))
        print("Add --apply to create these resources. See docs/aws-setup.md for required permissions.")
        return
    from botocore.exceptions import BotoCoreError, ClientError
    try:
        apply(args)
    except (BotoCoreError, ClientError, OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"Setup failed: {exc}\nSee docs/aws-setup.md for AWS login and setup steps.\n")


if __name__ == "__main__":
    main()
