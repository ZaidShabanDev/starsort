"""Free-trial limits: 1 run per GitHub account per week, and a monthly AI spend cap.

Standard library + boto3 only, so the API Lambda needs no dependencies layer.
"""

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError

from starsort.web.jobs import STALE_SECONDS

WEEK_SECONDS = 7 * 24 * 60 * 60
BUDGET_MESSAGE = "The free trial is paused until next month: this month's AI budget is used up."


@dataclass(frozen=True)
class Limits:
    monthly_cap_micro_usd: int
    unlimited_logins: frozenset[str]  # lowercase; the owner skips the weekly limit to test, but not the spend cap

    @classmethod
    def from_env(cls) -> "Limits":
        logins = os.environ.get("UNLIMITED_LOGINS", "")
        return cls(
            monthly_cap_micro_usd=round(float(os.environ["MONTHLY_SPEND_CAP_USD"]) * 1_000_000),
            unlimited_logins=frozenset(login.strip().lower() for login in logins.split(",") if login.strip()),
        )

    def is_unlimited(self, login: str) -> bool:
        return login.lower() in self.unlimited_logins


@dataclass(frozen=True)
class StartCheck:
    allowed: bool
    reason: str | None = None  # "running" or "weekly"
    retry_at: int | None = None  # Unix seconds when a new run is allowed


def cost_micro_usd(input_tokens: int, output_tokens: int, input_usd_per_million: float, output_usd_per_million: float) -> int:
    """$1 per million tokens is exactly 1 micro-dollar per token, so the price per million is the per-token rate."""
    return round(input_tokens * input_usd_per_million + output_tokens * output_usd_per_million)


def month_key(now: float | None = None) -> str:
    """AWS bills by UTC calendar month, so the counter does too."""
    return datetime.fromtimestamp(time.time() if now is None else now, tz=timezone.utc).strftime("%Y-%m")


def _condition_failed(error: ClientError) -> bool:
    return error.response["Error"]["Code"] == "ConditionalCheckFailedException"


def _start_check(item: dict[str, Any] | None, weekly: bool, now: int) -> StartCheck:
    last_started = int(item["last_started_at"]) if item else 0
    active_until = int(item["active_until"]) if item else 0
    if active_until > now:
        return StartCheck(False, "running", active_until)
    if weekly and last_started and now < last_started + WEEK_SECONDS:
        return StartCheck(False, "weekly", last_started + WEEK_SECONDS)
    return StartCheck(True)


class UsageStore:
    """One row per GitHub user: when their last run started, and until when a job counts as running."""

    def __init__(self, table: Any):
        self.table = table

    def check(self, owner_id: str, weekly: bool = True, now: float | None = None) -> StartCheck:
        """Read-only version of try_start, for the dashboard banner."""
        now = int(time.time() if now is None else now)
        return _start_check(self.table.get_item(Key={"owner_id": owner_id}).get("Item"), weekly, now)

    def try_start(self, owner_id: str, weekly: bool = True, now: float | None = None) -> StartCheck:
        now = int(time.time() if now is None else now)
        item = self.table.get_item(Key={"owner_id": owner_id}).get("Item")
        check = _start_check(item, weekly, now)
        if not check.allowed:
            return check

        last_started = int(item["last_started_at"]) if item else 0
        row = {
            "owner_id": owner_id,
            "last_started_at": now,
            "previous_started_at": last_started,  # put back if the run fails, so a failure doesn't use up the week
            "active_until": now + STALE_SECONDS,
        }
        if item and "last_job_id" in item:
            row["last_job_id"] = item["last_job_id"]
        # Only write if the row is still what we just read: two clicks at the same moment can't both start.
        if item is None:
            condition: dict[str, Any] = {"ConditionExpression": "attribute_not_exists(owner_id)"}
        else:
            condition = {
                "ConditionExpression": "#last = :last AND #active = :active",
                "ExpressionAttributeNames": {"#last": "last_started_at", "#active": "active_until"},
                "ExpressionAttributeValues": {":last": item["last_started_at"], ":active": item["active_until"]},
            }
        try:
            self.table.put_item(Item=row, **condition)
        except ClientError as error:
            if _condition_failed(error):
                return StartCheck(False, "running", now + STALE_SECONDS)
            raise
        return StartCheck(True)

    def remember_job(self, owner_id: str, job_id: str) -> None:
        """So GET /api/jobs/latest can find the run after a page refresh or on another device."""
        self.table.update_item(
            Key={"owner_id": owner_id},
            UpdateExpression="SET #job = :job",
            ConditionExpression="attribute_exists(owner_id)",
            ExpressionAttributeNames={"#job": "last_job_id"},
            ExpressionAttributeValues={":job": job_id},
        )

    def last_job_id(self, owner_id: str) -> str | None:
        item = self.table.get_item(Key={"owner_id": owner_id}).get("Item")
        return item.get("last_job_id") if item else None

    def finish(self, owner_id: str, refund: bool = False) -> None:
        item = self.table.get_item(Key={"owner_id": owner_id}).get("Item")
        if item is None:
            return
        fields = {"active_until": 0}
        if refund:
            fields["last_started_at"] = item["previous_started_at"]
        names = {f"#f{index}": name for index, name in enumerate(fields)}
        values = {f":v{index}": value for index, value in enumerate(fields.values())}
        self.table.update_item(
            Key={"owner_id": owner_id},
            UpdateExpression="SET " + ", ".join(f"#f{index} = :v{index}" for index in range(len(fields))),
            ConditionExpression="attribute_exists(owner_id)",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )


class SpendCounter:
    """One row per UTC month with the AI cost so far, in micro-dollars (integers, so no rounding drift)."""

    def __init__(self, table: Any):
        self.table = table

    def month_total(self, now: float | None = None) -> int:
        item = self.table.get_item(Key={"month": month_key(now)}).get("Item")
        return int(item["cost_micro_usd"]) if item else 0

    def add(self, micro_usd: int, now: float | None = None) -> None:
        # ADD is atomic, so several workers can count at the same time without losing any cost.
        self.table.update_item(
            Key={"month": month_key(now)},
            UpdateExpression="ADD #cost :amount",
            ExpressionAttributeNames={"#cost": "cost_micro_usd"},
            ExpressionAttributeValues={":amount": micro_usd},
        )
