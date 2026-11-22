import json
from datetime import datetime, timezone

import pytest

from starsort.cache import StarCache
from starsort.github import GitHubError
from starsort.github_lists import MANAGED_MARKER, StarList
from starsort.models import BatchCategorization, Repo, RepoAssignment
from starsort.web import jobs, worker
from starsort.web.jobs import STALE_SECONDS, JobStore
from starsort.web.limits import BUDGET_MESSAGE, SpendCounter, UsageStore
from starsort.web.worker import (
    FAILED_MESSAGE,
    MAX_STARS,
    NO_CATEGORIES_MESSAGE,
    SYNC_FAILED_MESSAGE,
    WAITED_TOO_LONG_MESSAGE,
    DynamoCacheStore,
    Tables,
    WorkerSettings,
    priced_ask_factory,
    priced_merge_factory,
    run_job,
    run_sync,
)

SETTINGS = WorkerSettings(
    client_id="cid",
    client_secret="csecret",
    model_id="test-model",
    input_usd_per_million=3.3,
    output_usd_per_million=16.5,
    monthly_cap_micro_usd=50_000_000,
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


def test_job_is_not_run_when_the_budget_ran_out_while_it_waited(github, db):
    job_id = new_job(db)
    db.spend.add(SETTINGS.monthly_cap_micro_usd)
    ai = FakeAI("AI")

    run_job(job_id, db, SETTINGS, ask_factory=ai)

    job = db.jobs.get(job_id)
    assert ai.asked == []
    assert (job["status"], job["error"]) == (jobs.FAILED, BUDGET_MESSAGE)
    assert db.usage.try_start("1").allowed  # the week was given back


def test_job_that_waited_too_long_in_the_queue_is_not_run(github, db, jobs_table):
    job_id = new_job(db)
    jobs_table.items[job_id]["created_at"] -= STALE_SECONDS + 1
    ai = FakeAI("AI")

    run_job(job_id, db, SETTINGS, ask_factory=ai)

    job = db.jobs.get(job_id)
    assert ai.asked == []
    assert (job["status"], job["error"]) == (jobs.FAILED, WAITED_TOO_LONG_MESSAGE)
    assert db.usage.try_start("1").allowed


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


def test_merge_call_counts_toward_the_spend_cap_too(monkeypatch, db):
    seen = {}

    def fake_make_merger(model, on_usage=None):
        seen["on_usage"] = on_usage
        return lambda prompt: None

    monkeypatch.setattr(worker, "build_model", lambda provider, model_id: f"model:{model_id}")
    monkeypatch.setattr(worker, "make_merger", fake_make_merger)

    priced_merge_factory(SETTINGS, db.spend)()
    seen["on_usage"](1_000_000, 0)

    assert db.spend.month_total() == 3_300_000


# ---------- GitHub Lists sync ----------


class FakeListsGitHub:
    """Stands in for GitHubGraphQL inside sync_github_lists."""

    login = "octo"

    restricted: set[str] = set()
    hidden: set[str] = set()  # blocking orgs found only by check_repo_access (their repos aren't in any list yet)
    existing: list[StarList] = []

    def __init__(self, token: str):
        self.token = token
        self.created: list[tuple[str, bool]] = []
        self.updates: list[tuple[str, list[str]]] = []
        self.deleted: list[str] = []
        self.restricted_orgs = set(self.restricted)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        pass

    def viewer_login(self):
        return self.login

    def fetch_lists(self):
        return list(self.existing)

    def check_repo_access(self, node_ids):
        self.restricted_orgs |= self.hidden

    def delete_list(self, list_id):
        self.deleted.append(list_id)

    def create_list(self, name, private=False):
        self.created.append((name, private))
        return f"L-{name}"

    def set_lists_for_item(self, item_id, list_ids):
        self.updates.append((item_id, list_ids))


@pytest.fixture
def lists_github(monkeypatch) -> list[FakeListsGitHub]:
    made: list[FakeListsGitHub] = []

    def connect(token):
        made.append(FakeListsGitHub(token))
        return made[-1]

    monkeypatch.setattr("starsort.service.GitHubGraphQL", connect)
    return made


def save_categories(db: Tables) -> None:
    repos = [
        Repo(full_name="a/agent", url="https://github.com/a/agent", node_id="R1", updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        Repo(full_name="b/bot", url="https://github.com/b/bot", node_id="R2", updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
    ]
    cache = StarCache(username="octo", fetched_at=datetime.now(timezone.utc), repos=repos, categories={"a/agent": "AI", "b/bot": "Other"})
    DynamoCacheStore(db.stars, "1").save(cache)


def sync_job(db: Tables) -> str:
    """What the API does after the user approves: a finished run exists, then the sync takes the running slot."""
    db.usage.try_start("1", weekly=False)
    db.usage.finish("1")
    db.usage.try_start_sync("1")
    return db.jobs.create("1", "octo", kind=jobs.LISTS)["job_id"]


def sync(db: Tables, job_id: str, revoked: list[str], private: bool = False) -> None:
    run_sync(job_id, "gho_lists", private, db, SETTINGS, pause=lambda: None, revoke=revoked.append)


def test_sync_copies_categories_to_lists_then_revokes_the_token(lists_github, db):
    save_categories(db)
    job_id = sync_job(db)
    revoked: list[str] = []

    sync(db, job_id, revoked, private=True)

    job = db.jobs.get(job_id)
    assert lists_github[0].token == "gho_lists"
    assert lists_github[0].created == [("AI", True)]  # "Other" never gets a list
    assert lists_github[0].updates == [("R1", ["L-AI"])]
    assert (job["status"], job["lists_created"], job["repos_updated"], job["repos_to_update"]) == (jobs.DONE, 1, 1, 1)
    assert revoked == ["gho_lists"]
    assert db.usage.try_start("1", weekly=False).allowed  # the running slot is free again


def test_sync_removes_an_empty_starsort_list(monkeypatch, lists_github, db):
    monkeypatch.setattr(FakeListsGitHub, "existing", [StarList(id="OLD", name="AI Agents", description=MANAGED_MARKER)])
    save_categories(db)
    job_id = sync_job(db)

    sync(db, job_id, [])

    assert lists_github[0].deleted == ["OLD"]
    assert db.jobs.get(job_id)["lists_deleted"] == 1


def test_sync_skips_repos_of_an_org_that_blocks_the_app(monkeypatch, lists_github, db):
    monkeypatch.setattr(FakeListsGitHub, "restricted", {"a"})
    save_categories(db)
    job_id = sync_job(db)
    revoked: list[str] = []

    sync(db, job_id, revoked)

    job = db.jobs.get(job_id)
    assert lists_github[0].updates == []
    assert (job["status"], job["repos_skipped"], job["skipped_orgs"], job["complete"]) == (jobs.DONE, 1, ["a"], True)
    assert revoked == ["gho_lists"]


def test_sync_creates_no_list_for_repos_of_a_blocking_org_found_up_front(monkeypatch, lists_github, db):
    monkeypatch.setattr(FakeListsGitHub, "hidden", {"a"})
    save_categories(db)
    job_id = sync_job(db)

    sync(db, job_id, [])

    job = db.jobs.get(job_id)
    assert (lists_github[0].created, lists_github[0].updates) == ([], [])
    assert (job["lists_created"], job["repos_skipped"], job["skipped_orgs"]) == (0, 1, ["a"])


def test_sync_for_another_github_account_fails_and_still_revokes(monkeypatch, lists_github, db):
    monkeypatch.setattr(FakeListsGitHub, "login", "mallory")
    save_categories(db)
    job_id = sync_job(db)
    revoked: list[str] = []

    sync(db, job_id, revoked)

    assert db.jobs.get(job_id)["status"] == jobs.FAILED
    assert "your own account" in db.jobs.get(job_id)["error"]
    assert revoked == ["gho_lists"]


def test_sync_without_categories_fails_with_a_clear_message(lists_github, db):
    job_id = sync_job(db)
    revoked: list[str] = []

    sync(db, job_id, revoked)

    assert db.jobs.get(job_id)["error"] == NO_CATEGORIES_MESSAGE
    assert (lists_github, revoked) == ([], ["gho_lists"])


def test_unexpected_sync_error_shows_a_safe_message(monkeypatch, lists_github, db):
    def broken(token):
        raise RuntimeError("internal details")

    monkeypatch.setattr("starsort.service.GitHubGraphQL", broken)
    save_categories(db)
    job_id = sync_job(db)
    revoked: list[str] = []

    sync(db, job_id, revoked)

    assert db.jobs.get(job_id)["error"] == SYNC_FAILED_MESSAGE
    assert revoked == ["gho_lists"]


def test_sync_that_waited_too_long_is_not_run(lists_github, db, jobs_table):
    save_categories(db)
    job_id = sync_job(db)
    jobs_table.items[job_id]["created_at"] -= STALE_SECONDS + 1
    revoked: list[str] = []

    sync(db, job_id, revoked)

    assert db.jobs.get(job_id)["error"] == WAITED_TOO_LONG_MESSAGE
    assert (lists_github, revoked) == ([], ["gho_lists"])


def test_a_started_sync_is_not_run_again_or_revoked_twice(lists_github, db):
    job_id = sync_job(db)
    db.jobs.start(job_id)
    revoked: list[str] = []

    sync(db, job_id, revoked)

    assert (lists_github, revoked) == ([], [])


def test_sync_for_an_unknown_job_still_revokes_the_token(db):
    revoked: list[str] = []

    sync(db, "nope", revoked)

    assert revoked == ["gho_lists"]


def test_cache_store_round_trip_is_gzipped(stars_table):
    store = DynamoCacheStore(stars_table, "1")
    cache = StarCache(username="octo", fetched_at=datetime.now(timezone.utc), repos=[make_repo("a/agent")], categories={"a/agent": "AI"})

    store.save(cache)

    assert stars_table.items["1"]["data"][:2] == b"\x1f\x8b"
    assert store.load() == cache


def test_cache_store_is_empty_for_a_new_user(stars_table):
    assert DynamoCacheStore(stars_table, "1").load() is None
