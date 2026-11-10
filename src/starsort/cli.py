import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, NoReturn

import httpx
import typer
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from strands.types.exceptions import StructuredOutputException

from starsort.cache import StarCache, default_cache_path, load_cache, merge_repos, save_cache
from starsort.categorizer import group_assignments, group_by_language, uncategorized, update_assignments
from starsort.github import GitHubError, fetch_starred
from starsort.markdown import render_markdown, write_markdown
from starsort.models import Repo
from starsort.providers import DEFAULT_MODELS, Provider, ProviderError, build_model, make_asker

load_dotenv()  # at import time, so Typer's envvar= options can read values from .env

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
) -> None:
    token = os.environ.get("GITHUB_TOKEN") or None
    cache_path = cache_file or default_cache_path(user)

    try:
        cache = get_repos(user, token, cache_path, refresh)
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


if __name__ == "__main__":
    app()
