import re
from datetime import date
from pathlib import Path

from starsort.models import OTHER_CATEGORY, Repo

PROJECT_URL = "https://github.com/ZaidShabanDev/starsort"
MAX_DESCRIPTION_LENGTH = 120


def slugify(heading: str) -> str:
    """Build the same anchor GitHub generates for a heading."""
    slug = re.sub(r"[^\w\- ]", "", heading.lower())
    return slug.replace(" ", "-")


def unique_slugs(headings: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    slugs = []
    for heading in headings:
        slug = slugify(heading)
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        slugs.append(slug if count == 0 else f"{slug}-{count}")
    return slugs


def clean_description(description: str | None) -> str:
    if not description:
        return ""
    text = " ".join(description.split())
    if len(text) > MAX_DESCRIPTION_LENGTH:
        text = text[: MAX_DESCRIPTION_LENGTH - 3].rstrip() + "..."
    return text


def format_repo_line(repo: Repo) -> str:
    line = f"- [{repo.full_name}]({repo.url})"
    description = clean_description(repo.description)
    if description:
        line += f" - {description}"
    if repo.language:
        line += f" `{repo.language}`"
    return line


def order_categories(categories: dict[str, list[Repo]]) -> list[tuple[str, list[Repo]]]:
    non_empty = [(name, repos) for name, repos in categories.items() if repos]
    return sorted(non_empty, key=lambda pair: (pair[0] == OTHER_CATEGORY, pair[0].lower()))


def render_markdown(categories: dict[str, list[Repo]], username: str, generated_on: date) -> str:
    ordered = order_categories(categories)
    names = [name for name, _ in ordered]
    slugs = unique_slugs(["Awesome Stars", "Contents", *names])[2:]  # fixed headings take their anchors first
    repo_count = sum(len(repos) for _, repos in ordered)

    lines = [
        "# Awesome Stars",
        "",
        f"A curated list of the repositories [@{username}](https://github.com/{username}) has starred, "
        f"sorted into categories by [starsort]({PROJECT_URL}).",
        "",
        f"_{repo_count} repos in {len(ordered)} categories. Last updated {generated_on.isoformat()}._",
        "",
        "## Contents",
        "",
    ]
    lines += [f"- [{name}](#{slug})" for name, slug in zip(names, slugs)]

    for name, repos in ordered:
        lines += ["", f"## {name}", ""]
        for repo in sorted(repos, key=lambda r: (-r.stars, r.full_name.lower())):
            lines.append(format_repo_line(repo))

    return "\n".join(lines) + "\n"


def write_markdown(content: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")  # LF on Windows too, so git diffs stay clean
