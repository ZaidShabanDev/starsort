import json
import urllib.parse

import pytest
from botocore.exceptions import ClientError

from starsort.web import api, auth, sessions
from starsort.web.api import handler
from starsort.web.jobs import JobStore
from starsort.web.limits import WEEK_SECONDS, Limits, SpendCounter, UsageStore

USER = {"login": "octo", "id": 1, "name": "Octo Cat", "avatar_url": "https://a"}
SITE = "https://site.example.com"


def call(route_key: str) -> tuple[int, dict]:
    response = handler({"routeKey": route_key}, context=None)
    return response["statusCode"], json.loads(response["body"])


def event(route_key: str, query: dict | None = None, cookies: list[str] | None = None, path: dict | None = None) -> dict:
    return {
        "routeKey": route_key,
        "requestContext": {"domainName": "api.example.com"},
        "queryStringParameters": query,
        "cookies": cookies,
        "pathParameters": path,
    }


def cookie_pair(set_cookie: str) -> str:
    """'name=value; HttpOnly; ...' -> 'name=value', the way the browser sends it back."""
    return set_cookie.split(";")[0]


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(api, "load_settings", lambda: api.Settings("cid", "csecret", "session-secret", SITE))


@pytest.fixture
def github(monkeypatch):
    calls = []

    def exchange_code(client_id, client_secret, code, redirect_uri, verifier):
        calls.append({"code": code, "redirect_uri": redirect_uri, "verifier": verifier})
        return "gho_token"

    monkeypatch.setattr(auth, "exchange_code", exchange_code)
    monkeypatch.setattr(auth, "fetch_user", lambda token: dict(USER))
    return calls


def start_login() -> tuple[str, str]:
    """Run /api/auth/login and return (state sent to GitHub, login cookie)."""
    response = handler(event("GET /api/auth/login"), context=None)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(response["headers"]["location"]).query)
    return query["state"][0], cookie_pair(response["cookies"][0])


def test_health_returns_ok():
    status, body = call("GET /api/health")

    assert status == 200
    assert body == {"status": "ok", "service": "starsort"}


def test_unknown_route_returns_404():
    status, body = call("GET /nope")

    assert status == 404
    assert body["route"] == "GET /nope"


def test_old_routes_without_the_api_prefix_are_gone():
    status, _ = call("GET /health")

    assert status == 404


def test_responses_are_json():
    response = handler({"routeKey": "GET /api/health"}, context=None)

    assert response["headers"]["content-type"] == "application/json"


def test_login_redirects_to_github_with_a_login_cookie(settings):
    response = handler(event("GET /api/auth/login"), context=None)

    location = response["headers"]["location"]
    assert response["statusCode"] == 302
    assert location.startswith(auth.AUTHORIZE_URL)
    assert "redirect_uri=https%3A%2F%2Fsite.example.com%2Fapi%2Fauth%2Fcallback" in location
    assert response["cookies"][0].startswith("starsort_login=")
    assert "Path=/api/auth" in response["cookies"][0]


def test_full_login_sets_session_and_me_returns_user(job_api, github):
    state, login_cookie = start_login()

    response = handler(event("GET /api/auth/callback", {"code": "c1", "state": state}, [login_cookie]), context=None)

    assert response["statusCode"] == 302
    assert response["headers"]["location"] == "/dashboard"
    assert github[0]["redirect_uri"] == f"{SITE}/api/auth/callback"
    session_cookie = cookie_pair(response["cookies"][0])
    assert session_cookie.startswith("starsort_session=")
    assert "gho_token" not in json.dumps(response)

    me = handler(event("GET /api/me", cookies=[session_cookie]), context=None)
    body = json.loads(me["body"])
    assert me["statusCode"] == 200
    assert {field: body[field] for field in USER} == USER
    assert body["trial"]["can_run"] is True


def test_callback_with_wrong_state_is_rejected(settings, github):
    _, login_cookie = start_login()

    response = handler(event("GET /api/auth/callback", {"code": "c1", "state": "forged"}, [login_cookie]), context=None)

    assert response["statusCode"] == 400
    assert github == []


