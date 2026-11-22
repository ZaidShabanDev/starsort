"""Worker Lambda: runs one job from start to finish (fetch stars, categorize with Bedrock, render Markdown).

Unlike the API, this needs httpx, pydantic and strands, so its Lambda has the dependencies layer attached.
"""

import gzip
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import boto3

from starsort.cache import StarCache
from starsort.categorizer import Asker, Merger, group_assignments
from starsort.github import GitHubError
from starsort.github_lists import ListsError
from starsort.providers import Provider, build_model, make_asker, make_merger
from starsort.service import OrganizeOptions, organize, sync_github_lists
from starsort.web import auth
from starsort.web.jobs import LISTS, STALE_SECONDS, JobStore
from starsort.web.limits import BUDGET_MESSAGE, Limits, SpendCounter, UsageStore, cost_micro_usd

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

FAILED_MESSAGE = "Something went wrong while sorting your stars. Please try again later."
WAITED_TOO_LONG_MESSAGE = "The run waited too long to start because many people are using Starsort. Please try again."
SYNC_FAILED_MESSAGE = "Something went wrong while updating your GitHub Lists. Please try again later."
NO_CATEGORIES_MESSAGE = "There are no categories to sync yet. Start a run first."
# GitHub allows about 80 content changes a minute and 500 an hour. A paced sync of 300 repos also fits in Lambda's 15 minutes.
MAX_LIST_CHANGES = 300
LIST_CHANGE_PAUSE_SECONDS = 1.0
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
    monthly_cap_micro_usd: int


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
        monthly_cap_micro_usd=Limits.from_env().monthly_cap_micro_usd,
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


def priced_usage(settings: WorkerSettings, spend: SpendCounter) -> Callable[[int, int], None]:
    def record(input_tokens: int, output_tokens: int) -> None:
        spend.add(cost_micro_usd(input_tokens, output_tokens, settings.input_usd_per_million, settings.output_usd_per_million))

    return record


def priced_ask_factory(settings: WorkerSettings, spend: SpendCounter) -> Callable[[], Asker]:
    """Bedrock asker that adds the cost of every call to this month's spend counter."""
    return lambda: make_asker(build_model(Provider.bedrock, settings.model_id), on_usage=priced_usage(settings, spend))


def priced_merge_factory(settings: WorkerSettings, spend: SpendCounter) -> Callable[[], Merger]:
    return lambda: make_merger(build_model(Provider.bedrock, settings.model_id), on_usage=priced_usage(settings, spend))


def start_refusal(job: dict[str, Any], db: Tables, settings: WorkerSettings) -> str | None:
    """Jobs can wait in Lambda's async queue (reserved concurrency), so the API's checks may be out of date by now."""
    if time.time() - int(job["created_at"]) > STALE_SECONDS:
        return WAITED_TOO_LONG_MESSAGE  # the dashboard already shows a job this old as failed
    if db.spend.month_total() >= settings.monthly_cap_micro_usd:
        return BUDGET_MESSAGE
    return None


def run_job(
    job_id: str,
    db: Tables,
    settings: WorkerSettings,
    ask_factory: Callable[[], Asker] | None = None,
    merge_factory: Callable[[], Merger] | None = None,
) -> None:
    job = db.jobs.get(job_id)
    if job is None or not db.jobs.start(job_id):
        logger.warning("Skipping job %s: not found or already started.", job_id)
        return

    owner_id = job["owner_id"]
    refusal = start_refusal(job, db, settings)
    if refusal:
        logger.warning("Job %s not run: %s", job_id, refusal)
        db.jobs.fail(job_id, refusal)
        db.usage.finish(owner_id, refund=True)
        return

    options = OrganizeOptions(
        username=job["login"],
        app_auth=(settings.client_id, settings.client_secret),
        provider=Provider.bedrock,
        model_id=settings.model_id,
        max_stars=MAX_STARS,
    )
    if ask_factory is None:
        ask_factory, merge_factory = priced_ask_factory(settings, db.spend), priced_merge_factory(settings, db.spend)
    try:
        result = organize(
            options,
            DynamoCacheStore(db.stars, owner_id),
            ask_factory=ask_factory,
            merge_factory=merge_factory,
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


def revoke_quietly(settings: WorkerSettings, token: str) -> None:
    try:
        auth.revoke_token(settings.client_id, settings.client_secret, token)
    except auth.OAuthError:
        logger.warning("Could not revoke a GitHub token; it stays valid until the user removes the app.")


def run_sync(
    job_id: str,
    token: str,
    private: bool,
    db: Tables,
    settings: WorkerSettings,
    pause: Callable[[], None] | None = None,
    revoke: Callable[[str], None] | None = None,
) -> None:
    """Copy the user's saved categories to their GitHub star Lists, then cancel the token, whatever happens."""
    revoke = revoke or (lambda used_token: revoke_quietly(settings, used_token))
    job = db.jobs.get(job_id)
    if job is not None and not db.jobs.start(job_id):
        logger.warning("Skipping sync %s: already started (that invocation revokes the token).", job_id)
        return
    if job is None:
        revoke(token)
        return

    owner_id = job["owner_id"]
    try:
        if time.time() - int(job["created_at"]) > STALE_SECONDS:
            db.jobs.fail(job_id, WAITED_TOO_LONG_MESSAGE)
            return
        cache = DynamoCacheStore(db.stars, owner_id).load()
        if cache is None or not cache.categories:
            db.jobs.fail(job_id, NO_CATEGORIES_MESSAGE)
            return
        result = sync_github_lists(
            token,
            job["login"],
            group_assignments(cache.repos, cache.categories),
            log=logger.info,
            private=private,
            max_changes=MAX_LIST_CHANGES,
            pause=pause or (lambda: time.sleep(LIST_CHANGE_PAUSE_SECONDS)),
            on_progress=lambda done, total: db.jobs.progress(job_id, done, total),
        )
        db.jobs.finish_sync(job_id, {
            "lists_created": result.lists_created,
            "lists_deleted": result.lists_deleted,
            "repos_updated": result.repos_updated,
            "repos_to_update": result.repos_to_update,
            "repos_skipped": result.repos_skipped,
            "skipped_orgs": list(result.skipped_orgs),
            "complete": result.complete,
        })
    except ListsError as error:
        db.jobs.fail(job_id, str(error))
    except Exception:
        logger.exception("Sync %s failed.", job_id)
        db.jobs.fail(job_id, SYNC_FAILED_MESSAGE)
    finally:
        revoke(token)
        db.usage.finish(owner_id)


def handler(event: dict[str, Any], context: Any) -> None:
    """AWS Lambda entry point, invoked asynchronously by the API. Never log the event: a Lists sync carries a token."""
    if event.get("kind") == LISTS:
        run_sync(event["job_id"], event["token"], event.get("private") is True, tables(), load_settings())
    else:
        run_job(event["job_id"], tables(), load_settings())
