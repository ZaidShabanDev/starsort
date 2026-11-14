import hmac
import json
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from starsort.web import auth, jobs, sessions

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


@lru_cache(maxsize=1)
def job_store() -> jobs.JobStore:
    return jobs.JobStore(boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"]))


def start_worker(job_id: str) -> None:
    """Async ("Event") invoke: Lambda queues the job and returns at once, so the API never hits its 30-second limit."""
    boto3.client("lambda").invoke(
        FunctionName=os.environ["WORKER_FUNCTION"],
        InvocationType="Event",
        Payload=json.dumps({"job_id": job_id}).encode(),
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


def current_user(event: dict[str, Any]) -> dict[str, Any] | None:
    settings = load_settings()
    return sessions.verify(sessions.read_cookie(event.get("cookies"), sessions.SESSION_COOKIE) or "", settings.session_secret, "session")


def not_signed_in() -> dict[str, Any]:
    return json_response(401, {"error": "not signed in"})


def me(event: dict[str, Any]) -> dict[str, Any]:
    user = current_user(event)
    if user is None:
        return not_signed_in()
    return json_response(200, {field: user.get(field) for field in auth.USER_FIELDS})


def create_job(event: dict[str, Any]) -> dict[str, Any]:
    user = current_user(event)
    if user is None:
        return not_signed_in()

    store = job_store()
    job = store.create(owner_id=str(user["id"]), login=user["login"])
    try:
        start_worker(job["job_id"])
    except (BotoCoreError, ClientError):
        store.fail(job["job_id"], "Could not start the job. Please try again.")
        return json_response(502, {"error": "Could not start the job. Please try again."})
    return json_response(202, jobs.public_view(job))


def get_job(event: dict[str, Any]) -> dict[str, Any]:
    user = current_user(event)
    if user is None:
        return not_signed_in()

    job_id = (event.get("pathParameters") or {}).get("job_id")
    job = job_store().get(job_id) if job_id else None
    # Someone else's job answers 404, not 403, so job IDs can't be probed.
    if job is None or job["owner_id"] != str(user["id"]):
        return json_response(404, {"error": "job not found"})
    return json_response(200, jobs.public_view(job))


ROUTES = {
    "GET /health": health,
    "GET /auth/login": login,
    "GET /auth/callback": callback,
    "GET /me": me,
    "POST /jobs": create_job,
    "GET /jobs/{job_id}": get_job,
}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """AWS Lambda entry point for the HTTP API (payload format 2.0)."""
    route = event.get("routeKey", "")
    action = ROUTES.get(route)
    if action is None:
        return json_response(404, {"error": "not found", "route": route})
    return action(event)
