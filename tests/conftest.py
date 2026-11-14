import re

import pytest
from botocore.exceptions import ClientError


class FakeTable:
    """In-memory stand-in for a boto3 DynamoDB Table. Understands only the expressions starsort writes."""

    def __init__(self, key: str):
        self.key = key
        self.items: dict[str, dict] = {}

    def put_item(self, Item, ConditionExpression=None):
        if ConditionExpression == f"attribute_not_exists({self.key})" and Item[self.key] in self.items:
            raise self._condition_failed("PutItem")
        self.items[Item[self.key]] = dict(Item)

    def get_item(self, Key):
        item = self.items.get(Key[self.key])
        return {"Item": dict(item)} if item is not None else {}

    def update_item(self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeNames, ExpressionAttributeValues):
        item = self.items.get(Key[self.key])
        if ConditionExpression == f"attribute_exists({self.key})":
            allowed = item is not None
        else:
            name, value = re.fullmatch(r"(#\w+) = (:\w+)", ConditionExpression).groups()
            allowed = item is not None and item.get(ExpressionAttributeNames[name]) == ExpressionAttributeValues[value]
        if not allowed:
            raise self._condition_failed("UpdateItem")
        for name, value in re.findall(r"(#\w+) = (:\w+)", UpdateExpression.removeprefix("SET ")):
            item[ExpressionAttributeNames[name]] = ExpressionAttributeValues[value]

    @staticmethod
    def _condition_failed(operation: str) -> ClientError:
        return ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "condition failed"}}, operation)


@pytest.fixture
def jobs_table() -> FakeTable:
    return FakeTable(key="job_id")


@pytest.fixture
def stars_table() -> FakeTable:
    return FakeTable(key="owner_id")
