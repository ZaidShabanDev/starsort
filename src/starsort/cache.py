from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from starsort.models import Repo

DEFAULT_CACHE_DIR = Path(".starsort")


class StarCache(BaseModel):
    username: str
    fetched_at: datetime
    repos: list[Repo] = Field(default_factory=list)
    categories: dict[str, str] = Field(default_factory=dict, description="full_name -> category from earlier AI runs")

    def repo_names(self) -> set[str]:
        return {repo.full_name for repo in self.repos}


def default_cache_path(username: str) -> Path:
    return DEFAULT_CACHE_DIR / f"{username.lower()}.json"


def load_cache(path: Path) -> StarCache | None:
    if not path.exists():
        return None
    return StarCache.model_validate_json(path.read_text(encoding="utf-8"))


def save_cache(cache: StarCache, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(cache.model_dump_json(indent=2), encoding="utf-8")
    temp_path.replace(path)  # write-then-rename so a crash never leaves a half-written cache


def merge_repos(new_repos: list[Repo], cached_repos: list[Repo]) -> list[Repo]:
    new_names = {repo.full_name for repo in new_repos}
    return new_repos + [repo for repo in cached_repos if repo.full_name not in new_names]
