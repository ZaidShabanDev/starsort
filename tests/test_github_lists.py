import json

import httpx
import pytest

from starsort.github import API_URL
from starsort.github_lists import (
    MANAGED_MARKER,
    GitHubGraphQL,
    ListsError,
    OrgRestrictedError,
    RateLimitedError,
    StarList,
    apply_sync,
    plan_sync,
)

MANAGED = MANAGED_MARKER


def managed_list(list_id: str, name: str, *items: str) -> StarList:
    return StarList(id=list_id, name=name, description=MANAGED, item_ids=set(items))


def own_list(list_id: str, name: str, *items: str) -> StarList:
    return StarList(id=list_id, name=name, description="my own list", item_ids=set(items))


# ---------- plan_sync (pure logic) ----------

def test_creates_lists_for_new_categories_but_never_for_other():
    plan = plan_sync([], {"a/one": "AI Agents", "b/two": "Other"}, {"a/one": "R1", "b/two": "R2"})

    assert plan.lists_to_create == ["AI Agents"]
    assert [(c.full_name, c.target) for c in plan.changes] == [("a/one", "AI Agents")]


def test_repo_already_in_the_right_list_needs_no_change():
    lists = [managed_list("L1", "AI Agents", "R1")]

    plan = plan_sync(lists, {"a/one": "AI Agents"}, {"a/one": "R1"})

    assert plan.lists_to_create == [] and plan.changes == []


def test_existing_list_is_reused_by_name_ignoring_case():
    lists = [own_list("L1", "ai agents")]

    plan = plan_sync(lists, {"a/one": "AI Agents"}, {"a/one": "R1"})

    assert plan.lists_to_create == []
    assert plan.changes[0].target == "AI Agents"


def test_moved_repo_leaves_old_starsort_list_but_keeps_users_own_lists():
    lists = [managed_list("L1", "Graphics", "R1"), managed_list("L2", "AI Agents"), own_list("MINE", "Favorites", "R1")]

    plan = plan_sync(lists, {"a/one": "AI Agents"}, {"a/one": "R1"})

    [change] = plan.changes
    assert change.target == "AI Agents"
    assert change.keep_list_ids == frozenset({"MINE"})


def test_repo_in_other_is_only_removed_from_starsort_lists():
    lists = [managed_list("L1", "Graphics", "R1"), own_list("MINE", "Favorites", "R1", "R2")]

    plan = plan_sync(lists, {"a/one": "Other", "b/two": "Other"}, {"a/one": "R1", "b/two": "R2"})

    [change] = plan.changes
    assert change.full_name == "a/one"
    assert change.target is None
    assert change.keep_list_ids == frozenset({"MINE"})


def test_repo_without_node_id_is_skipped():
    plan = plan_sync([], {"a/one": "AWS"}, {})

    assert plan.changes == []


# ---------- apply_sync ----------

class FakeGitHub:
    def __init__(self, rate_limited_after: int | None = None, restricted_items: tuple[str, ...] = ()):
        self.created: list[str] = []
        self.private: list[bool] = []
        self.updates: list[tuple[str, list[str]]] = []
        self.rate_limited_after = rate_limited_after
        self.restricted_items = restricted_items

    def create_list(self, name, private=False):
        self.created.append(name)
        self.private.append(private)
        return f"NEW-{name}"

    def set_lists_for_item(self, item_id, list_ids):
        if self.rate_limited_after is not None and len(self.updates) >= self.rate_limited_after:
            raise RateLimitedError("slow down")
        if item_id in self.restricted_items:
            raise OrgRestrictedError({"awslabs"})
        self.updates.append((item_id, list_ids))


THREE_REPOS = ({"a/one": "AWS", "b/two": "AWS", "c/three": "AWS"}, {"a/one": "R1", "b/two": "R2", "c/three": "R3"})


def test_apply_creates_lists_first_and_sends_users_own_lists_back():
    lists = [own_list("MINE", "Favorites", "R1")]
    plan = plan_sync(lists, {"a/one": "AWS"}, {"a/one": "R1"})
    github = FakeGitHub()

    result = apply_sync(github, plan, lists)

    assert github.created == ["AWS"]
    assert github.updates == [("R1", ["MINE", "NEW-AWS"])]
    assert (result.lists_created, result.repos_updated, result.complete) == (1, 1, True)


def test_apply_can_create_private_lists():
    github = FakeGitHub()

    apply_sync(github, plan_sync([], {"a/one": "AWS"}, {"a/one": "R1"}), [], private=True)

    assert github.private == [True]


def test_apply_stops_after_max_changes_and_reports_the_rest():
    plan = plan_sync([], *THREE_REPOS)
    github = FakeGitHub()

    result = apply_sync(github, plan, [], max_changes=2)

    assert len(github.updates) == 2
    assert (result.repos_updated, result.repos_to_update, result.complete) == (2, 3, False)


def test_apply_stops_cleanly_on_githubs_rate_limit():
    github = FakeGitHub(rate_limited_after=1)

    result = apply_sync(github, plan_sync([], *THREE_REPOS), [])

    assert (result.repos_updated, result.repos_to_update, result.complete) == (1, 3, False)


