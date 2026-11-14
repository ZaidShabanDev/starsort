import base64

import httpx
import pytest

from starsort.github import API_URL, GitHubError, fetch_starred


def make_item(full_name: str, **overrides) -> dict:
    item = {
        "full_name": full_name,
        "node_id": f"R_{full_name}",
        "html_url": f"https://github.com/{full_name}",
        "description": f"Description of {full_name}",
        "topics": ["python"],
        "language": "Python",
        "stargazers_count": 42,
        "updated_at": "2026-01-15T10:00:00Z",
    }
    item.update(overrides)
    return item


def make_client(handler) -> httpx.Client:
    return httpx.Client(base_url=API_URL, transport=httpx.MockTransport(handler))


def paged_handler(pages: list[list[dict]], seen_requests: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen_requests is not None:
            seen_requests.append(request)
        page = int(request.url.params.get("page", "1"))
        headers = {}
        if page < len(pages):
            headers["Link"] = f'<{API_URL}/users/octo/starred?per_page=100&page={page + 1}>; rel="next"'
        return httpx.Response(200, json=pages[page - 1], headers=headers)

    return handler


def test_fetch_follows_pagination():
    pages = [[make_item("a/one"), make_item("a/two")], [make_item("b/three")]]
    with make_client(paged_handler(pages)) as client:
        repos = fetch_starred("octo", client=client)

    assert [repo.full_name for repo in repos] == ["a/one", "a/two", "b/three"]


def test_fetch_maps_fields():
    item = make_item("a/one", description=None, topics=None, language=None, stargazers_count=7)
    with make_client(paged_handler([[item]])) as client:
        repo = fetch_starred("octo", client=client)[0]

    assert repo.url == "https://github.com/a/one"
    assert repo.node_id == "R_a/one"
    assert repo.description is None
    assert repo.topics == []
    assert repo.language is None
    assert repo.stars == 7
    assert repo.updated_at.year == 2026


def test_token_is_sent_as_bearer_header():
    requests: list[httpx.Request] = []
    with make_client(paged_handler([[make_item("a/one")]], requests)) as client:
        fetch_starred("octo", token="secret-token", client=client)

    assert requests[0].headers["Authorization"] == "Bearer secret-token"


def test_no_token_sends_no_auth_header():
    requests: list[httpx.Request] = []
    with make_client(paged_handler([[make_item("a/one")]], requests)) as client:
        fetch_starred("octo", client=client)

    assert "Authorization" not in requests[0].headers


def test_app_auth_is_sent_as_basic_auth():
    requests: list[httpx.Request] = []
    with make_client(paged_handler([[make_item("a/one")]], requests)) as client:
        fetch_starred("octo", client=client, app_auth=("cid", "csecret"))

    assert requests[0].headers["Authorization"] == "Basic " + base64.b64encode(b"cid:csecret").decode()


def test_fetch_stops_at_first_known_repo():
    requests: list[httpx.Request] = []
    pages = [[make_item("new/one"), make_item("old/two")], [make_item("old/three")]]
    with make_client(paged_handler(pages, requests)) as client:
        repos = fetch_starred("octo", known={"old/two", "old/three"}, client=client)

    assert [repo.full_name for repo in repos] == ["new/one"]
    assert len(requests) == 1


def test_unknown_user_raises_clear_error():
    with make_client(lambda request: httpx.Response(404, json={"message": "Not Found"})) as client:
        with pytest.raises(GitHubError, match="not found"):
            fetch_starred("ghost", client=client)


def test_rate_limit_suggests_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "rate limit"}, headers={"x-ratelimit-remaining": "0"})

    with make_client(handler) as client:
        with pytest.raises(GitHubError, match="GITHUB_TOKEN"):
            fetch_starred("octo", client=client)
