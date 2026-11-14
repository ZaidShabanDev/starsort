import httpx

from starsort.models import Repo

API_URL = "https://api.github.com"
PER_PAGE = 100

AppAuth = tuple[str, str]  # an OAuth App's (client_id, client_secret), sent as HTTP Basic auth


class GitHubError(Exception):
    pass


def build_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "starsort",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def parse_repo(item: dict) -> Repo:
    return Repo(
        full_name=item["full_name"],
        node_id=item.get("node_id"),
        url=item["html_url"],
        description=item.get("description"),
        topics=item.get("topics") or [],
        language=item.get("language"),
        stars=item.get("stargazers_count", 0),
        updated_at=item["updated_at"],
    )


def _raise_for_error(response: httpx.Response, username: str) -> None:
    if response.status_code == 404:
        raise GitHubError(f"GitHub user '{username}' was not found.")
    if response.status_code in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0":
        raise GitHubError("GitHub rate limit reached. Set GITHUB_TOKEN to get a higher limit.")
    if response.is_error:
        raise GitHubError(f"GitHub API error {response.status_code}: {response.text[:200]}")


def fetch_starred(
    username: str,
    token: str | None = None,
    known: set[str] | None = None,
    client: httpx.Client | None = None,
    app_auth: AppAuth | None = None,
) -> list[Repo]:
    """Return starred repos, newest star first.

    If `known` is given, stop at the first repo already in it, so only newly starred repos are returned.
    `app_auth` gives the 5,000 requests/hour OAuth App limit for public data without any user's token.
    """
    owns_client = client is None
    client = client or httpx.Client(base_url=API_URL, timeout=30)
    known = known or set()
    repos: list[Repo] = []
    try:
        url: str | None = f"/users/{username}/starred"
        params: dict | None = {"per_page": PER_PAGE}
        while url:
            response = client.get(url, params=params, headers=build_headers(token), auth=app_auth)
            _raise_for_error(response, username)
            for item in response.json():
                if item["full_name"] in known:
                    return repos
                repos.append(parse_repo(item))
            next_link = response.links.get("next")
            url = next_link["url"] if next_link else None
            params = None  # the "next" URL already carries the query string
        return repos
    finally:
        if owns_client:
            client.close()
