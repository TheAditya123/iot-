"""Offline checks for setup and the one-message MQTT test; never contact AWS."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from scripts import aws_bootstrap, mqtt_test


class FakeClient:
    """Exercise acknowledgments and callbacks without a network connection."""
    def __init__(self, mode="success"):
        self.mode = mode
        self.closed = False
        self.sent = []

    def tls_set_context(self, context):
        pass

    def connect(self, *args, **kwargs):
        pass

    def loop_start(self):
        self.on_connect(self, None, None, 5 if self.mode == "connection_denied" else 0, None)

    def subscribe(self, topic, qos):
        self.on_subscribe(self, None, 1, [SimpleNamespace(is_failure=self.mode == "subscription_denied")], None)
        return 0, 1

    def publish(self, topic, payload, qos, retain):
        self.sent.append((topic, payload, qos, retain))
        if self.mode != "no_receive":
            delivered = b'{"old": "message"}' if self.mode == "wrong_message" else payload
            self.on_message(self, None, SimpleNamespace(topic=topic, payload=delivered))
        return SimpleNamespace(rc=0, wait_for_publish=lambda timeout: None,
                               is_published=lambda: self.mode != "no_ack")

    def disconnect(self):
        self.closed = True

    def loop_stop(self):
        pass


class MqttSetupTests(unittest.TestCase):
    def test_device_permissions_use_exact_topic_and_topicfilter(self):
        policy = aws_bootstrap.device_policy("aws", "us-east-1", "123456789012", "pi", "iot/setup/test")
        statements = policy["Statement"]
        self.assertEqual(statements[0]["Resource"], "arn:aws:iot:us-east-1:123456789012:client/pi")
        self.assertEqual(statements[1]["Action"], ["iot:Publish", "iot:Receive"])
        self.assertEqual(statements[1]["Resource"], "arn:aws:iot:us-east-1:123456789012:topic/iot/setup/test")
        self.assertEqual(statements[2]["Action"], "iot:Subscribe")
        self.assertEqual(statements[2]["Resource"], "arn:aws:iot:us-east-1:123456789012:topicfilter/iot/setup/test")
        self.assertNotIn("*", json.dumps(policy))

    def exercise(self, mode):
        client = FakeClient(mode)
        settings = mqtt_test.Settings("pi", "example-ats.iot.us-east-1.amazonaws.com",
                                      "iot/setup/test", Path("ca"), Path("cert"), Path("key"), timeout=0.01)
        output = io.StringIO()
        try:
            with patch.object(mqtt_test.mqtt, "Client", return_value=client), \
                    patch.object(mqtt_test.ssl, "create_default_context"), redirect_stdout(output):
                result = mqtt_test.run_test(settings)
                self.assertEqual(result["message"], "hello from device")
        finally:
            self.assertTrue(client.closed)
            if mode != "success":
                self.assertNotIn("PASS:", output.getvalue())
        return client, output.getvalue()

    def test_success_requires_ack_and_returned_message(self):
        client, output = self.exercise("success")
        self.assertIn("PASS:", output)
        self.assertEqual(len(client.sent), 1)
        self.assertEqual(client.sent[0][2:], (1, False))

    def test_publish_ack_without_receive_does_not_pass(self):
        with self.assertRaises(TimeoutError):
            self.exercise("no_receive")

    def test_unrelated_message_does_not_pass(self):
        with self.assertRaises(TimeoutError):
            self.exercise("wrong_message")

    def test_receive_without_publish_ack_does_not_pass(self):
        with self.assertRaises(TimeoutError):
            self.exercise("no_ack")

    def test_connection_denial_fails(self):
        with self.assertRaisesRegex(RuntimeError, "rejected the MQTT connection"):
            self.exercise("connection_denied")

    def test_subscription_denial_fails(self):
        with self.assertRaisesRegex(RuntimeError, "rejected the subscription"):
            self.exercise("subscription_denied")

    def test_missing_config_explains_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Run the AWS bootstrap"):
                mqtt_test.Settings.load(Path(directory) / ".env.aws", 1)

    def test_bootstrap_uses_only_iot_and_sts_and_reuses_certificate(self):
        class Missing(Exception):
            pass

        args = SimpleNamespace(profile=None, region="us-east-1", thing="pi",
                               topic="iot/setup/test", policy="TestMqttPolicy")
        iot, sts, session = MagicMock(), MagicMock(), MagicMock()
        iot.exceptions.ResourceNotFoundException = Missing
        sts.get_caller_identity.return_value = {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/test"}
        session.client.side_effect = lambda service: {"iot": iot, "sts": sts}[service]
        policy = aws_bootstrap.device_policy("aws", args.region, "123456789012", args.thing, args.topic)
        iot.get_policy.side_effect = [Missing(), {"policyDocument": json.dumps(policy)}]
        iot.create_keys_and_certificate.return_value = {
            "certificateArn": "arn:aws:iot:us-east-1:123456789012:cert/test",
            "certificateId": "test", "certificatePem": "FAKE CERT FOR OFFLINE TEST",
            "keyPair": {"PrivateKey": "FAKE KEY FOR OFFLINE TEST"},
        }
        iot.describe_certificate.return_value = {"certificateDescription": {"certificatePem": "FAKE CERT FOR OFFLINE TEST"}}
        iot.describe_endpoint.return_value = {"endpointAddress": "example-ats.iot.us-east-1.amazonaws.com"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(aws_bootstrap, "ROOT", root), \
                    patch("boto3.Session", return_value=session), \
                    patch.object(aws_bootstrap.ssl, "SSLContext"), \
                    patch.object(aws_bootstrap.ssl, "create_default_context"), \
                    patch.object(aws_bootstrap.urllib.request, "urlopen") as download, redirect_stdout(io.StringIO()):
                download.return_value.__enter__.return_value.read.return_value = b"FAKE CA FOR OFFLINE TEST"
                aws_bootstrap.apply(args)
                aws_bootstrap.apply(args)
            iot.create_keys_and_certificate.assert_called_once_with(setAsActive=False)
            iot.create_policy.assert_called_once()
            self.assertEqual({call.args[0] for call in session.client.call_args_list}, {"iot", "sts"})
            config = (root / ".env.aws").read_text(encoding="utf-8")
            self.assertIn("MQTT_TOPIC=iot/setup/test", config)
            self.assertNotIn("FAKE KEY", config)
            state = json.loads((root / "certs/aws-bootstrap-state.json").read_text(encoding="utf-8"))
            self.assertTrue(state["complete"])


if __name__ == "__main__":
    unittest.main()
