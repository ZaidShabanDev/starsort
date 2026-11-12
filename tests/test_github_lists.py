import json

import httpx
import pytest

from starsort.github import API_URL
from starsort.github_lists import (
    MANAGED_MARKER,
    GitHubGraphQL,
    ListsError,
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
    def __init__(self):
        self.created: list[str] = []
        self.updates: list[tuple[str, list[str]]] = []

    def create_list(self, name):
        self.created.append(name)
        return f"NEW-{name}"

    def set_lists_for_item(self, item_id, list_ids):
        self.updates.append((item_id, list_ids))


def test_apply_creates_lists_first_and_sends_users_own_lists_back():
    lists = [own_list("MINE", "Favorites", "R1")]
    plan = plan_sync(lists, {"a/one": "AWS"}, {"a/one": "R1"})
    github = FakeGitHub()

    apply_sync(github, plan, lists)

    assert github.created == ["AWS"]
    assert github.updates == [("R1", ["MINE", "NEW-AWS"])]


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

    assert sent[0]["variables"] == {"name": "AWS", "description": MANAGED}
