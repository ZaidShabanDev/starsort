import json
import urllib.parse

import pytest

from starsort.web import api, auth
from starsort.web.api import handler

USER = {"login": "octo", "id": 1, "name": "Octo Cat", "avatar_url": "https://a"}


def call(route_key: str) -> tuple[int, dict]:
    response = handler({"routeKey": route_key}, context=None)
    return response["statusCode"], json.loads(response["body"])


def event(route_key: str, query: dict | None = None, cookies: list[str] | None = None) -> dict:
    return {
        "routeKey": route_key,
        "requestContext": {"domainName": "api.example.com"},
        "queryStringParameters": query,
        "cookies": cookies,
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
