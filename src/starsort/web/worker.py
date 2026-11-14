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
from starsort.providers import Provider
from starsort.service import OrganizeOptions, organize
from starsort.web.jobs import JobStore

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

FAILED_MESSAGE = "Something went wrong while sorting your stars. Please try again later."


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


@lru_cache(maxsize=1)
def load_settings() -> WorkerSettings:
    ssm = boto3.client("ssm")
    secret = ssm.get_parameter(Name=os.environ["GITHUB_CLIENT_SECRET_PARAM"], WithDecryption=True)["Parameter"]["Value"]
    return WorkerSettings(client_id=os.environ["GITHUB_CLIENT_ID"], client_secret=secret, model_id=os.environ["STARSORT_MODEL"])


@lru_cache(maxsize=1)
def tables() -> tuple[JobStore, Any]:
    dynamodb = boto3.resource("dynamodb")
    return JobStore(dynamodb.Table(os.environ["JOBS_TABLE"])), dynamodb.Table(os.environ["STARS_TABLE"])


def run_job(
    job_id: str,
    jobs: JobStore,
    stars_table: Any,
    settings: WorkerSettings,
    ask_factory: Callable[[], Asker] | None = None,
) -> None:
    job = jobs.get(job_id)
    if job is None or not jobs.start(job_id):
        logger.warning("Skipping job %s: not found or already started.", job_id)
        return

    options = OrganizeOptions(
        username=job["login"],
        app_auth=(settings.client_id, settings.client_secret),
        provider=Provider.bedrock,
        model_id=settings.model_id,
    )
    try:
        result = organize(
            options,
            DynamoCacheStore(stars_table, job["owner_id"]),
            ask_factory=ask_factory,
            log=logger.info,
            on_batch=lambda done, total: jobs.progress(job_id, done, total),
        )
    except GitHubError as error:
        jobs.fail(job_id, str(error))
        return
    except Exception:
        # Caught (not re-raised) so Lambda doesn't retry a run that would fail again and cost money twice.
        logger.exception("Job %s failed.", job_id)
        jobs.fail(job_id, FAILED_MESSAGE)
        return

    jobs.finish(job_id, {
        "markdown": result.markdown,
        "categories": {name: len(repos) for name, repos in result.categories.items()},
        "repo_count": len(result.repos),
        "new_repo_count": result.new_repo_count,
    })
    logger.info("Job %s done: %d repos, %d sent to the AI.", job_id, len(result.repos), result.new_repo_count)


def handler(event: dict[str, Any], context: Any) -> None:
    """AWS Lambda entry point, invoked asynchronously by the API with {"job_id": ...}."""
    jobs, stars_table = tables()
    run_job(event["job_id"], jobs, stars_table, load_settings())