def test_callback_without_login_cookie_is_rejected(settings, github):
    state, _ = start_login()

    response = handler(event("GET /api/auth/callback", {"code": "c1", "state": state}), context=None)

    assert response["statusCode"] == 400
    assert github == []


def test_callback_when_user_denies_access(settings, github):
    response = handler(event("GET /api/auth/callback", {"error": "access_denied"}), context=None)

    assert response["statusCode"] == 400
    assert github == []


def test_callback_reports_github_failure(settings, monkeypatch):
    def failing_exchange(*args):
        raise auth.OAuthError("GitHub request failed: timeout")

    monkeypatch.setattr(auth, "exchange_code", failing_exchange)
    state, login_cookie = start_login()

    response = handler(event("GET /api/auth/callback", {"code": "c1", "state": state}, [login_cookie]), context=None)

    assert response["statusCode"] == 502


def test_me_without_session_is_401(settings):
    response = handler(event("GET /api/me"), context=None)

    assert response["statusCode"] == 401


def test_login_cookie_does_not_work_as_a_session(settings):
    _, login_cookie = start_login()
    fake_session = login_cookie.replace("starsort_login=", "starsort_session=")

    response = handler(event("GET /api/me", cookies=[fake_session]), context=None)

    assert response["statusCode"] == 401


def test_logout_clears_the_session_cookie(settings):
    response = handler(event("POST /api/auth/logout"), context=None)

    assert response["statusCode"] == 200
    assert response["cookies"][0].startswith("starsort_session=;")
    assert "Max-Age=0" in response["cookies"][0]


# ---------- jobs ----------


def session_cookie_for(user: dict) -> str:
    return f"{sessions.SESSION_COOKIE}={sessions.sign(user, 'session-secret', 'session', 3600)}"


CAP = 50_000_000


@pytest.fixture
def job_api(monkeypatch, settings, jobs_table, usage_table, spend_table, stars_table):
    """Real stores on in-memory tables; records worker starts instead of invoking Lambda."""
    started = []
    monkeypatch.setattr(api, "job_store", lambda: JobStore(jobs_table))
    monkeypatch.setattr(api, "usage_store", lambda: UsageStore(usage_table))
    monkeypatch.setattr(api, "stars_table", lambda: stars_table)
    monkeypatch.setattr(api, "spend_counter", lambda: SpendCounter(spend_table))
    monkeypatch.setattr(api, "load_limits", lambda: Limits(CAP, frozenset({"boss"})))
    monkeypatch.setattr(api, "start_worker", started.append)
    return started


def post_job(user: dict = USER) -> tuple[int, dict]:
    response = handler(event("POST /api/jobs", cookies=[session_cookie_for(user)]), context=None)
    return response["statusCode"], json.loads(response["body"])


def test_create_job_needs_a_session(job_api):
    response = handler(event("POST /api/jobs"), context=None)

    assert response["statusCode"] == 401
    assert job_api == []


def test_create_job_queues_it_and_starts_the_worker(job_api, jobs_table):
    response = handler(event("POST /api/jobs", cookies=[session_cookie_for(USER)]), context=None)

    body = json.loads(response["body"])
    assert response["statusCode"] == 202
    assert body["status"] == "queued"
    assert body["login"] == "octo"
    assert "owner_id" not in body
    assert job_api == [body["job_id"]]
    assert jobs_table.items[body["job_id"]]["owner_id"] == "1"


def test_create_job_marks_it_failed_when_the_worker_cannot_start(monkeypatch, job_api, jobs_table):
    def failing_start(job_id):
        raise ClientError({"Error": {"Code": "TooManyRequestsException", "Message": "slow down"}}, "Invoke")

    monkeypatch.setattr(api, "start_worker", failing_start)

    response = handler(event("POST /api/jobs", cookies=[session_cookie_for(USER)]), context=None)

    assert response["statusCode"] == 502
    assert [job["status"] for job in jobs_table.items.values()] == ["failed"]


