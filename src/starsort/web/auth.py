"""GitHub OAuth web flow with PKCE. Standard library only, so the Lambda zip needs no packages.

Docs: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps
"""

import base64
import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
TIMEOUT_SECONDS = 5
USER_FIELDS = ("login", "id", "name", "avatar_url")

Send = Callable[[urllib.request.Request], dict[str, Any]]


class OAuthError(Exception):
    pass


def send_request(request: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise OAuthError(f"GitHub request failed: {error}") from error


def new_pkce() -> tuple[str, str]:
    """A (verifier, challenge) pair. GitHub only accepts the S256 method."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return f"{AUTHORIZE_URL}?{query}"


def exchange_code(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    verifier: str,
    send: Send = send_request,
) -> str:
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }).encode()
    request = urllib.request.Request(
        TOKEN_URL, data=data, method="POST", headers={"Accept": "application/json", "User-Agent": "starsort"}
    )
    body = send(request)
    token = body.get("access_token")
    if not token:
        raise OAuthError(body.get("error_description") or body.get("error") or "GitHub returned no access token.")
    return token


def fetch_user(token: str, send: Send = send_request) -> dict[str, Any]:
    request = urllib.request.Request(USER_URL, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "starsort",
    })
    user = send(request)
    if "login" not in user or "id" not in user:
        raise OAuthError("GitHub did not return a user.")
    return {field: user.get(field) for field in USER_FIELDS}
