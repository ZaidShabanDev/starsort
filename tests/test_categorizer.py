from datetime import datetime, timezone

from starsort.categorizer import group_by_language
from starsort.models import Repo


def make_repo(full_name: str, language: str | None) -> Repo:
    return Repo(
        full_name=full_name,
        url=f"https://github.com/{full_name}",
        language=language,
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_group_by_language_puts_missing_language_in_other():
    repos = [make_repo("a/one", "Python"), make_repo("b/two", None), make_repo("c/three", "Python")]

    categories = group_by_language(repos)

    assert {name: [r.full_name for r in group] for name, group in categories.items()} == {
        "Python": ["a/one", "c/three"],
        "Other": ["b/two"],
    }
