"""Worker Lambda: runs one job from start to finish (fetch stars, categorize with Bedrock, render Markdown).

Unlike the API, this needs httpx, pydantic and strands, so its Lambda has the dependencies layer attached.
"""

import gzip
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import boto3

from starsort.cache import StarCache
from starsort.categorizer import Asker
from starsort.github import GitHubError
from starsort.providers import Provider, build_model, make_asker
from starsort.service import OrganizeOptions, organize
from starsort.web.jobs import JobStore
from starsort.web.limits import SpendCounter, UsageStore, cost_micro_usd

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

FAILED_MESSAGE = "Something went wrong while sorting your stars. Please try again later."
MAX_STARS = 500  # free-trial cap; also keeps a run well inside the 15-minute Lambda limit


class DynamoCacheStore:
    """The web app's CacheStore: one item per GitHub user, so later runs only send new stars to the AI."""

    def __init__(self, table: Any, owner_id: str):
        self.table = table
        self.owner_id = owner_id

    def load(self) -> StarCache | None:
        item = self.table.get_item(Key={"owner_id": self.owner_id}).get("Item")
        if item is None:
            return None
        return StarCache.model_validate_json(gzip.decompress(bytes(item["data"])))

    def save(self, cache: StarCache) -> None:
        # Gzipped because a DynamoDB item is limited to 400 KB and 500 repos of JSON can come close.
        self.table.put_item(Item={
            "owner_id": self.owner_id,
            "login": cache.username,
            "data": gzip.compress(cache.model_dump_json().encode()),
        })


@dataclass(frozen=True)
class WorkerSettings:
    client_id: str
    client_secret: str
    model_id: str
    input_usd_per_million: float
    output_usd_per_million: float


@dataclass(frozen=True)
class Tables:
    jobs: JobStore
    stars: Any
    usage: UsageStore
    spend: SpendCounter


@lru_cache(maxsize=1)
def load_settings() -> WorkerSettings:
    ssm = boto3.client("ssm")
    secret = ssm.get_parameter(Name=os.environ["GITHUB_CLIENT_SECRET_PARAM"], WithDecryption=True)["Parameter"]["Value"]
    return WorkerSettings(
        client_id=os.environ["GITHUB_CLIENT_ID"],
        client_secret=secret,
        model_id=os.environ["STARSORT_MODEL"],
        input_usd_per_million=float(os.environ["INPUT_USD_PER_MILLION"]),
        output_usd_per_million=float(os.environ["OUTPUT_USD_PER_MILLION"]),
    )


@lru_cache(maxsize=1)
def tables() -> Tables:
    dynamodb = boto3.resource("dynamodb")
    return Tables(
        jobs=JobStore(dynamodb.Table(os.environ["JOBS_TABLE"])),
        stars=dynamodb.Table(os.environ["STARS_TABLE"]),
        usage=UsageStore(dynamodb.Table(os.environ["USAGE_TABLE"])),
        spend=SpendCounter(dynamodb.Table(os.environ["SPEND_TABLE"])),
    )


def priced_ask_factory(settings: WorkerSettings, spend: SpendCounter) -> Callable[[], Asker]:
    """Bedrock asker that adds the cost of every call to this month's spend counter."""

    def record(input_tokens: int, output_tokens: int) -> None:
        spend.add(cost_micro_usd(input_tokens, output_tokens, settings.input_usd_per_million, settings.output_usd_per_million))

    return lambda: make_asker(build_model(Provider.bedrock, settings.model_id), on_usage=record)


def run_job(job_id: str, db: Tables, settings: WorkerSettings, ask_factory: Callable[[], Asker] | None = None) -> None:
    job = db.jobs.get(job_id)
    if job is None or not db.jobs.start(job_id):
        logger.warning("Skipping job %s: not found or already started.", job_id)
        return

    owner_id = job["owner_id"]
    options = OrganizeOptions(
        username=job["login"],
        app_auth=(settings.client_id, settings.client_secret),
        provider=Provider.bedrock,
        model_id=settings.model_id,
        max_stars=MAX_STARS,
    )
    try:
        result = organize(
            options,
            DynamoCacheStore(db.stars, owner_id),
            ask_factory=ask_factory or priced_ask_factory(settings, db.spend),
            log=logger.info,
            on_batch=lambda done, total: db.jobs.progress(job_id, done, total),
        )
    except GitHubError as error:
        db.jobs.fail(job_id, str(error))
        db.usage.finish(owner_id, refund=True)
        return
    except Exception:
        # Caught (not re-raised) so Lambda doesn't retry a run that would fail again and cost money twice.
        logger.exception("Job %s failed.", job_id)
        db.jobs.fail(job_id, FAILED_MESSAGE)
        db.usage.finish(owner_id, refund=True)
        return

    db.jobs.finish(job_id, {
        "markdown": result.markdown,
        "categories": {name: len(repos) for name, repos in result.categories.items()},
        "repo_count": len(result.repos),
        "new_repo_count": result.new_repo_count,
    })
    db.usage.finish(owner_id)
    logger.info("Job %s done: %d repos, %d sent to the AI.", job_id, len(result.repos), result.new_repo_count)


def handler(event: dict[str, Any], context: Any) -> None:
    """AWS Lambda entry point, invoked asynchronously by the API with {"job_id": ...}."""
    run_job(event["job_id"], tables(), load_settings())
