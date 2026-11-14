import base64
import hashlib
import urllib.parse

import pytest

from starsort.web import auth


class FakeSend:
    """Stands in for the HTTP call: records the request and returns a canned JSON body."""

    def __init__(self, body: dict):
        self.body = body
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.body


def test_pkce_challenge_is_sha256_of_verifier():
    verifier, challenge = auth.new_pkce()

    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert 43 <= len(verifier) <= 128


def test_authorize_url_has_state_and_pkce():
    url = auth.authorize_url("cid", "https://api.example.com/auth/callback", "state123", "challenge456")

    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert url.startswith(auth.AUTHORIZE_URL)
    assert query["client_id"] == ["cid"]
    assert query["redirect_uri"] == ["https://api.example.com/auth/callback"]
    assert query["state"] == ["state123"]
    assert query["code_challenge"] == ["challenge456"]
    assert query["code_challenge_method"] == ["S256"]


def test_exchange_code_posts_secret_and_verifier():
    send = FakeSend({"access_token": "gho_token"})

    token = auth.exchange_code("cid", "csecret", "code1", "https://cb", "verifier1", send=send)

    request = send.requests[0]
    form = urllib.parse.parse_qs(request.data.decode())
    assert token == "gho_token"
    assert request.full_url == auth.TOKEN_URL
    assert request.get_method() == "POST"
    assert request.get_header("Accept") == "application/json"
    assert form["client_secret"] == ["csecret"]
    assert form["code_verifier"] == ["verifier1"]


def test_exchange_code_raises_on_github_error():
    send = FakeSend({"error": "bad_verification_code", "error_description": "The code is incorrect or expired."})

    with pytest.raises(auth.OAuthError, match="incorrect or expired"):
        auth.exchange_code("cid", "csecret", "old", "https://cb", "v", send=send)


def test_fetch_user_keeps_only_public_fields():
    send = FakeSend({"login": "octo", "id": 1, "name": "Octo Cat", "avatar_url": "https://a", "email": "x@y"})

    user = auth.fetch_user("gho_token", send=send)

    assert user == {"login": "octo", "id": 1, "name": "Octo Cat", "avatar_url": "https://a"}
    assert send.requests[0].get_header("Authorization") == "Bearer gho_token"
