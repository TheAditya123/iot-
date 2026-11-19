"""Offline unit checks for the AWS IoT -> DynamoDB setup helpers."""
import unittest

from scripts import cloud_storage_setup


class CloudStorageSetupTests(unittest.TestCase):
    def test_rule_routes_exact_event_topic_to_table(self):
        payload = cloud_storage_setup.topic_rule_payload(
            "iot/room/events", "IoTEdgeEvents",
            "arn:aws:iam::123456789012:role/IoTEdgeEventsIoTRuleRole",
        )
        self.assertEqual(payload["sql"], "SELECT * FROM 'iot/room/events'")
        action = payload["actions"][0]["dynamoDBv2"]
        self.assertEqual(action["putItem"]["tableName"], "IoTEdgeEvents")
        self.assertEqual(
            action["roleArn"],
            "arn:aws:iam::123456789012:role/IoTEdgeEventsIoTRuleRole",
        )

    def test_role_policy_can_only_put_items_in_one_table(self):
        arn = "arn:aws:dynamodb:us-east-1:123456789012:table/IoTEdgeEvents"
        policy = cloud_storage_setup.dynamodb_write_policy(arn)
        statement = policy["Statement"][0]
        self.assertEqual(statement["Action"], "dynamodb:PutItem")
        self.assertEqual(statement["Resource"], arn)
        self.assertNotIn("*", statement["Resource"])

    def test_table_schema_requires_device_and_event_keys(self):
        table = {
            "KeySchema": [
                {"AttributeName": "device_id", "KeyType": "HASH"},
                {"AttributeName": "event_id", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "device_id", "AttributeType": "S"},
                {"AttributeName": "event_id", "AttributeType": "S"},
            ],
        }
        cloud_storage_setup.validate_table(table)
        table["KeySchema"][1]["AttributeName"] = "timestamp"
        with self.assertRaises(RuntimeError):
            cloud_storage_setup.validate_table(table)


if __name__ == "__main__":
    unittest.main()
