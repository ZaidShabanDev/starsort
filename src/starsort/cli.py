import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, NoReturn

import httpx
import typer
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import find_dotenv, load_dotenv
from strands.types.exceptions import StructuredOutputException

from starsort.cache import StarCache, default_cache_path, load_cache, merge_repos, save_cache
from starsort.categorizer import group_assignments, group_by_language, uncategorized, update_assignments
from starsort.github import GitHubError, fetch_starred
from starsort.github_lists import GitHubGraphQL, ListsError, apply_sync, plan_sync
from starsort.markdown import render_markdown, write_markdown
from starsort.models import Repo
from starsort.providers import DEFAULT_MODELS, Provider, ProviderError, build_model, make_asker

# Import time, so Typer's envvar= options see .env. usecwd: as an installed tool, look where the user runs it.
load_dotenv(find_dotenv(usecwd=True))

app = typer.Typer(add_completion=False, help="Organize your GitHub stars into an awesome-list README.")


def get_repos(username: str, token: str | None, cache_path: Path, refresh: bool) -> StarCache:
    cache = load_cache(cache_path)
    if cache is None or refresh:
        repos = fetch_starred(username, token)
        typer.echo(f"Fetched {len(repos)} starred repos.")
    else:
        new_repos = fetch_starred(username, token, known=cache.repo_names())
        repos = merge_repos(new_repos, cache.repos)
        typer.echo(f"Loaded {len(cache.repos)} repos from cache, {len(new_repos)} new.")

    names = {repo.full_name for repo in repos}
    saved = {name: category for name, category in (cache.categories if cache else {}).items() if name in names}
    updated = StarCache(username=username, fetched_at=datetime.now(timezone.utc), repos=repos, categories=saved)
    save_cache(updated, cache_path)
    return updated


def categorize(repos: list[Repo], saved: dict[str, str], provider: Provider, model_id: str | None) -> dict[str, str]:
    pending = len(uncategorized(repos, saved))
    if pending:
        typer.echo(
            f"Categorizing {pending} new repos ({len(repos) - pending} kept) "
            f"with {provider.value} ({model_id or DEFAULT_MODELS[provider]})..."
        )
    else:
        typer.echo("No new repos to categorize. Keeping saved categories (no AI call).")
    return update_assignments(
        repos,
        saved,
        ask_factory=lambda: make_asker(build_model(provider, model_id)),
        on_batch=lambda done, total: typer.echo(f"  batch {done}/{total} done"),
    )


def sync_github_lists(token: str, user: str, categories: dict[str, list[Repo]], dry_run: bool) -> None:
    with GitHubGraphQL(token) as github:
        login = github.viewer_login()
        if login.lower() != user.lower():
            fail(f"--sync-lists can only change your own lists. GITHUB_TOKEN belongs to '{login}', not '{user}'.")

        lists = github.fetch_lists()
        repos = [repo for group in categories.values() for repo in group]
        assignments = {repo.full_name: name for name, group in categories.items() for repo in group}
        node_ids = {repo.full_name: repo.node_id for repo in repos if repo.node_id}
        plan = plan_sync(lists, assignments, node_ids)

        if not plan.lists_to_create and not plan.changes:
            typer.echo("GitHub Lists are already up to date.")
            return
        typer.echo(f"GitHub Lists: {len(plan.lists_to_create)} to create, {len(plan.changes)} repos to update.")
        for name in plan.lists_to_create:
            typer.echo(f"  + list: {name}")
        for change in plan.changes:
            typer.echo(f"  {change.full_name} -> {change.target or '(removed from starsort lists)'}")
        if dry_run:
            typer.echo("Dry run: nothing was changed on GitHub.")
            return

        apply_sync(github, plan, lists, on_step=lambda step: typer.echo(f"  done: {step}"))
        typer.echo("GitHub Lists updated.")


def fail(message: str) -> NoReturn:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


@app.command()
def main(
    user: Annotated[str, typer.Option("--user", "-u", help="GitHub username whose stars to organize.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Markdown file to write.")] = Path("awesome-stars.md"),
    cache_file: Annotated[Path | None, typer.Option("--cache", help="Cache file path. Default: .starsort/<user>.json")] = None,
    refresh: Annotated[bool, typer.Option("--refresh", help="Refetch all stars from GitHub (saved categories are kept).")] = False,
    provider: Annotated[Provider, typer.Option("--provider", envvar="STARSORT_PROVIDER", help="Where the AI model runs.")] = Provider.bedrock,
    model: Annotated[str | None, typer.Option("--model", envvar="STARSORT_MODEL", help="Model ID. Default depends on --provider.")] = None,
    no_ai: Annotated[bool, typer.Option("--no-ai", help="Group by language instead of calling the AI (free, offline).")] = False,
    recategorize: Annotated[bool, typer.Option("--recategorize", help="Forget saved categories and let the AI sort every repo again.")] = False,
    sync_lists: Annotated[bool, typer.Option("--sync-lists", help="Also put each repo in a GitHub star List named after its category. Needs GITHUB_TOKEN with write access.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="With --sync-lists: show what would change on GitHub, change nothing.")] = False,
) -> None:
    token = os.environ.get("GITHUB_TOKEN") or None
    cache_path = cache_file or default_cache_path(user)
    if sync_lists and not token:
        fail("--sync-lists needs GITHUB_TOKEN in .env (a token that can manage your star lists).")

    try:
        cache = get_repos(user, token, cache_path, refresh)
        if sync_lists and any(repo.node_id is None for repo in cache.repos):
            typer.echo("Refetching all stars once to get the repo IDs GitHub Lists need...")
            cache = get_repos(user, token, cache_path, refresh=True)
    except (GitHubError, httpx.HTTPError) as error:
        fail(str(error))
    repos = cache.repos

    if no_ai or not repos:
        categories = group_by_language(repos)
    else:
        try:
            saved = {} if recategorize else cache.categories
            assignments = categorize(repos, saved, provider, model)
            save_cache(cache.model_copy(update={"categories": assignments}), cache_path)
            categories = group_assignments(repos, assignments)
        except ProviderError as error:
            fail(str(error))
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "AccessDeniedException":
                fail(f"AWS: {error}\nYour account may not have access to this model. Try another one with --model "
                     "(list them: aws bedrock list-inference-profiles) or enable it in the Bedrock playground.")
            fail(f"AWS: {error}. Check AWS_PROFILE / AWS_REGION and Bedrock model access.")
        except BotoCoreError as error:
            fail(f"AWS: {error}. Check AWS_PROFILE / AWS_REGION and Bedrock model access.")
        except StructuredOutputException as error:
            fail(f"The model did not return valid categories: {error}")

    write_markdown(render_markdown(categories, user, date.today()), output)
    typer.echo(f"Wrote {output} with {len(repos)} repos in {len(categories)} categories.")

    if sync_lists:
        try:
            sync_github_lists(token, user, categories, dry_run)
        except (ListsError, httpx.HTTPError) as error:
            fail(str(error))


if __name__ == "__main__":
    app()
