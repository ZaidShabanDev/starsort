import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import httpx

from starsort.github import API_URL, build_headers
from starsort.models import OTHER_CATEGORY

MANAGED_MARKER = "Managed by starsort"
RESTRICTED_ORG = re.compile(r"the `([^`]+)` organization has enabled OAuth App access restrictions")

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
mutation($name: String!, $description: String!, $isPrivate: Boolean!) {
  createUserList(input: {name: $name, description: $description, isPrivate: $isPrivate}) {
    list { id }
  }
}"""

SET_LISTS_MUTATION = """
mutation($itemId: ID!, $listIds: [ID!]!) {
  updateUserListsForItem(input: {itemId: $itemId, listIds: $listIds}) {
    clientMutationId
  }
}"""

DELETE_LIST_MUTATION = """
mutation($listId: ID!) {
  deleteUserList(input: {listId: $listId}) {
    clientMutationId
  }
}"""


class ListsError(Exception):
    pass


class RateLimitedError(ListsError):
    """GitHub's secondary rate limit (about 80 changes a minute, 500 an hour). Stop and continue in a later sync."""


class OrgRestrictedError(ListsError):
    """An organization blocks unapproved OAuth Apps (e.g. awslabs), so the web app can't touch its repos."""

    def __init__(self, orgs: set[str]):
        super().__init__(f"GitHub: the {', '.join(sorted(orgs))} organization blocks apps like Starsort.")
        self.orgs = orgs


def _restricted_orgs(errors: list[dict]) -> set[str]:
    """Orgs named by OAuth App access-restriction errors; empty if any error is something else."""
    orgs = set()
    for error in errors:
        match = RESTRICTED_ORG.search(error.get("message", ""))
        if not match:
            return set()
        orgs.add(match.group(1).lower())
    return orgs


@dataclass
class StarList:
    id: str
    name: str
    description: str | None = None
    item_ids: set[str] = field(default_factory=set)
    other_items: int = 0  # items we couldn't read (hidden by an org, or not a repo): the list is never "empty"

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


@dataclass(frozen=True)
class SyncResult:
    lists_created: int
    repos_updated: int
    repos_to_update: int  # what the plan needed; more than updated + skipped when the sync stopped early
    repos_skipped: int = 0  # repos of organizations that block OAuth Apps
    skipped_orgs: tuple[str, ...] = ()
    lists_deleted: int = 0  # starsort's own lists left empty by this sync

    @property
    def complete(self) -> bool:
        return self.repos_updated + self.repos_skipped == self.repos_to_update


def lists_to_delete(lists: list[StarList], applied: list[RepoChange]) -> list[StarList]:
    """Starsort's own lists that are empty once these changes are applied (a moved repo stays only in its target list).
    Lists with unreadable items are never deleted. Pure function, so --dry-run can show it."""
    moved = {change.node_id for change in applied}
    deletable = []
    for star_list in lists:
        if not star_list.managed or star_list.other_items:
            continue
        stays = star_list.item_ids - moved
        arrives = any(change.target and change.target.lower() == star_list.name.lower() for change in applied)
        if not stays and not arrives:
            deletable.append(star_list)
    return deletable


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
        self.restricted_orgs: set[str] = set()  # orgs whose repos were hidden from fetch_lists

    def __enter__(self) -> "GitHubGraphQL":
        return self

    def __exit__(self, *exc_info) -> None:
        if self.owns_client:
            self.client.close()

    def run(self, query: str, variables: dict | None = None, partial_ok: bool = False) -> dict:
        """partial_ok: keep the data when the only errors are orgs blocking OAuth Apps (their repos come back as null)."""
        response = self.client.post(
            "/graphql", json={"query": query, "variables": variables or {}}, headers=build_headers(self.token)
        )
        if response.status_code == 401:
            raise ListsError("GitHub rejected the token (401). Check that it is valid (the CLI reads GITHUB_TOKEN).")
        if response.status_code in (403, 429) and "rate limit" in response.text.lower():
            raise RateLimitedError(f"GitHub's rate limit was reached (HTTP {response.status_code}): {response.text[:300]}")
        if response.is_error:
            raise ListsError(f"GitHub GraphQL error {response.status_code}: {response.text[:200]}")
        body = response.json()
        if body.get("errors"):  # GraphQL reports most errors with HTTP 200
            if any(error.get("type") == "RATE_LIMITED" for error in body["errors"]):
                raise RateLimitedError(f"GitHub's rate limit was reached: {body['errors']}"[:400])
            orgs = _restricted_orgs(body["errors"])
            if orgs and partial_ok and body.get("data"):
                self.restricted_orgs |= orgs
                return body["data"]
            if orgs:
                raise OrgRestrictedError(orgs)
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
            page = self.run(LISTS_QUERY, {"after": after}, partial_ok=True)["viewer"]["lists"]
            for node in page["nodes"]:
                if not node:
                    continue
                star_list = StarList(id=node["id"], name=node["name"], description=node.get("description"))
                star_list.item_ids, star_list.other_items = self._collect_items(node["id"], node["items"])
                lists.append(star_list)
            if not page["pageInfo"]["hasNextPage"]:
                return lists
            after = page["pageInfo"]["endCursor"]

    def _collect_items(self, list_id: str, items_page: dict) -> tuple[set[str], int]:
        """Repo IDs in the list, plus how many items had no readable repo ID (hidden by an org, or not a repo)."""
        ids: set[str] = set()
        others = 0
        while True:
            for node in items_page["nodes"]:
                if node and node.get("id"):
                    ids.add(node["id"])
                else:
                    others += 1
            if not items_page["pageInfo"]["hasNextPage"]:
                return ids, others
            after = items_page["pageInfo"]["endCursor"]
            items_page = self.run(LIST_ITEMS_QUERY, {"id": list_id, "after": after}, partial_ok=True)["node"]["items"]

    def create_list(self, name: str, private: bool = False) -> str:
        data = self.run(CREATE_LIST_MUTATION, {"name": name, "description": MANAGED_MARKER, "isPrivate": private})
        return data["createUserList"]["list"]["id"]

    def set_lists_for_item(self, item_id: str, list_ids: list[str]) -> None:
        self.run(SET_LISTS_MUTATION, {"itemId": item_id, "listIds": list_ids})

    def delete_list(self, list_id: str) -> None:
        self.run(DELETE_LIST_MUTATION, {"listId": list_id})