def test_get_job_returns_its_status(job_api):
    cookie = session_cookie_for(USER)
    job_id = json.loads(handler(event("POST /api/jobs", cookies=[cookie]), context=None)["body"])["job_id"]

    response = handler(event("GET /api/jobs/{job_id}", cookies=[cookie], path={"job_id": job_id}), context=None)

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["job_id"] == job_id


def test_get_job_of_another_user_is_404(job_api):
    job_id = json.loads(handler(event("POST /api/jobs", cookies=[session_cookie_for(USER)]), context=None)["body"])["job_id"]
    other = session_cookie_for({**USER, "login": "mallory", "id": 2})

    response = handler(event("GET /api/jobs/{job_id}", cookies=[other], path={"job_id": job_id}), context=None)

    assert response["statusCode"] == 404


def test_get_unknown_job_is_404(job_api):
    response = handler(event("GET /api/jobs/{job_id}", cookies=[session_cookie_for(USER)], path={"job_id": "nope"}), context=None)

    assert response["statusCode"] == 404


def test_get_job_needs_a_session(job_api):
    response = handler(event("GET /api/jobs/{job_id}", path={"job_id": "x"}), context=None)

    assert response["statusCode"] == 401


# ---------- free-trial limits ----------


def test_second_job_in_the_same_week_is_refused_with_429(job_api, usage_table):
    post_job()
    usage_table.items["1"]["active_until"] = 0  # the first run finished

    status, body = post_job()

    assert status == 429
    assert body["reason"] == "weekly"
    assert body["retry_at"] > 0
    assert len(job_api) == 1


def test_job_is_refused_while_another_is_running(job_api):
    post_job()

    status, body = post_job()

    assert (status, body["reason"]) == (429, "running")


def test_unlimited_login_can_run_again_in_the_same_week(job_api, usage_table):
    boss = {**USER, "login": "Boss", "id": 7}
    post_job(boss)
    usage_table.items["7"]["active_until"] = 0

    status, _ = post_job(boss)

    assert status == 202


def test_jobs_are_refused_when_the_monthly_budget_is_used_up(job_api, spend_table):
    SpendCounter(spend_table).add(CAP)

    status, body = post_job()

    assert (status, body["reason"]) == (503, "budget")
    assert job_api == []


def test_failed_worker_start_gives_the_week_back(monkeypatch, job_api):
    def failing_start(job_id):
        raise ClientError({"Error": {"Code": "ServiceException", "Message": "down"}}, "Invoke")

    monkeypatch.setattr(api, "start_worker", failing_start)
    post_job()
    monkeypatch.setattr(api, "start_worker", job_api.append)

    status, _ = post_job()

    assert status == 202


# ---------- latest job ----------


def get_latest(user: dict = USER) -> tuple[int, dict]:
    response = handler(event("GET /api/jobs/latest", cookies=[session_cookie_for(user)]), context=None)
    return response["statusCode"], json.loads(response["body"])


def test_latest_job_is_404_before_the_first_run(job_api):
    status, body = get_latest()

    assert (status, body["error"]) == (404, "no runs yet")


def test_latest_job_returns_the_newest_run(job_api, usage_table):
    boss = {**USER, "login": "Boss", "id": 7}
    post_job(boss)
    usage_table.items["7"]["active_until"] = 0
    _, second = post_job(boss)

    status, body = get_latest(boss)

    assert (status, body["job_id"]) == (200, second["job_id"])
    assert "owner_id" not in body


def test_latest_job_shows_a_run_whose_worker_could_not_start(monkeypatch, job_api):
    def failing_start(job_id):
        raise ClientError({"Error": {"Code": "ServiceException", "Message": "down"}}, "Invoke")

    monkeypatch.setattr(api, "start_worker", failing_start)
    post_job()

    status, body = get_latest()

    assert (status, body["status"]) == (200, "failed")


def test_latest_job_needs_a_session(job_api):
    response = handler(event("GET /api/jobs/latest"), context=None)

    assert response["statusCode"] == 401


# ---------- delete my data ----------


def delete_data(user: dict = USER) -> dict:
    return handler(event("DELETE /api/me/data", cookies=[session_cookie_for(user)]), context=None)


