import hmac
import json
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from starsort.web import auth, jobs, limits, sessions

LOGIN_COOKIE = "starsort_login"
LOGIN_COOKIE_PATH = "/api/auth"
LOGIN_SECONDS = 10 * 60
AFTER_LOGIN_PATH = "/dashboard"  # an Angular route, served through the same CloudFront address

BUDGET_MESSAGE = "The free trial is paused until next month: this month's AI budget is used up."
LIMIT_MESSAGES = {
    "running": "A job is already running for your account. Wait for it to finish.",
    "weekly": "The free trial allows one run per week.",  # the UI shows the date from retry_at
}


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    session_secret: str
    site_url: str  # the CloudFront address; requests reach the Lambda with API Gateway's own host name


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
        site_url=os.environ["SITE_URL"],
    )


@lru_cache(maxsize=1)
def job_store() -> jobs.JobStore:
    return jobs.JobStore(boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"]))


@lru_cache(maxsize=1)
def usage_store() -> limits.UsageStore:
    return limits.UsageStore(boto3.resource("dynamodb").Table(os.environ["USAGE_TABLE"]))


@lru_cache(maxsize=1)
def spend_counter() -> limits.SpendCounter:
    return limits.SpendCounter(boto3.resource("dynamodb").Table(os.environ["SPEND_TABLE"]))


@lru_cache(maxsize=1)
def load_limits() -> limits.Limits:
    return limits.Limits.from_env()


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


def callback_url(settings: Settings) -> str:
    return f"{settings.site_url}/api/auth/callback"


def health(event: dict[str, Any]) -> dict[str, Any]:
    return json_response(200, {"status": "ok", "service": "starsort"})


def login(event: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    state = secrets.token_urlsafe(24)
    verifier, challenge = auth.new_pkce()
    login_data = sessions.sign({"state": state, "verifier": verifier}, settings.session_secret, "login", LOGIN_SECONDS)
    return redirect(
        auth.authorize_url(settings.client_id, callback_url(settings), state, challenge),
        [sessions.make_cookie(LOGIN_COOKIE, login_data, LOGIN_SECONDS, path=LOGIN_COOKIE_PATH)],
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
        token = auth.exchange_code(settings.client_id, settings.client_secret, query["code"], callback_url(settings), login_data["verifier"])
        user = auth.fetch_user(token)
    except auth.OAuthError as error:
        return json_response(502, {"error": str(error)})

    # The GitHub token is dropped here on purpose: only the public profile goes into the session.
    session = sessions.sign(user, settings.session_secret, "session", sessions.SESSION_SECONDS)
    return redirect(AFTER_LOGIN_PATH, [
        sessions.make_cookie(sessions.SESSION_COOKIE, session, sessions.SESSION_SECONDS),
        sessions.clear_cookie(LOGIN_COOKIE, path=LOGIN_COOKIE_PATH),
    ])


def logout(event: dict[str, Any]) -> dict[str, Any]:
    return json_response(200, {"status": "signed out"}, [sessions.clear_cookie(sessions.SESSION_COOKIE)])


def current_user(event: dict[str, Any]) -> dict[str, Any] | None:
    settings = load_settings()
    return sessions.verify(sessions.read_cookie(event.get("cookies"), sessions.SESSION_COOKIE) or "", settings.session_secret, "session")


def not_signed_in() -> dict[str, Any]:
    return json_response(401, {"error": "not signed in"})


def budget_used_up(trial: limits.Limits) -> bool:
    return spend_counter().month_total() >= trial.monthly_cap_micro_usd


def trial_status(user: dict[str, Any]) -> dict[str, Any]:
    """Whether this user could start a run now, and if not why and until when. Read-only."""
    trial = load_limits()
    unlimited = trial.is_unlimited(user["login"])
    status: dict[str, Any] = {"unlimited": unlimited, "can_run": False, "reason": None, "retry_at": None, "message": None}
    if budget_used_up(trial):
        return {**status, "reason": "budget", "message": BUDGET_MESSAGE}

    check = usage_store().check(str(user["id"]), weekly=not unlimited)
    if check.allowed:
        return {**status, "can_run": True}
    return {**status, "reason": check.reason, "retry_at": check.retry_at, "message": LIMIT_MESSAGES[check.reason]}


def me(event: dict[str, Any]) -> dict[str, Any]:
    user = current_user(event)
    if user is None:
        return not_signed_in()
    profile = {field: user.get(field) for field in auth.USER_FIELDS}
    return json_response(200, {**profile, "trial": trial_status(user)})


def create_job(event: dict[str, Any]) -> dict[str, Any]:
    user = current_user(event)
    if user is None:
        return not_signed_in()

    trial = load_limits()
    if budget_used_up(trial):
        return json_response(503, {"error": BUDGET_MESSAGE, "reason": "budget"})

    owner_id = str(user["id"])
    check = usage_store().try_start(owner_id, weekly=not trial.is_unlimited(user["login"]))
    if not check.allowed:
        return json_response(429, {"error": LIMIT_MESSAGES[check.reason], "reason": check.reason, "retry_at": check.retry_at})

    store = job_store()
    job = store.create(owner_id=owner_id, login=user["login"])
    try:
        start_worker(job["job_id"])
    except (BotoCoreError, ClientError):
        store.fail(job["job_id"], "Could not start the job. Please try again.")
        usage_store().finish(owner_id, refund=True)
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


# Everything lives under /api: CloudFront sends /api/* here and every other path to the Angular site.
ROUTES = {
    "GET /api/health": health,
    "GET /api/auth/login": login,
    "GET /api/auth/callback": callback,
    "POST /api/auth/logout": logout,
    "GET /api/me": me,
    "POST /api/jobs": create_job,
    "GET /api/jobs/{job_id}": get_job,
}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """AWS Lambda entry point for the HTTP API (payload format 2.0)."""
    route = event.get("routeKey", "")
    action = ROUTES.get(route)
    if action is None:
        return json_response(404, {"error": "not found", "route": route})
    return action(event)
