import hmac
import json
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import boto3

from starsort.web import auth, sessions

LOGIN_COOKIE = "starsort_login"
LOGIN_SECONDS = 10 * 60
AFTER_LOGIN_PATH = "/me"  # the Angular app takes over in P6


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    session_secret: str


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Read once per Lambda container. Secrets live in SSM Parameter Store, never in code or Terraform."""
    ssm = boto3.client("ssm")

    def secret(name: str) -> str:
        return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]

    return Settings(
        client_id=os.environ["GITHUB_CLIENT_ID"],
        client_secret=secret(os.environ["GITHUB_CLIENT_SECRET_PARAM"]),
        session_secret=secret(os.environ["SESSION_SECRET_PARAM"]),
    )


def json_response(status_code: int, body: dict[str, Any], cookies: list[str] | None = None) -> dict[str, Any]:
    response = {
        "statusCode": status_code,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(body),
    }
    if cookies:
        response["cookies"] = cookies
    return response


def redirect(location: str, cookies: list[str]) -> dict[str, Any]:
    return {"statusCode": 302, "headers": {"location": location, "cache-control": "no-store"}, "cookies": cookies, "body": ""}


def callback_url(event: dict[str, Any]) -> str:
    return f"https://{event['requestContext']['domainName']}/auth/callback"


def health(event: dict[str, Any]) -> dict[str, Any]:
    return json_response(200, {"status": "ok", "service": "starsort"})


def login(event: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    state = secrets.token_urlsafe(24)
    verifier, challenge = auth.new_pkce()
    login_data = sessions.sign({"state": state, "verifier": verifier}, settings.session_secret, "login", LOGIN_SECONDS)
    return redirect(
        auth.authorize_url(settings.client_id, callback_url(event), state, challenge),
        [sessions.make_cookie(LOGIN_COOKIE, login_data, LOGIN_SECONDS, path="/auth")],
    )


def callback(event: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    query = event.get("queryStringParameters") or {}
    if query.get("error"):
        return json_response(400, {"error": query.get("error_description") or query["error"]})

    login_data = sessions.verify(sessions.read_cookie(event.get("cookies"), LOGIN_COOKIE) or "", settings.session_secret, "login")
    if not login_data or not query.get("code") or not hmac.compare_digest(query.get("state", "").encode(), login_data["state"].encode()):
        return json_response(400, {"error": "Login expired or invalid. Please sign in again."})

    try:
        token = auth.exchange_code(settings.client_id, settings.client_secret, query["code"], callback_url(event), login_data["verifier"])
        user = auth.fetch_user(token)
    except auth.OAuthError as error:
        return json_response(502, {"error": str(error)})

    # The GitHub token is dropped here on purpose: only the public profile goes into the session.
    session = sessions.sign(user, settings.session_secret, "session", sessions.SESSION_SECONDS)
    return redirect(AFTER_LOGIN_PATH, [
        sessions.make_cookie(sessions.SESSION_COOKIE, session, sessions.SESSION_SECONDS),
        sessions.clear_cookie(LOGIN_COOKIE, path="/auth"),
    ])


def me(event: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    user = sessions.verify(sessions.read_cookie(event.get("cookies"), sessions.SESSION_COOKIE) or "", settings.session_secret, "session")
    if user is None:
        return json_response(401, {"error": "not signed in"})
    return json_response(200, {field: user.get(field) for field in auth.USER_FIELDS})


ROUTES = {
    "GET /health": health,
    "GET /auth/login": login,
    "GET /auth/callback": callback,
    "GET /me": me,
}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """AWS Lambda entry point for the HTTP API (payload format 2.0)."""
    route = event.get("routeKey", "")
    action = ROUTES.get(route)
    if action is None:
        return json_response(404, {"error": "not found", "route": route})
    return action(event)