def apply_sync(
    client: GitHubGraphQL,
    plan: SyncPlan,
    lists: list[StarList],
    on_step: Callable[[str], None] | None = None,
    *,
    private: bool = False,
    max_changes: int | None = None,
    pause: Callable[[], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    skip_owners: Iterable[str] = (),
) -> SyncResult:
    """Apply at most max_changes repos, creating each list just before its first repo, then delete starsort's emptied lists.
    Stops cleanly on GitHub's rate limit. Why repos of blocking orgs are skipped: see docs/Starsort/handoff_Starsort.md (P8)."""
    list_ids = {star_list.name.lower(): star_list.id for star_list in lists}
    total = len(plan.changes)
    created = updated = skipped = deleted = 0
    blocked = {owner.lower() for owner in skip_owners}
    skipped_orgs: set[str] = set()
    new_lists: list[StarList] = []
    applied: list[RepoChange] = []
    mutations = 0

    def before_mutation() -> None:
        nonlocal mutations
        if pause and mutations:
            pause()
        mutations += 1

    def list_id_for(category: str) -> str:
        nonlocal created
        key = category.lower()
        if key not in list_ids:
            before_mutation()
            list_ids[key] = client.create_list(category, private=private)
            new_lists.append(StarList(id=list_ids[key], name=category, description=MANAGED_MARKER))
            created += 1
            if on_step:
                on_step(f"created list: {category}")
        return list_ids[key]

    try:
        for change in plan.changes[:max_changes]:
            owner = change.full_name.split("/")[0].lower()
            if owner in blocked:
                skipped += 1
                skipped_orgs.add(owner)
            else:
                # updateUserListsForItem REPLACES the repo's lists, so send the user's own lists back too.
                ids = set(change.keep_list_ids)
                if change.target:
                    ids.add(list_id_for(change.target))
                before_mutation()
                try:
                    client.set_lists_for_item(change.node_id, sorted(ids))
                except OrgRestrictedError as error:
                    skipped += 1
                    skipped_orgs |= error.orgs
                    blocked |= error.orgs
                else:
                    updated += 1
                    applied.append(change)
                    if on_step:
                        on_step(f"{change.full_name} -> {change.target or '(removed from starsort lists)'}")
            if on_progress:
                on_progress(updated + skipped, total)

        if updated + skipped == total:  # after a partial sync, a list that looks empty may still get repos next time
            for star_list in lists_to_delete([*lists, *new_lists], applied):
                before_mutation()
                client.delete_list(star_list.id)
                deleted += 1
                if on_step:
                    on_step(f"deleted empty list: {star_list.name}")
    except RateLimitedError as error:
        if on_step:
            on_step(f"stopped early: {error}")
    return SyncResult(
        lists_created=created,
        repos_updated=updated,
        repos_to_update=total,
        repos_skipped=skipped,
        skipped_orgs=tuple(sorted(skipped_orgs)),
        lists_deleted=deleted,
    )
