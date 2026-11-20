import hmac
import json
import logging
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from starsort.web import auth, jobs, limits, sessions

logger = logging.getLogger(__name__)

LOGIN_COOKIE = "starsort_login"
LOGIN_COOKIE_PATH = "/api/auth"
LOGIN_SECONDS = 10 * 60
AFTER_LOGIN_PATH = "/dashboard"  # an Angular route, served through the same CloudFront address
LISTS_PURPOSE = "lists"  # marks the login cookie of the "Sync to my GitHub Lists" approval

BUDGET_MESSAGE = limits.BUDGET_MESSAGE
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


def start_sync_worker(job_id: str, token: str, private: bool) -> None:
    """The token travels only inside this async event: it is never written to DynamoDB or the logs."""
    boto3.client("lambda").invoke(
        FunctionName=os.environ["WORKER_FUNCTION"],
        InvocationType="Event",
        Payload=json.dumps({"job_id": job_id, "kind": jobs.LISTS, "token": token, "private": private}).encode(),
    )


def revoke_quietly(settings: Settings, token: str) -> None:
    try:
        auth.revoke_token(settings.client_id, settings.client_secret, token)
    except auth.OAuthError:
        logger.warning("Could not revoke a GitHub token; it stays valid until the user removes the app.")


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


def github_redirect(settings: Settings, extra: dict[str, Any] | None = None, scope: str | None = None) -> dict[str, Any]:
    """Send the browser to GitHub's approval page, with state + PKCE kept in a short-lived signed cookie."""
    state = secrets.token_urlsafe(24)
    verifier, challenge = auth.new_pkce()
    login_data = sessions.sign({"state": state, "verifier": verifier, **(extra or {})}, settings.session_secret, "login", LOGIN_SECONDS)
    return redirect(
        auth.authorize_url(settings.client_id, callback_url(settings), state, challenge, scope),
        [sessions.make_cookie(LOGIN_COOKIE, login_data, LOGIN_SECONDS, path=LOGIN_COOKIE_PATH)],
    )


def login(event: dict[str, Any]) -> dict[str, Any]:
    return github_redirect(load_settings())


def lists_connect(event: dict[str, Any]) -> dict[str, Any]:
    """Step 1 of "Sync to my GitHub Lists": ask GitHub for the one extra permission, only when the user clicks."""
    settings = load_settings()
    if current_user(event) is None:
        return redirect("/", [])
    private = (event.get("queryStringParameters") or {}).get("private") == "1"
    return github_redirect(settings, {"purpose": LISTS_PURPOSE, "private": private}, scope=auth.LISTS_SCOPE)


def state_matches(query: dict[str, str], login_data: dict[str, Any]) -> bool:
    return bool(query.get("code")) and hmac.compare_digest(query.get("state", "").encode(), login_data["state"].encode())


def callback(event: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    query = event.get("queryStringParameters") or {}
    login_data = sessions.verify(sessions.read_cookie(event.get("cookies"), LOGIN_COOKIE) or "", settings.session_secret, "login")
    if login_data and login_data.get("purpose") == LISTS_PURPOSE:
        return lists_callback(event, settings, query, login_data)

    if query.get("error"):
        return json_response(400, {"error": query.get("error_description") or query["error"]})
    if not login_data or not state_matches(query, login_data):
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


def lists_callback(event: dict[str, Any], settings: Settings, query: dict[str, str], login_data: dict[str, Any]) -> dict[str, Any]:
    """Step 2: GitHub sent the user back. The dashboard shows the outcome from ?lists=<status>."""

    def back(status: str) -> dict[str, Any]:
        return redirect(f"{AFTER_LOGIN_PATH}?lists={status}", [sessions.clear_cookie(LOGIN_COOKIE, path=LOGIN_COOKIE_PATH)])

    user = current_user(event)
    if user is None:
        return back("signed_out")
    if query.get("error"):
        return back("denied")
    if not state_matches(query, login_data):
        return back("expired")
    try:
        token, scopes = auth.exchange_code_and_scopes(
            settings.client_id, settings.client_secret, query["code"], callback_url(settings), login_data["verifier"]
        )
    except auth.OAuthError:
        return back("error")

    status = start_lists_sync(user, token, scopes, login_data.get("private") is True)
    if status != "started":
        revoke_quietly(settings, token)
    return back(status)


def start_lists_sync(user: dict[str, Any], token: str, scopes: set[str], private: bool) -> str:
    if auth.LISTS_SCOPE not in scopes:
        return "denied"
    try:
        github_user = auth.fetch_user(token)
    except auth.OAuthError:
        return "error"
    if github_user["id"] != user["id"]:
        return "wrong_account"  # approved with another GitHub account than the one signed in

    owner_id = str(user["id"])
    check = usage_store().try_start_sync(owner_id)
    if not check.allowed:
        return "no_run" if check.reason == "no_run" else "busy"

    store = job_store()
    job = store.create(owner_id=owner_id, login=user["login"], kind=jobs.LISTS)
    usage_store().remember_job(owner_id, job["job_id"], kind=jobs.LISTS)
    try:
        start_sync_worker(job["job_id"], token, private)
    except (BotoCoreError, ClientError):
        store.fail(job["job_id"], "Could not start the sync. Please try again.")
        usage_store().finish(owner_id)
        return "error"
    return "started"


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
    usage_store().remember_job(owner_id, job["job_id"])
    try:
        start_worker(job["job_id"])
    except (BotoCoreError, ClientError):
        store.fail(job["job_id"], "Could not start the job. Please try again.")
        usage_store().finish(owner_id, refund=True)
        return json_response(502, {"error": "Could not start the job. Please try again."})
    return json_response(202, jobs.public_view(job))


def own_job_response(user: dict[str, Any], job_id: str | None, not_found: str) -> dict[str, Any]:
    job = job_store().get(job_id) if job_id else None
    # Someone else's job answers 404, not 403, so job IDs can't be probed.
    if job is None or job["owner_id"] != str(user["id"]):
        return json_response(404, {"error": not_found})
    return json_response(200, jobs.public_view(job))


def get_job(event: dict[str, Any]) -> dict[str, Any]:
    user = current_user(event)
    if user is None:
        return not_signed_in()
    return own_job_response(user, (event.get("pathParameters") or {}).get("job_id"), "job not found")


def latest_job(event: dict[str, Any]) -> dict[str, Any]:
    """The user's most recent run (or Lists sync with ?kind=lists), so the dashboard can pick it up again after a refresh."""
    user = current_user(event)
    if user is None:
        return not_signed_in()
    kind = jobs.LISTS if (event.get("queryStringParameters") or {}).get("kind") == jobs.LISTS else jobs.RUN
    return own_job_response(user, usage_store().last_job_id(str(user["id"]), kind), "no runs yet")


# Everything lives under /api: CloudFront sends /api/* here and every other path to the Angular site.
ROUTES = {
    "GET /api/health": health,
    "GET /api/auth/login": login,
    "GET /api/auth/callback": callback,
    "POST /api/auth/logout": logout,
    "GET /api/auth/lists": lists_connect,
    "GET /api/me": me,
    "POST /api/jobs": create_job,
    "GET /api/jobs/latest": latest_job,  # API Gateway matches this fixed path before {job_id}
    "GET /api/jobs/{job_id}": get_job,
}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """AWS Lambda entry point for the HTTP API (payload format 2.0)."""
    route = event.get("routeKey", "")
    action = ROUTES.get(route)
    if action is None:
        return json_response(404, {"error": "not found", "route": route})
    return action(event)
