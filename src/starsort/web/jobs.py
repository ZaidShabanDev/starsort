"""Job records in DynamoDB: one item per "sort my stars" run.

Standard library + boto3 only, so the API Lambda needs no dependencies layer.
"""

import secrets
import time
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

KEEP_SECONDS = 30 * 24 * 60 * 60  # DynamoDB TTL deletes a job this long after it was created
STALE_SECONDS = 20 * 60  # longer than the worker's 15-minute Lambda limit, so a job this quiet has died
RESULT_FIELDS = ("markdown", "categories", "repo_count", "new_repo_count")
PUBLIC_FIELDS = ("job_id", "login", "status", "created_at", "updated_at", "batches_done", "batches_total", "error", *RESULT_FIELDS)


class JobStore:
    def __init__(self, table: Any):
        self.table = table

    def create(self, owner_id: str, login: str, now: float | None = None) -> dict[str, Any]:
        now = int(time.time() if now is None else now)
        job = {
            "job_id": secrets.token_urlsafe(16),
            "owner_id": owner_id,
            "login": login,
            "status": QUEUED,
            "created_at": now,
            "updated_at": now,
            "batches_done": 0,
            "batches_total": 0,
            "expires_at": now + KEEP_SECONDS,
        }
        self.table.put_item(Item=job, ConditionExpression="attribute_not_exists(job_id)")
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self.table.get_item(Key={"job_id": job_id}).get("Item")

    def start(self, job_id: str) -> bool:
        """Queued -> running. False if the job already started, so a repeated event never runs it twice."""
        return self._update(job_id, {"status": RUNNING}, only_if_status=QUEUED)

    def progress(self, job_id: str, batches_done: int, batches_total: int) -> None:
        self._update(job_id, {"batches_done": batches_done, "batches_total": batches_total})

    def finish(self, job_id: str, result: dict[str, Any]) -> None:
        self._update(job_id, {**{field: result[field] for field in RESULT_FIELDS}, "status": DONE})

    def fail(self, job_id: str, message: str) -> None:
        self._update(job_id, {"status": FAILED, "error": message})

    def _update(self, job_id: str, fields: dict[str, Any], only_if_status: str | None = None) -> bool:
        fields = {**fields, "updated_at": int(time.time())}
        # Placeholders for every name, because words like "status" are reserved in DynamoDB expressions.
        names = {f"#f{index}": name for index, name in enumerate(fields)}
        values = {f":v{index}": value for index, value in enumerate(fields.values())}
        if only_if_status:
            names["#expected"] = "status"
            values[":expected"] = only_if_status
            condition = "#expected = :expected"
        else:
            condition = "attribute_exists(job_id)"
        try:
            self.table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET " + ", ".join(f"#f{index} = :v{index}" for index in range(len(fields))),
                ConditionExpression=condition,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True


def _plain(value: Any) -> Any:
    """DynamoDB returns numbers as Decimal, which json.dumps can't write."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def public_view(job: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """What the browser may see: no owner_id, and a dead job (Lambda timeout or crash) shows as failed."""
    now = time.time() if now is None else now
    view = {field: _plain(job[field]) for field in PUBLIC_FIELDS if field in job}
    if view["status"] in (QUEUED, RUNNING) and now - view["updated_at"] > STALE_SECONDS:
        view["status"] = FAILED
        view["error"] = "The job stopped without finishing. Please try again."
    return view