def test_delete_my_data_removes_stars_and_jobs_and_signs_out(job_api, jobs_table, usage_table, stars_table):
    post_job()
    usage_table.items["1"]["active_until"] = 0
    stars_table.items["1"] = {"owner_id": "1", "data": b"stars"}
    stars_table.items["2"] = {"owner_id": "2", "data": b"other user"}

    response = delete_data()

    assert (response["statusCode"], json.loads(response["body"])) == (200, {"deleted_jobs": 1})
    assert jobs_table.items == {}
    assert list(stars_table.items) == ["2"]
    assert response["cookies"][0].startswith(f"{sessions.SESSION_COOKIE}=;")
    assert get_latest()[0] == 404


def test_delete_my_data_keeps_the_weekly_limit(job_api, usage_table):
    post_job()
    usage_table.items["1"]["active_until"] = 0

    delete_data()

    assert post_job()[1]["reason"] == "weekly"


def test_delete_my_data_waits_for_a_running_job(job_api, jobs_table):
    post_job()

    response = delete_data()

    assert (response["statusCode"], json.loads(response["body"])["error"]) == (409, api.DELETE_BUSY_MESSAGE)
    assert len(jobs_table.items) == 1


def test_delete_my_data_needs_a_session(job_api):
    response = handler(event("DELETE /api/me/data"), context=None)

    assert response["statusCode"] == 401


# ---------- GitHub Lists sync ----------


@pytest.fixture
def lists_api(monkeypatch, job_api):
    """GitHub's answers for the Lists approval, plus records of revoked tokens and started syncs."""
    calls = {"revoked": [], "syncs": [], "scopes": {"user"}, "user": dict(USER)}
    monkeypatch.setattr(auth, "exchange_code_and_scopes", lambda *args: ("gho_lists", calls["scopes"]))
    monkeypatch.setattr(auth, "fetch_user", lambda token: calls["user"])
    monkeypatch.setattr(auth, "revoke_token", lambda client_id, secret, token: calls["revoked"].append(token))
    monkeypatch.setattr(api, "start_sync_worker", lambda job_id, token, private: calls["syncs"].append((job_id, token, private)))
    return calls


def finished_run(usage_table, owner_id: str = "1") -> None:
    post_job()
    usage_table.items[owner_id]["active_until"] = 0


def connect_lists(private: bool = False) -> tuple[str, str]:
    """Run /api/auth/lists and return (state sent to GitHub, login cookie)."""
    response = handler(event("GET /api/auth/lists", {"private": "1"} if private else None, [session_cookie_for(USER)]), context=None)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(response["headers"]["location"]).query)
    return query["state"][0], cookie_pair(response["cookies"][0])


def finish_lists(state: str, login_cookie: str, query: dict | None = None, signed_in: bool = True) -> str:
    cookies = [login_cookie, session_cookie_for(USER)] if signed_in else [login_cookie]
    response = handler(event("GET /api/auth/callback", query or {"code": "c1", "state": state}, cookies), context=None)
    assert response["statusCode"] == 302
    return response["headers"]["location"]


def test_lists_connect_asks_github_for_the_user_scope(lists_api):
    response = handler(event("GET /api/auth/lists", cookies=[session_cookie_for(USER)]), context=None)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(response["headers"]["location"]).query)
    assert query["scope"] == ["user"]
    assert "Path=/api/auth" in response["cookies"][0]


def test_lists_connect_without_a_session_goes_home(settings):
    response = handler(event("GET /api/auth/lists"), context=None)

    assert (response["statusCode"], response["headers"]["location"]) == (302, "/")


def test_lists_approval_starts_a_sync_and_never_stores_the_token(lists_api, usage_table, jobs_table):
    finished_run(usage_table)

    location = finish_lists(*connect_lists(private=True))

    job_id, token, private = lists_api["syncs"][0]
    assert location == "/dashboard?lists=started"
    assert (token, private) == ("gho_lists", True)
    assert lists_api["revoked"] == []  # the worker revokes it when the sync is done
    assert jobs_table.items[job_id]["kind"] == "lists"
    assert "gho_lists" not in json.dumps([jobs_table.items, usage_table.items], default=str)

    latest = handler(event("GET /api/jobs/latest", {"kind": "lists"}, [session_cookie_for(USER)]), context=None)
    assert json.loads(latest["body"])["job_id"] == job_id


