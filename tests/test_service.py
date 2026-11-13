from datetime import date, datetime, timezone

import pytest

from starsort.cache import StarCache
from starsort.models import BatchCategorization, Repo, RepoAssignment
from starsort.service import FileCacheStore, OrganizeOptions, organize

TODAY = date(2026, 11, 14)


def make_repo(full_name: str, language: str = "Python", node_id: str | None = "ID") -> Repo:
    return Repo(
        full_name=full_name,
        node_id=None if node_id is None else f"{node_id}-{full_name}",
        url=f"https://github.com/{full_name}",
        language=language,
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class MemoryStore:
    """A CacheStore that lives in memory. The web app will use a database the same way."""

    def __init__(self, cache: StarCache | None = None):
        self.cache = cache

    def load(self) -> StarCache | None:
        return self.cache

    def save(self, cache: StarCache) -> None:
        self.cache = cache


class FakeGitHub:
    """Stands in for fetch_starred: newest first, stops at the first known repo."""

    def __init__(self, *repos: Repo):
        self.repos = list(repos)
        self.calls: list[set[str] | None] = []

    def __call__(self, username, token=None, known=None):
        self.calls.append(known)
        result = []
        for repo in self.repos:
            if known and repo.full_name in known:
                break
            result.append(repo)
        return result


class FakeAI:
    """ask_factory that answers from a dict and records which repos it was asked about."""

    def __init__(self, categories: dict[str, str]):
        self.categories = categories
        self.factory_calls = 0
        self.prompts: list[str] = []

    def __call__(self):
        self.factory_calls += 1
        return self.ask

    def ask(self, prompt: str) -> BatchCategorization:
        self.prompts.append(prompt)
        asked = [name for name in self.categories if f'"full_name": "{name}"' in prompt]
        return BatchCategorization(
            assignments=[RepoAssignment(full_name=name, category=self.categories[name]) for name in asked]
        )


@pytest.fixture
def github(monkeypatch):
    fake = FakeGitHub(make_repo("a/agent"), make_repo("b/shader", "GLSL"))
    monkeypatch.setattr("starsort.service.fetch_starred", fake)
    return fake


def test_first_run_categorizes_everything_and_saves_categories(github):
    store = MemoryStore()
    ai = FakeAI({"a/agent": "AI Agents", "b/shader": "Graphics"})

    result = organize(OrganizeOptions(username="octo"), store, ask_factory=ai, today=TODAY)

    assert result.new_repo_count == 2
    assert store.cache.categories == {"a/agent": "AI Agents", "b/shader": "Graphics"}
    assert "## AI Agents" in result.markdown and "## Graphics" in result.markdown


def test_second_run_with_nothing_new_makes_no_ai_call(github):
    store = MemoryStore()
    organize(OrganizeOptions(username="octo"), store, ask_factory=FakeAI({"a/agent": "AI", "b/shader": "Gfx"}))
    ai = FakeAI({})
    messages: list[str] = []

    result = organize(OrganizeOptions(username="octo"), store, ask_factory=ai, log=messages.append)

    assert ai.factory_calls == 0
    assert result.new_repo_count == 0
    assert any("no AI call" in message for message in messages)


def test_only_new_stars_go_to_the_ai(github):
    store = MemoryStore()
    organize(OrganizeOptions(username="octo"), store, ask_factory=FakeAI({"a/agent": "AI", "b/shader": "Gfx"}))
    github.repos.insert(0, make_repo("c/new"))
    ai = FakeAI({"c/new": "AI"})

    result = organize(OrganizeOptions(username="octo"), store, ask_factory=ai)

    assert result.new_repo_count == 1
    assert '"full_name": "c/new"' in ai.prompts[0]
    assert '"full_name": "a/agent"' not in ai.prompts[0]


def test_no_ai_groups_by_language_and_keeps_saved_categories(github):
    store = MemoryStore()
    organize(OrganizeOptions(username="octo"), store, ask_factory=FakeAI({"a/agent": "AI", "b/shader": "Gfx"}))

    result = organize(OrganizeOptions(username="octo", use_ai=False), store)

    assert set(result.categories) == {"Python", "GLSL"}
    assert store.cache.categories == {"a/agent": "AI", "b/shader": "Gfx"}


def test_recategorize_sends_every_repo_again(github):
    store = MemoryStore()
    organize(OrganizeOptions(username="octo"), store, ask_factory=FakeAI({"a/agent": "AI", "b/shader": "Gfx"}))

    result = organize(
        OrganizeOptions(username="octo", recategorize=True),
        store,
        ask_factory=FakeAI({"a/agent": "Agents", "b/shader": "Shaders"}),
    )

    assert result.new_repo_count == 2
    assert store.cache.categories == {"a/agent": "Agents", "b/shader": "Shaders"}


def test_refresh_drops_unstarred_repos_but_keeps_other_categories(github):
    store = MemoryStore()
    organize(OrganizeOptions(username="octo"), store, ask_factory=FakeAI({"a/agent": "AI", "b/shader": "Gfx"}))
    github.repos.pop()  # user unstarred b/shader

    organize(OrganizeOptions(username="octo", refresh=True), store, ask_factory=FakeAI({}))

    assert store.cache.categories == {"a/agent": "AI"}


def test_missing_node_ids_trigger_one_full_refetch_for_lists(monkeypatch):
    old = StarCache(username="octo", fetched_at=datetime.now(timezone.utc), repos=[make_repo("a/agent", node_id=None)])
    github = FakeGitHub(make_repo("a/agent"))
    monkeypatch.setattr("starsort.service.fetch_starred", github)
    store = MemoryStore(old)

    organize(OrganizeOptions(username="octo", use_ai=False, require_node_ids=True), store)

    assert github.calls == [{"a/agent"}, None]
    assert store.cache.repos[0].node_id == "ID-a/agent"


def test_file_store_round_trip(tmp_path, github):
    store = FileCacheStore(tmp_path / "octo.json")

    organize(OrganizeOptions(username="octo"), store, ask_factory=FakeAI({"a/agent": "AI", "b/shader": "Gfx"}))

    assert store.load().categories == {"a/agent": "AI", "b/shader": "Gfx"}
