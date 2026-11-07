from datetime import datetime, timezone

from starsort.cache import StarCache, load_cache, merge_repos, save_cache
from starsort.models import Repo


def make_repo(full_name: str, stars: int = 1, description: str | None = None) -> Repo:
    return Repo(
        full_name=full_name,
        url=f"https://github.com/{full_name}",
        description=description,
        stars=stars,
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_save_then_load_round_trip(tmp_path):
    path = tmp_path / "nested" / "octo.json"
    cache = StarCache(username="octo", fetched_at=datetime.now(timezone.utc), repos=[make_repo("a/one")])

    save_cache(cache, path)

    assert load_cache(path) == cache


def test_load_missing_file_returns_none(tmp_path):
    assert load_cache(tmp_path / "missing.json") is None


def test_unicode_descriptions_survive_round_trip(tmp_path):
    path = tmp_path / "octo.json"
    repo = make_repo("a/one", description="مكتبة رسوميات 🚀")
    save_cache(StarCache(username="octo", fetched_at=datetime.now(timezone.utc), repos=[repo]), path)

    assert load_cache(path).repos[0].description == "مكتبة رسوميات 🚀"


def test_merge_puts_new_repos_first_and_prefers_new_data():
    cached = [make_repo("a/one", stars=1), make_repo("b/two", stars=2)]
    new = [make_repo("c/three"), make_repo("a/one", stars=99)]

    merged = merge_repos(new, cached)

    assert [repo.full_name for repo in merged] == ["c/three", "a/one", "b/two"]
    assert merged[1].stars == 99