def test_lists_approval_with_another_github_account_is_refused(lists_api, usage_table):
    finished_run(usage_table)
    lists_api["user"] = {**USER, "login": "mallory", "id": 2}

    location = finish_lists(*connect_lists())

    assert location == "/dashboard?lists=wrong_account"
    assert (lists_api["syncs"], lists_api["revoked"]) == ([], ["gho_lists"])


def test_lists_approval_without_the_scope_is_refused(lists_api, usage_table):
    finished_run(usage_table)
    lists_api["scopes"] = set()

    assert finish_lists(*connect_lists()) == "/dashboard?lists=denied"
    assert lists_api["revoked"] == ["gho_lists"]


def test_lists_sync_needs_a_run_first(lists_api):
    assert finish_lists(*connect_lists()) == "/dashboard?lists=no_run"
    assert lists_api["revoked"] == ["gho_lists"]


def test_lists_sync_waits_for_a_running_job(lists_api):
    post_job()

    assert finish_lists(*connect_lists()) == "/dashboard?lists=busy"
    assert lists_api["revoked"] == ["gho_lists"]


def test_cancelled_lists_approval_changes_nothing(lists_api, usage_table):
    finished_run(usage_table)
    state, login_cookie = connect_lists()

    location = finish_lists(state, login_cookie, {"error": "access_denied"})

    assert location == "/dashboard?lists=denied"
    assert (lists_api["syncs"], lists_api["revoked"]) == ([], [])


def test_lists_approval_after_signing_out(lists_api, usage_table):
    finished_run(usage_table)
    state, login_cookie = connect_lists()

    assert finish_lists(state, login_cookie, signed_in=False) == "/dashboard?lists=signed_out"


def test_lists_sync_that_cannot_start_frees_the_slot(monkeypatch, lists_api, usage_table, jobs_table):
    def failing_start(job_id, token, private):
        raise ClientError({"Error": {"Code": "ServiceException", "Message": "down"}}, "Invoke")

    finished_run(usage_table)
    monkeypatch.setattr(api, "start_sync_worker", failing_start)

    location = finish_lists(*connect_lists())

    assert location == "/dashboard?lists=error"
    assert lists_api["revoked"] == ["gho_lists"]
    assert usage_table.items["1"]["active_until"] == 0
    assert [job["status"] for job in jobs_table.items.values() if job["kind"] == "lists"] == ["failed"]


# ---------- trial status on /api/me ----------


def me_trial(user: dict = USER) -> dict:
    response = handler(event("GET /api/me", cookies=[session_cookie_for(user)]), context=None)
    return json.loads(response["body"])["trial"]


def test_me_shows_the_next_run_date_after_a_run(job_api, usage_table):
    post_job()
    usage_table.items["1"]["active_until"] = 0

    trial = me_trial()

    assert trial["can_run"] is False
    assert trial["reason"] == "weekly"
    assert trial["retry_at"] == usage_table.items["1"]["last_started_at"] + WEEK_SECONDS
    assert trial["message"] == api.LIMIT_MESSAGES["weekly"]


def test_me_shows_a_running_job(job_api):
    post_job()

    assert me_trial()["reason"] == "running"


def test_me_shows_the_trial_is_paused_when_the_budget_is_used_up(job_api, spend_table):
    SpendCounter(spend_table).add(CAP)

    trial = me_trial()

    assert (trial["can_run"], trial["reason"], trial["message"]) == (False, "budget", api.BUDGET_MESSAGE)


def test_me_marks_unlimited_testers(job_api, usage_table):
    boss = {**USER, "login": "Boss", "id": 7}
    post_job(boss)
    usage_table.items["7"]["active_until"] = 0

    trial = me_trial(boss)

    assert (trial["unlimited"], trial["can_run"]) == (True, True)
    assert me_trial()["unlimited"] is False


def test_me_does_not_use_up_a_run(job_api, usage_table):
    me_trial()

    assert usage_table.items == {}
    assert post_job()[0] == 202
