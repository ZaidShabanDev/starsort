from decimal import Decimal

import pytest

from starsort.web import jobs
from starsort.web.jobs import JobStore, public_view

RESULT = {"markdown": "# Stars", "categories": {"AI": 2}, "repo_count": 2, "new_repo_count": 2}


@pytest.fixture
def store(jobs_table) -> JobStore:
    return JobStore(jobs_table)


def test_create_saves_a_queued_job(store):
    job = store.create(owner_id="1", login="octo", now=1000)

    saved = store.get(job["job_id"])
    assert saved["status"] == jobs.QUEUED
    assert saved["owner_id"] == "1"
    assert saved["login"] == "octo"
    assert saved["expires_at"] == 1000 + jobs.KEEP_SECONDS


def test_job_ids_are_unique(store):
    assert store.create("1", "octo")["job_id"] != store.create("1", "octo")["job_id"]


def test_missing_job_is_none(store):
    assert store.get("nope") is None


def test_start_only_works_once(store):
    job_id = store.create("1", "octo")["job_id"]

    assert store.start(job_id) is True
    assert store.start(job_id) is False
    assert store.get(job_id)["status"] == jobs.RUNNING


def test_start_of_missing_job_is_false(store):
    assert store.start("nope") is False


def test_progress_finish_and_fail_update_the_job(store):
    job_id = store.create("1", "octo")["job_id"]
    store.start(job_id)

    store.progress(job_id, 2, 5)
    assert (store.get(job_id)["batches_done"], store.get(job_id)["batches_total"]) == (2, 5)

    store.finish(job_id, RESULT)
    assert store.get(job_id)["status"] == jobs.DONE
    assert store.get(job_id)["markdown"] == "# Stars"

    store.fail(job_id, "boom")
    assert store.get(job_id)["error"] == "boom"


def test_updates_never_create_a_missing_job(store, jobs_table):
    store.progress("nope", 1, 1)

    assert jobs_table.items == {}


def test_public_view_hides_owner_and_converts_decimals():
    job = {"job_id": "j", "owner_id": "1", "login": "octo", "status": jobs.DONE, "updated_at": Decimal(1000),
           "categories": {"AI": Decimal(3)}, "expires_at": Decimal(9)}

    view = public_view(job, now=1000)

    assert "owner_id" not in view
    assert "expires_at" not in view
    assert view["categories"] == {"AI": 3}
    assert isinstance(view["updated_at"], int)


def test_public_view_marks_a_quiet_running_job_as_failed():
    job = {"job_id": "j", "status": jobs.RUNNING, "updated_at": 1000}

    view = public_view(job, now=1000 + jobs.STALE_SECONDS + 1)

    assert view["status"] == jobs.FAILED
    assert view["error"]


def test_public_view_keeps_an_active_job_running():
    job = {"job_id": "j", "status": jobs.RUNNING, "updated_at": 1000}

    assert public_view(job, now=1060)["status"] == jobs.RUNNING
