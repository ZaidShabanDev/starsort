from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from starsort.github import API_URL, build_headers
from starsort.models import OTHER_CATEGORY

MANAGED_MARKER = "Managed by starsort"

VIEWER_QUERY = "query { viewer { login } }"

LISTS_QUERY = """
query($after: String) {
  viewer {
    lists(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        name
        description
        items(first: 100) {
          pageInfo { hasNextPage endCursor }
          nodes { ... on Repository { id } }
        }
      }
    }
  }
}"""

LIST_ITEMS_QUERY = """
query($id: ID!, $after: String) {
  node(id: $id) {
    ... on UserList {
      items(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { ... on Repository { id } }
      }
    }
  }
}"""

CREATE_LIST_MUTATION = """
mutation($name: String!, $description: String!) {
  createUserList(input: {name: $name, description: $description, isPrivate: false}) {
    list { id }
  }
}"""

SET_LISTS_MUTATION = """
mutation($itemId: ID!, $listIds: [ID!]!) {
  updateUserListsForItem(input: {itemId: $itemId, listIds: $listIds}) {
    clientMutationId
  }
}"""


class ListsError(Exception):
    pass


@dataclass
class StarList:
    id: str
    name: str
    description: str | None = None
    item_ids: set[str] = field(default_factory=set)

    @property
    def managed(self) -> bool:
        return MANAGED_MARKER in (self.description or "")


@dataclass(frozen=True)
class RepoChange:
    full_name: str
    node_id: str
    keep_list_ids: frozenset[str]  # the user's own lists, never touched
    target: str | None  # category list to put the repo in; None = only take it out of starsort's lists


@dataclass
class SyncPlan:
    lists_to_create: list[str]
    changes: list[RepoChange]


def plan_sync(lists: list[StarList], assignments: dict[str, str], node_ids: dict[str, str]) -> SyncPlan:
    """Work out the smallest set of changes. Pure function: no API calls, so it is safe for --dry-run."""
    by_name = {star_list.name.lower(): star_list for star_list in lists}
    managed_ids = {star_list.id for star_list in lists if star_list.managed}
    categories = sorted({c for c in assignments.values() if c != OTHER_CATEGORY}, key=str.lower)
    lists_to_create = [name for name in categories if name.lower() not in by_name]

    changes = []
    for full_name, category in assignments.items():
        node_id = node_ids.get(full_name)
        if not node_id:
            continue
        current = {star_list.id for star_list in lists if node_id in star_list.item_ids}
        keep = current - managed_ids
        target = None if category == OTHER_CATEGORY else category
        existing = by_name.get(target.lower()) if target else None

        if target and existing is None:
            changed = True  # its list doesn't exist yet
        else:
            changed = (keep | ({existing.id} if existing else set())) != current
        if changed:
            changes.append(RepoChange(full_name, node_id, frozenset(keep), target))

    return SyncPlan(lists_to_create, changes)


class GitHubGraphQL:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self.token = token
        self.owns_client = client is None
        self.client = client or httpx.Client(base_url=API_URL, timeout=30)

    def __enter__(self) -> "GitHubGraphQL":
        return self

    def __exit__(self, *exc_info) -> None:
        if self.owns_client:
            self.client.close()

    def run(self, query: str, variables: dict | None = None) -> dict:
        response = self.client.post(
            "/graphql", json={"query": query, "variables": variables or {}}, headers=build_headers(self.token)
        )
        if response.status_code == 401:
            raise ListsError("GitHub rejected GITHUB_TOKEN (401). Check that the token is valid.")
        if response.is_error:
            raise ListsError(f"GitHub GraphQL error {response.status_code}: {response.text[:200]}")
        body = response.json()
        if body.get("errors"):  # GraphQL reports most errors with HTTP 200
            message = "; ".join(error.get("message", "unknown error") for error in body["errors"])
            if "not accessible by personal access token" in message:
                message += ". Star lists need a classic token with the 'user' scope (fine-grained tokens can't create lists)."
            raise ListsError(f"GitHub: {message}")
        return body["data"]

    def viewer_login(self) -> str:
        return self.run(VIEWER_QUERY)["viewer"]["login"]

    def fetch_lists(self) -> list[StarList]:
        lists: list[StarList] = []
        after = None
        while True:
            page = self.run(LISTS_QUERY, {"after": after})["viewer"]["lists"]
            for node in page["nodes"]:
                star_list = StarList(id=node["id"], name=node["name"], description=node.get("description"))
                star_list.item_ids = self._collect_item_ids(node["id"], node["items"])
                lists.append(star_list)
            if not page["pageInfo"]["hasNextPage"]:
                return lists
            after = page["pageInfo"]["endCursor"]

    def _collect_item_ids(self, list_id: str, items_page: dict) -> set[str]:
        ids: set[str] = set()
        while True:
            ids.update(node["id"] for node in items_page["nodes"] if node and node.get("id"))
            if not items_page["pageInfo"]["hasNextPage"]:
                return ids
            after = items_page["pageInfo"]["endCursor"]
            items_page = self.run(LIST_ITEMS_QUERY, {"id": list_id, "after": after})["node"]["items"]

    def create_list(self, name: str) -> str:
        data = self.run(CREATE_LIST_MUTATION, {"name": name, "description": MANAGED_MARKER})
        return data["createUserList"]["list"]["id"]

    def set_lists_for_item(self, item_id: str, list_ids: list[str]) -> None:
        self.run(SET_LISTS_MUTATION, {"itemId": item_id, "listIds": list_ids})


def apply_sync(
    client: GitHubGraphQL,
    plan: SyncPlan,
    lists: list[StarList],
    on_step: Callable[[str], None] | None = None,
) -> None:
    list_ids = {star_list.name.lower(): star_list.id for star_list in lists}
    for name in plan.lists_to_create:
        list_ids[name.lower()] = client.create_list(name)
        if on_step:
            on_step(f"created list: {name}")

    for change in plan.changes:
        # updateUserListsForItem REPLACES the repo's lists, so send the user's own lists back too.
        ids = set(change.keep_list_ids)
        if change.target:
            ids.add(list_ids[change.target.lower()])
        client.set_lists_for_item(change.node_id, sorted(ids))
        if on_step:
            on_step(f"{change.full_name} -> {change.target or '(removed from starsort lists)'}")
