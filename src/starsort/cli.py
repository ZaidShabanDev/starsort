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
from starsort.categorizer import categorize_repos, group_assignments, group_by_language, limit_categories
from starsort.github import GitHubError, fetch_starred
from starsort.markdown import render_markdown, write_markdown
from starsort.models import Repo
from starsort.providers import DEFAULT_MODELS, Provider, ProviderError, build_model, make_asker

load_dotenv()  # at import time, so Typer's envvar= options can read values from .env

app = typer.Typer(add_completion=False, help="Organize your GitHub stars into an awesome-list README.")


def get_repos(username: str, token: str | None, cache_path: Path, refresh: bool) -> list[Repo]:
    cache = None if refresh else load_cache(cache_path)
    if cache is None:
        repos = fetch_starred(username, token)
        typer.echo(f"Fetched {len(repos)} starred repos.")
    else:
        new_repos = fetch_starred(username, token, known=cache.repo_names())
        repos = merge_repos(new_repos, cache.repos)
        typer.echo(f"Loaded {len(cache.repos)} repos from cache, {len(new_repos)} new.")
    save_cache(StarCache(username=username, fetched_at=datetime.now(timezone.utc), repos=repos), cache_path)
    return repos


def categorize(repos: list[Repo], provider: Provider, model_id: str | None) -> dict[str, list[Repo]]:
    ask = make_asker(build_model(provider, model_id))
    typer.echo(f"Categorizing {len(repos)} repos with {provider.value} ({model_id or DEFAULT_MODELS[provider]})...")
    assignments = categorize_repos(repos, ask, on_batch=lambda done, total: typer.echo(f"  batch {done}/{total} done"))
    return group_assignments(repos, limit_categories(assignments))


def fail(message: str) -> NoReturn:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


@app.command()
def main(
    user: Annotated[str, typer.Option("--user", "-u", help="GitHub username whose stars to organize.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Markdown file to write.")] = Path("awesome-stars.md"),
    cache_file: Annotated[Path | None, typer.Option("--cache", help="Cache file path. Default: .starsort/<user>.json")] = None,
    refresh: Annotated[bool, typer.Option("--refresh", help="Ignore the cache and refetch all stars.")] = False,
    provider: Annotated[Provider, typer.Option("--provider", envvar="STARSORT_PROVIDER", help="Where the AI model runs.")] = Provider.bedrock,
    model: Annotated[str | None, typer.Option("--model", envvar="STARSORT_MODEL", help="Model ID. Default depends on --provider.")] = None,
    no_ai: Annotated[bool, typer.Option("--no-ai", help="Group by language instead of calling the AI (free, offline).")] = False,
) -> None:
    token = os.environ.get("GITHUB_TOKEN") or None
    cache_path = cache_file or default_cache_path(user)

    try:
        repos = get_repos(user, token, cache_path, refresh)
    except (GitHubError, httpx.HTTPError) as error:
        fail(str(error))

    if no_ai or not repos:
        categories = group_by_language(repos)
    else:
        try:
            categories = categorize(repos, provider, model)
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
