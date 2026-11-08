from starsort.models import OTHER_CATEGORY, Repo


def group_by_language(repos: list[Repo]) -> dict[str, list[Repo]]:
    """Placeholder rule until the Strands agent lands: one category per language."""
    categories: dict[str, list[Repo]] = {}
    for repo in repos:
        categories.setdefault(repo.language or OTHER_CATEGORY, []).append(repo)
    return categories
