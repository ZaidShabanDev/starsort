import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import httpx
import typer
from dotenv import load_dotenv

from starsort.cache import StarCache, default_cache_path, load_cache, merge_repos, save_cache
from starsort.github import GitHubError, fetch_starred
from starsort.models import Repo

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


@app.command()
def main(
    user: Annotated[str, typer.Option("--user", "-u", help="GitHub username whose stars to organize.")],
    cache_file: Annotated[Path | None, typer.Option("--cache", help="Cache file path. Default: .starsort/<user>.json")] = None,
    refresh: Annotated[bool, typer.Option("--refresh", help="Ignore the cache and refetch all stars.")] = False,
) -> None:
    load_dotenv()
    token = os.environ.get("GITHUB_TOKEN") or None
    cache_path = cache_file or default_cache_path(user)

    try:
        repos = get_repos(user, token, cache_path, refresh)
    except (GitHubError, httpx.HTTPError) as error:
        typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    for repo in repos:
        language = repo.language or "-"
        description = repo.description or ""
        typer.echo(f"{repo.full_name}  [{language}]  {repo.stars} stars  {description}")


if __name__ == "__main__":
    app()
