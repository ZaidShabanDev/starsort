import json
import urllib.parse

import pytest
from botocore.exceptions import ClientError

from starsort.web import api, auth, sessions
from starsort.web.api import handler
from starsort.web.jobs import JobStore

USER = {"login": "octo", "id": 1, "name": "Octo Cat", "avatar_url": "https://a"}


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
    monkeypatch.setattr(api, "load_settings", lambda: api.Settings("cid", "csecret", "session-secret"))


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
    """Run /auth/login and return (state sent to GitHub, login cookie)."""
    response = handler(event("GET /auth/login"), context=None)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(response["headers"]["location"]).query)
    return query["state"][0], cookie_pair(response["cookies"][0])


def test_health_returns_ok():
    status, body = call("GET /health")

    assert status == 200
    assert body == {"status": "ok", "service": "starsort"}


def test_unknown_route_returns_404():
    status, body = call("GET /nope")

    assert status == 404
    assert body["route"] == "GET /nope"


def test_responses_are_json():
    response = handler({"routeKey": "GET /health"}, context=None)

    assert response["headers"]["content-type"] == "application/json"


def test_login_redirects_to_github_with_a_login_cookie(settings):
    response = handler(event("GET /auth/login"), context=None)

    location = response["headers"]["location"]
    assert response["statusCode"] == 302
    assert location.startswith(auth.AUTHORIZE_URL)
    assert "redirect_uri=https%3A%2F%2Fapi.example.com%2Fauth%2Fcallback" in location
    assert response["cookies"][0].startswith("starsort_login=")
    assert "Path=/auth" in response["cookies"][0]


def test_full_login_sets_session_and_me_returns_user(settings, github):
    state, login_cookie = start_login()

    response = handler(event("GET /auth/callback", {"code": "c1", "state": state}, [login_cookie]), context=None)

    assert response["statusCode"] == 302
    assert response["headers"]["location"] == "/me"
    assert github[0]["redirect_uri"] == "https://api.example.com/auth/callback"
    session_cookie = cookie_pair(response["cookies"][0])
    assert session_cookie.startswith("starsort_session=")
    assert "gho_token" not in json.dumps(response)

    me = handler(event("GET /me", cookies=[session_cookie]), context=None)
    assert me["statusCode"] == 200
    assert json.loads(me["body"]) == USER


def test_callback_with_wrong_state_is_rejected(settings, github):
    _, login_cookie = start_login()

    response = handler(event("GET /auth/callback", {"code": "c1", "state": "forged"}, [login_cookie]), context=None)

    assert response["statusCode"] == 400
    assert github == []


def test_callback_without_login_cookie_is_rejected(settings, github):
    state, _ = start_login()

    response = handler(event("GET /auth/callback", {"code": "c1", "state": state}), context=None)

    assert response["statusCode"] == 400
    assert github == []


def test_callback_when_user_denies_access(settings, github):
    response = handler(event("GET /auth/callback", {"error": "access_denied"}), context=None)

    assert response["statusCode"] == 400
    assert github == []


def test_callback_reports_github_failure(settings, monkeypatch):
    def failing_exchange(*args):
        raise auth.OAuthError("GitHub request failed: timeout")

    monkeypatch.setattr(auth, "exchange_code", failing_exchange)
    state, login_cookie = start_login()

    response = handler(event("GET /auth/callback", {"code": "c1", "state": state}, [login_cookie]), context=None)

    assert response["statusCode"] == 502


def test_me_without_session_is_401(settings):
    response = handler(event("GET /me"), context=None)

    assert response["statusCode"] == 401


def test_login_cookie_does_not_work_as_a_session(settings):
    _, login_cookie = start_login()
    fake_session = login_cookie.replace("starsort_login=", "starsort_session=")

    response = handler(event("GET /me", cookies=[fake_session]), context=None)

    assert response["statusCode"] == 401


# ---------- jobs ----------


def session_cookie_for(user: dict) -> str:
    return f"{sessions.SESSION_COOKIE}={sessions.sign(user, 'session-secret', 'session', 3600)}"


@pytest.fixture
def job_api(monkeypatch, settings, jobs_table):
    """Real JobStore on an in-memory table; records worker starts instead of invoking Lambda."""
    started = []
    monkeypatch.setattr(api, "job_store", lambda: JobStore(jobs_table))
    monkeypatch.setattr(api, "start_worker", started.append)
    return started


def test_create_job_needs_a_session(job_api):
    response = handler(event("POST /jobs"), context=None)

    assert response["statusCode"] == 401
    assert job_api == []


def test_create_job_queues_it_and_starts_the_worker(job_api, jobs_table):
    response = handler(event("POST /jobs", cookies=[session_cookie_for(USER)]), context=None)

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

    response = handler(event("POST /jobs", cookies=[session_cookie_for(USER)]), context=None)

    assert response["statusCode"] == 502
    assert [job["status"] for job in jobs_table.items.values()] == ["failed"]


def test_get_job_returns_its_status(job_api):
    cookie = session_cookie_for(USER)
    job_id = json.loads(handler(event("POST /jobs", cookies=[cookie]), context=None)["body"])["job_id"]

    response = handler(event("GET /jobs/{job_id}", cookies=[cookie], path={"job_id": job_id}), context=None)

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["job_id"] == job_id


def test_get_job_of_another_user_is_404(job_api):
    job_id = json.loads(handler(event("POST /jobs", cookies=[session_cookie_for(USER)]), context=None)["body"])["job_id"]
    other = session_cookie_for({**USER, "login": "mallory", "id": 2})

    response = handler(event("GET /jobs/{job_id}", cookies=[other], path={"job_id": job_id}), context=None)

    assert response["statusCode"] == 404


def test_get_unknown_job_is_404(job_api):
    response = handler(event("GET /jobs/{job_id}", cookies=[session_cookie_for(USER)], path={"job_id": "nope"}), context=None)

    assert response["statusCode"] == 404


def test_get_job_needs_a_session(job_api):
    response = handler(event("GET /jobs/{job_id}", path={"job_id": "x"}), context=None)

    assert response["statusCode"] == 401