AWS_REPOS = (
    {"awslabs/mcp": "AWS", "awslabs/cdk": "AWS", "me/tool": "AWS"},
    {"awslabs/mcp": "R1", "awslabs/cdk": "R2", "me/tool": "R3"},
)


def test_apply_never_touches_repos_of_orgs_whose_lists_were_hidden():
    github = FakeGitHub()

    result = apply_sync(github, plan_sync([], *AWS_REPOS), [], skip_owners={"AWSLabs"})

    assert github.updates == [("R3", ["NEW-AWS"])]
    assert (result.repos_updated, result.repos_skipped, result.skipped_orgs, result.complete) == (1, 2, ("awslabs",), True)


def test_apply_skips_an_org_that_refuses_a_change_and_its_other_repos():
    github = FakeGitHub(restricted_items=("R1",))

    result = apply_sync(github, plan_sync([], *AWS_REPOS), [])

    assert github.updates == [("R3", ["NEW-AWS"])]  # R2 is skipped without asking GitHub
    assert (result.repos_updated, result.repos_skipped, result.complete) == (1, 2, True)


def test_apply_pauses_between_changes_and_reports_progress():
    pauses = []
    progress = []

    apply_sync(FakeGitHub(), plan_sync([], *THREE_REPOS), [], pause=lambda: pauses.append(1), on_progress=lambda *step: progress.append(step))

    assert len(pauses) == 3  # 1 list + 3 repos = 4 changes, with a pause between each
    assert progress == [(1, 3), (2, 3), (3, 3)]


# ---------- GitHubGraphQL client (mocked HTTP) ----------

def page(nodes, has_next=False, cursor=None):
    return {"pageInfo": {"hasNextPage": has_next, "endCursor": cursor}, "nodes": nodes}


def graphql_client(handler) -> GitHubGraphQL:
    return GitHubGraphQL("token", client=httpx.Client(base_url=API_URL, transport=httpx.MockTransport(handler)))


def test_fetch_lists_follows_pagination_of_lists_and_items():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        query, variables = body["query"], body["variables"]
        if "node(id" in query:
            data = {"node": {"items": page([{"id": "R3"}, {}])}}
        elif variables.get("after") is None:
            first_list = {"id": "L1", "name": "AWS", "description": MANAGED, "items": page([{"id": "R1"}], True, "c1")}
            data = {"viewer": {"lists": page([first_list], True, "lists-c1")}}
        else:
            second_list = {"id": "L2", "name": "Mine", "description": None, "items": page([{"id": "R2"}])}
            data = {"viewer": {"lists": page([second_list])}}
        return httpx.Response(200, json={"data": data})

    with graphql_client(handler) as github:
        lists = github.fetch_lists()

    assert [(l.id, l.managed, l.item_ids) for l in lists] == [("L1", True, {"R1", "R3"}), ("L2", False, {"R2"})]


def test_graphql_errors_raise_lists_error():
    def handler(request):
        return httpx.Response(200, json={"errors": [{"message": "Resource not accessible by personal access token"}]})

    with graphql_client(handler) as github:
        with pytest.raises(ListsError, match="not accessible"):
            github.viewer_login()


def test_create_list_marks_it_as_managed_by_starsort():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"data": {"createUserList": {"list": {"id": "L9"}}}})

    with graphql_client(handler) as github:
        assert github.create_list("AWS") == "L9"

    assert sent[0]["variables"] == {"name": "AWS", "description": MANAGED, "isPrivate": False}


def test_secondary_rate_limit_raises_rate_limited_error():
    def handler(request):
        return httpx.Response(403, json={"message": "You have exceeded a secondary rate limit. Please wait a few minutes."})

    with graphql_client(handler) as github:
        with pytest.raises(RateLimitedError):
            github.set_lists_for_item("R1", ["L1"])


RESTRICTED = (
    "Although you appear to have the correct authorization credentials, the `awslabs` organization has enabled "
    "OAuth App access restrictions, meaning that data access to third-parties is limited."
)


def test_fetch_lists_keeps_what_it_can_read_when_an_org_blocks_the_app():
    def handler(request):
        items = page([{"id": "R1"}, None])
        lists = page([{"id": "L1", "name": "AWS", "description": MANAGED, "items": items}])
        return httpx.Response(200, json={"data": {"viewer": {"lists": lists}}, "errors": [{"type": "FORBIDDEN", "message": RESTRICTED}]})

    with graphql_client(handler) as github:
        lists = github.fetch_lists()

    assert lists[0].item_ids == {"R1"}
    assert github.restricted_orgs == {"awslabs"}


def test_change_refused_by_an_org_raises_org_restricted_error():
    def handler(request):
        return httpx.Response(200, json={"data": None, "errors": [{"type": "FORBIDDEN", "message": RESTRICTED}]})

    with graphql_client(handler) as github:
        with pytest.raises(OrgRestrictedError) as error:
            github.set_lists_for_item("R1", ["L1"])

    assert error.value.orgs == {"awslabs"}


def test_graphql_rate_limited_error_raises_rate_limited_error():
    def handler(request):
        return httpx.Response(200, json={"errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]})

    with graphql_client(handler) as github:
        with pytest.raises(RateLimitedError):
            github.viewer_login()
