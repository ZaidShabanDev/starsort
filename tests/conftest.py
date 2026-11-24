import re

import pytest
from botocore.exceptions import ClientError


class FakeTable:
    """In-memory stand-in for a boto3 DynamoDB Table. Understands only the expressions starsort writes."""

    def __init__(self, key: str):
        self.key = key
        self.items: dict[str, dict] = {}

    def put_item(self, Item, ConditionExpression=None, ExpressionAttributeNames=None, ExpressionAttributeValues=None):
        current = self.items.get(Item[self.key])
        if not self._allowed(current, ConditionExpression, ExpressionAttributeNames or {}, ExpressionAttributeValues or {}):
            raise self._condition_failed("PutItem")
        self.items[Item[self.key]] = dict(Item)

    def get_item(self, Key):
        item = self.items.get(Key[self.key])
        return {"Item": dict(item)} if item is not None else {}

    def delete_item(self, Key):
        self.items.pop(Key[self.key], None)

    def query(self, IndexName, KeyConditionExpression, ExpressionAttributeNames, ExpressionAttributeValues):
        """A KEYS_ONLY index on one attribute, returned in a single page."""
        name, value = re.fullmatch(r"(#\w+) = (:\w+)", KeyConditionExpression).groups()
        field, wanted = ExpressionAttributeNames[name], ExpressionAttributeValues[value]
        matches = [item for item in self.items.values() if item.get(field) == wanted]
        return {"Items": [{self.key: item[self.key], field: wanted} for item in matches]}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues=None, ConditionExpression=None):
        ExpressionAttributeValues = ExpressionAttributeValues or {}
        item = self.items.get(Key[self.key])
        if not self._allowed(item, ConditionExpression, ExpressionAttributeNames, ExpressionAttributeValues):
            raise self._condition_failed("UpdateItem")
        if item is None:  # like DynamoDB, an update without a condition creates the item
            item = self.items[Key[self.key]] = dict(Key)
        action, _, assignments = UpdateExpression.partition(" ")
        if action == "SET":
            for name, value in re.findall(r"(#\w+) = (:\w+)", assignments):
                item[ExpressionAttributeNames[name]] = ExpressionAttributeValues[value]
        elif action == "ADD":
            name, value = assignments.split(" ")
            field = ExpressionAttributeNames[name]
            item[field] = item.get(field, 0) + ExpressionAttributeValues[value]
        elif action == "REMOVE":
            for name in re.findall(r"#\w+", assignments):
                item.pop(ExpressionAttributeNames[name], None)

    def _allowed(self, item, condition, names, values) -> bool:
        if condition is None:
            return True
        for part in condition.split(" AND "):
            if part == f"attribute_not_exists({self.key})":
                ok = item is None
            elif part == f"attribute_exists({self.key})":
                ok = item is not None
            else:
                name, value = re.fullmatch(r"(#\w+) = (:\w+)", part).groups()
                ok = item is not None and item.get(names[name]) == values[value]
            if not ok:
                return False
        return True

    @staticmethod
    def _condition_failed(operation: str) -> ClientError:
        return ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "condition failed"}}, operation)


@pytest.fixture
def jobs_table() -> FakeTable:
    return FakeTable(key="job_id")


@pytest.fixture
def stars_table() -> FakeTable:
    return FakeTable(key="owner_id")


@pytest.fixture
def usage_table() -> FakeTable:
    return FakeTable(key="owner_id")


@pytest.fixture
def spend_table() -> FakeTable:
    return FakeTable(key="month")
