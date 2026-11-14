"""The whole starsort flow as plain functions, shared by the CLI and the web backend.

Nothing here prints, exits or knows about Typer or HTTP. Callers pass a `log` callback and catch exceptions.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol

from starsort.cache import StarCache, load_cache, merge_repos, save_cache
from starsort.categorizer import Asker, group_assignments, group_by_language, uncategorized, update_assignments
from starsort.github import AppAuth, fetch_starred
from starsort.github_lists import GitHubGraphQL, ListsError, SyncPlan, apply_sync, plan_sync
from starsort.markdown import render_markdown
from starsort.models import Repo
from starsort.providers import DEFAULT_MODELS, Provider, build_model, make_asker

Log = Callable[[str], None]
OnBatch = Callable[[int, int], None]


def _quiet(_message: str) -> None:
    pass


class CacheStore(Protocol):
    """Where a user's stars and categories are kept: a JSON file for the CLI, a database for the web app."""

    def load(self) -> StarCache | None: ...

    def save(self, cache: StarCache) -> None: ...


class FileCacheStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> StarCache | None:
        return load_cache(self.path)

    def save(self, cache: StarCache) -> None:
        save_cache(cache, self.path)


@dataclass
class OrganizeOptions:
    username: str
    token: str | None = None
    refresh: bool = False
    recategorize: bool = False
    use_ai: bool = True
    provider: Provider = Provider.bedrock
    model_id: str | None = None
    require_node_ids: bool = False  # GitHub Lists need GraphQL IDs, which old caches don't have
    app_auth: AppAuth | None = None  # the web worker reads public stars with the OAuth App's credentials


@dataclass
class OrganizeResult:
    repos: list[Repo]
    categories: dict[str, list[Repo]]
    markdown: str
    new_repo_count: int  # repos sent to the AI in this run


def load_stars(options: OrganizeOptions, store: CacheStore, log: Log = _quiet) -> StarCache:
    cache = store.load()
    if cache is None or options.refresh:
        repos = fetch_starred(options.username, options.token, app_auth=options.app_auth)
        log(f"Fetched {len(repos)} starred repos.")
    else:
        new_repos = fetch_starred(options.username, options.token, known=cache.repo_names(), app_auth=options.app_auth)
        repos = merge_repos(new_repos, cache.repos)
        log(f"Loaded {len(cache.repos)} repos from cache, {len(new_repos)} new.")
        if options.require_node_ids and any(repo.node_id is None for repo in repos):
            log("Refetching all stars once to get the repo IDs GitHub Lists need...")
            repos = fetch_starred(options.username, options.token, app_auth=options.app_auth)

    names = {repo.full_name for repo in repos}
    saved = {name: category for name, category in (cache.categories if cache else {}).items() if name in names}
    updated = StarCache(username=options.username, fetched_at=datetime.now(timezone.utc), repos=repos, categories=saved)
    store.save(updated)
    return updated


def default_ask_factory(options: OrganizeOptions) -> Callable[[], Asker]:
    return lambda: make_asker(build_model(options.provider, options.model_id))


def organize(
    options: OrganizeOptions,
    store: CacheStore,
    *,
    ask_factory: Callable[[], Asker] | None = None,
    log: Log = _quiet,
    on_batch: OnBatch | None = None,
    today: date | None = None,
) -> OrganizeResult:
    """Fetch stars, categorize (incrementally) and render the Markdown list."""
    cache = load_stars(options, store, log)
    repos = cache.repos
    new_count = 0

    if options.use_ai and repos:
        saved = {} if options.recategorize else cache.categories
        new_count = len(uncategorized(repos, saved))
        if new_count:
            model = options.model_id or DEFAULT_MODELS[options.provider]
            log(f"Categorizing {new_count} new repos ({len(repos) - new_count} kept) with {options.provider.value} ({model})...")
        else:
            log("No new repos to categorize. Keeping saved categories (no AI call).")
        assignments = update_assignments(repos, saved, ask_factory or default_ask_factory(options), on_batch=on_batch)
        store.save(cache.model_copy(update={"categories": assignments}))
        categories = group_assignments(repos, assignments)
    else:
        categories = group_by_language(repos)

    markdown = render_markdown(categories, options.username, today or date.today())
    return OrganizeResult(repos=repos, categories=categories, markdown=markdown, new_repo_count=new_count)


def sync_github_lists(
    token: str,
    username: str,
    categories: dict[str, list[Repo]],
    dry_run: bool = False,
    log: Log = _quiet,
) -> SyncPlan:
    with GitHubGraphQL(token) as github:
        login = github.viewer_login()
        if login.lower() != username.lower():
            raise ListsError(f"Star lists can only be changed for your own account. The token belongs to '{login}', not '{username}'.")

        lists = github.fetch_lists()
        repos = [repo for group in categories.values() for repo in group]
        assignments = {repo.full_name: name for name, group in categories.items() for repo in group}
        node_ids = {repo.full_name: repo.node_id for repo in repos if repo.node_id}
        plan = plan_sync(lists, assignments, node_ids)

        if not plan.lists_to_create and not plan.changes:
            log("GitHub Lists are already up to date.")
            return plan
        log(f"GitHub Lists: {len(plan.lists_to_create)} to create, {len(plan.changes)} repos to update.")
        for name in plan.lists_to_create:
            log(f"  + list: {name}")
        for change in plan.changes:
            log(f"  {change.full_name} -> {change.target or '(removed from starsort lists)'}")
        if dry_run:
            log("Dry run: nothing was changed on GitHub.")
            return plan

        apply_sync(github, plan, lists, on_step=lambda step: log(f"  done: {step}"))
        log("GitHub Lists updated.")
        return plan
