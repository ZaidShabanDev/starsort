import os
from pathlib import Path
from typing import Annotated, NoReturn

import httpx
import typer
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import find_dotenv, load_dotenv
from strands.types.exceptions import StructuredOutputException

from starsort.cache import default_cache_path
from starsort.github import GitHubError
from starsort.github_lists import ListsError
from starsort.markdown import write_markdown
from starsort.providers import Provider, ProviderError
from starsort.service import FileCacheStore, OrganizeOptions, organize, sync_github_lists

# Import time, so Typer's envvar= options see .env. usecwd: as an installed tool, look where the user runs it.
load_dotenv(find_dotenv(usecwd=True))

app = typer.Typer(add_completion=False, help="Organize your GitHub stars into an awesome-list README.")


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
    if sync_lists and not token:
        fail("--sync-lists needs GITHUB_TOKEN in .env (a token that can manage your star lists).")

    options = OrganizeOptions(
        username=user,
        token=token,
        refresh=refresh,
        recategorize=recategorize,
        use_ai=not no_ai,
        provider=provider,
        model_id=model,
        require_node_ids=sync_lists,
    )
    store = FileCacheStore(cache_file or default_cache_path(user))

    try:
        result = organize(options, store, log=typer.echo, on_batch=lambda done, total: typer.echo(f"  batch {done}/{total} done"))
    except (GitHubError, httpx.HTTPError, ProviderError) as error:
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

    write_markdown(result.markdown, output)
    typer.echo(f"Wrote {output} with {len(result.repos)} repos in {len(result.categories)} categories.")

    if sync_lists:
        try:
            sync_github_lists(token, user, result.categories, dry_run, log=typer.echo)
        except (ListsError, httpx.HTTPError) as error:
            fail(str(error))


if __name__ == "__main__":
    app()
