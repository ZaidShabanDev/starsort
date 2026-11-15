import json
from datetime import datetime, timezone

import pytest

from starsort.cache import StarCache
from starsort.github import GitHubError
from starsort.models import BatchCategorization, Repo, RepoAssignment
from starsort.web import jobs, worker
from starsort.web.jobs import JobStore
from starsort.web.limits import SpendCounter, UsageStore
from starsort.web.worker import FAILED_MESSAGE, MAX_STARS, DynamoCacheStore, Tables, WorkerSettings, priced_ask_factory, run_job

SETTINGS = WorkerSettings(
    client_id="cid", client_secret="csecret", model_id="test-model", input_usd_per_million=3.3, output_usd_per_million=16.5
)


def make_repo(full_name: str) -> Repo:
    return Repo(full_name=full_name, url=f"https://github.com/{full_name}", updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


class FakeGitHub:
    def __init__(self, *names: str):
        self.repos = [make_repo(name) for name in names]
        self.app_auth = None
        self.limit = None

    def __call__(self, username, token=None, known=None, app_auth=None, limit=None):
        self.app_auth = app_auth
        self.limit = limit
        return [repo for repo in self.repos if not known or repo.full_name not in known]


class FakeAI:
    """ask_factory whose agent puts every repo in the prompt into one category."""

    def __init__(self, category: str):
        self.category = category
        self.asked: list[str] = []

    def __call__(self):
        return self.ask

    def ask(self, prompt: str) -> BatchCategorization:
        repos = json.loads(prompt[prompt.index("["):])
        self.asked.extend(repo["full_name"] for repo in repos)
        return BatchCategorization(assignments=[RepoAssignment(full_name=repo["full_name"], category=self.category) for repo in repos])


@pytest.fixture
def github(monkeypatch):
    fake = FakeGitHub("a/agent", "b/bot")
    monkeypatch.setattr("starsort.service.fetch_starred", fake)
    return fake


@pytest.fixture
def db(jobs_table, stars_table, usage_table, spend_table) -> Tables:
    return Tables(jobs=JobStore(jobs_table), stars=stars_table, usage=UsageStore(usage_table), spend=SpendCounter(spend_table))


def new_job(db: Tables, owner_id: str = "1") -> str:
    """What POST /jobs does: take the weekly slot, then create the job."""
    db.usage.try_start(owner_id)
    return db.jobs.create(owner_id, "octo")["job_id"]


def test_run_job_sorts_stars_and_saves_the_result(github, db):
    job_id = new_job(db)

    run_job(job_id, db, SETTINGS, ask_factory=FakeAI("AI"))

    job = db.jobs.get(job_id)
    assert job["status"] == jobs.DONE
    assert job["categories"] == {"AI": 2}
    assert job["repo_count"] == 2
    assert job["new_repo_count"] == 2
    assert "a/agent" in job["markdown"]
    assert (job["batches_done"], job["batches_total"]) == (1, 1)


def test_run_job_reads_github_with_the_app_credentials_and_the_star_cap(github, db):
    run_job(new_job(db), db, SETTINGS, ask_factory=FakeAI("AI"))

    assert github.app_auth == ("cid", "csecret")
    assert github.limit == MAX_STARS == 500


def test_finished_run_frees_the_running_slot_but_keeps_the_week(github, db):
    run_job(new_job(db), db, SETTINGS, ask_factory=FakeAI("AI"))

    assert db.usage.try_start("1").reason == "weekly"


def test_failed_run_gives_the_week_back(monkeypatch, db):
    def missing_user(*args, **kwargs):
        raise GitHubError("GitHub user 'octo' was not found.")

    monkeypatch.setattr("starsort.service.fetch_starred", missing_user)

    run_job(new_job(db), db, SETTINGS)

    assert db.usage.try_start("1").allowed


def test_second_run_only_sends_new_stars_to_the_ai(github, db):
    run_job(new_job(db), db, SETTINGS, ask_factory=FakeAI("AI"))
    github.repos.insert(0, make_repo("c/new"))
    second_ai = FakeAI("AI")

    job_id = db.jobs.create("1", "octo")["job_id"]
    run_job(job_id, db, SETTINGS, ask_factory=second_ai)

    assert second_ai.asked == ["c/new"]
    assert db.jobs.get(job_id)["new_repo_count"] == 1


def test_a_started_job_is_not_run_again(github, db):
    job_id = new_job(db)
    db.jobs.start(job_id)
    ai = FakeAI("AI")

    run_job(job_id, db, SETTINGS, ask_factory=ai)

    assert ai.asked == []
    assert db.jobs.get(job_id)["status"] == jobs.RUNNING


def test_unknown_job_is_ignored(db):
    run_job("nope", db, SETTINGS)


def test_github_error_is_shown_to_the_user(monkeypatch, db):
    def missing_user(*args, **kwargs):
        raise GitHubError("GitHub user 'octo' was not found.")

    monkeypatch.setattr("starsort.service.fetch_starred", missing_user)
    job_id = new_job(db)

    run_job(job_id, db, SETTINGS)

    assert db.jobs.get(job_id)["status"] == jobs.FAILED
    assert db.jobs.get(job_id)["error"] == "GitHub user 'octo' was not found."


def test_unexpected_error_fails_the_job_with_a_safe_message(github, db):
    def broken_ai():
        raise RuntimeError("Bedrock said no: internal details")

    job_id = new_job(db)

    run_job(job_id, db, SETTINGS, ask_factory=broken_ai)

    assert db.jobs.get(job_id)["status"] == jobs.FAILED
    assert db.jobs.get(job_id)["error"] == FAILED_MESSAGE


def test_priced_asker_adds_every_call_to_the_spend_counter(monkeypatch, db):
    seen = {}

    def fake_make_asker(model, on_usage=None):
        seen["on_usage"] = on_usage
        return lambda prompt: None

    monkeypatch.setattr(worker, "build_model", lambda provider, model_id: f"model:{model_id}")
    monkeypatch.setattr(worker, "make_asker", fake_make_asker)

    priced_ask_factory(SETTINGS, db.spend)()
    seen["on_usage"](1_000_000, 0)
    seen["on_usage"](0, 100_000)

    assert db.spend.month_total() == 3_300_000 + 1_650_000


def test_cache_store_round_trip_is_gzipped(stars_table):
    store = DynamoCacheStore(stars_table, "1")
    cache = StarCache(username="octo", fetched_at=datetime.now(timezone.utc), repos=[make_repo("a/agent")], categories={"a/agent": "AI"})

    store.save(cache)

    assert stars_table.items["1"]["data"][:2] == b"\x1f\x8b"
    assert store.load() == cache


def test_cache_store_is_empty_for_a_new_user(stars_table):
    assert DynamoCacheStore(stars_table, "1").load() is None
