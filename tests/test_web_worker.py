import json
from datetime import datetime, timezone

import pytest

from starsort.cache import StarCache
from starsort.github import GitHubError
from starsort.models import BatchCategorization, Repo, RepoAssignment
from starsort.web import jobs
from starsort.web.jobs import JobStore
from starsort.web.worker import FAILED_MESSAGE, DynamoCacheStore, WorkerSettings, run_job

SETTINGS = WorkerSettings(client_id="cid", client_secret="csecret", model_id="test-model")


def make_repo(full_name: str) -> Repo:
    return Repo(full_name=full_name, url=f"https://github.com/{full_name}", updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


class FakeGitHub:
    def __init__(self, *names: str):
        self.repos = [make_repo(name) for name in names]
        self.app_auth = None

    def __call__(self, username, token=None, known=None, app_auth=None):
        self.app_auth = app_auth
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
def store(jobs_table) -> JobStore:
    return JobStore(jobs_table)


def test_run_job_sorts_stars_and_saves_the_result(github, store, stars_table):
    job_id = store.create("1", "octo")["job_id"]

    run_job(job_id, store, stars_table, SETTINGS, ask_factory=FakeAI("AI"))

    job = store.get(job_id)
    assert job["status"] == jobs.DONE
    assert job["categories"] == {"AI": 2}
    assert job["repo_count"] == 2
    assert job["new_repo_count"] == 2
    assert "a/agent" in job["markdown"]
    assert (job["batches_done"], job["batches_total"]) == (1, 1)


def test_run_job_reads_github_with_the_app_credentials(github, store, stars_table):
    run_job(store.create("1", "octo")["job_id"], store, stars_table, SETTINGS, ask_factory=FakeAI("AI"))

    assert github.app_auth == ("cid", "csecret")


def test_second_run_only_sends_new_stars_to_the_ai(github, store, stars_table):
    run_job(store.create("1", "octo")["job_id"], store, stars_table, SETTINGS, ask_factory=FakeAI("AI"))
    github.repos.insert(0, make_repo("c/new"))
    second_ai = FakeAI("AI")

    job_id = store.create("1", "octo")["job_id"]
    run_job(job_id, store, stars_table, SETTINGS, ask_factory=second_ai)

    assert second_ai.asked == ["c/new"]
    assert store.get(job_id)["new_repo_count"] == 1


def test_a_started_job_is_not_run_again(github, store, stars_table):
    job_id = store.create("1", "octo")["job_id"]
    store.start(job_id)
    ai = FakeAI("AI")

    run_job(job_id, store, stars_table, SETTINGS, ask_factory=ai)

    assert ai.asked == []
    assert store.get(job_id)["status"] == jobs.RUNNING


def test_unknown_job_is_ignored(store, stars_table):
    run_job("nope", store, stars_table, SETTINGS)


def test_github_error_is_shown_to_the_user(monkeypatch, store, stars_table):
    def missing_user(*args, **kwargs):
        raise GitHubError("GitHub user 'octo' was not found.")

    monkeypatch.setattr("starsort.service.fetch_starred", missing_user)
    job_id = store.create("1", "octo")["job_id"]

    run_job(job_id, store, stars_table, SETTINGS)

    assert store.get(job_id)["status"] == jobs.FAILED
    assert store.get(job_id)["error"] == "GitHub user 'octo' was not found."


def test_unexpected_error_fails_the_job_with_a_safe_message(github, store, stars_table):
    def broken_ai():
        raise RuntimeError("Bedrock said no: internal details")

    job_id = store.create("1", "octo")["job_id"]

    run_job(job_id, store, stars_table, SETTINGS, ask_factory=broken_ai)

    assert store.get(job_id)["status"] == jobs.FAILED
    assert store.get(job_id)["error"] == FAILED_MESSAGE


def test_cache_store_round_trip_is_gzipped(stars_table):
    store = DynamoCacheStore(stars_table, "1")
    cache = StarCache(username="octo", fetched_at=datetime.now(timezone.utc), repos=[make_repo("a/agent")], categories={"a/agent": "AI"})

    store.save(cache)

    assert stars_table.items["1"]["data"][:2] == b"\x1f\x8b"
    assert store.load() == cache


def test_cache_store_is_empty_for_a_new_user(stars_table):
    assert DynamoCacheStore(stars_table, "1").load() is None
