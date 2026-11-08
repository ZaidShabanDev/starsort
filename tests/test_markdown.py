from datetime import date, datetime, timezone

from starsort.markdown import clean_description, format_repo_line, render_markdown, slugify, write_markdown
from starsort.models import Repo


def make_repo(full_name: str, stars: int = 1, description: str | None = None, language: str | None = None) -> Repo:
    return Repo(
        full_name=full_name,
        url=f"https://github.com/{full_name}",
        description=description,
        language=language,
        stars=stars,
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_slugify_matches_github_anchors():
    assert slugify("AI Agents") == "ai-agents"
    assert slugify("Shaders & Graphics") == "shaders--graphics"
    assert slugify("C++") == "c"
    assert slugify("Dev_Tools") == "dev_tools"


def test_render_full_document():
    categories = {
        "Python": [
            make_repo("a/alpha", stars=10, description="Tool A", language="Python"),
            make_repo("b/beta", stars=50, language="Python"),
        ],
        "Other": [make_repo("c/gamma", description="Misc")],
        "Empty": [],
    }

    result = render_markdown(categories, "octo", date(2026, 11, 8))

    assert result == (
        "# Awesome Stars\n"
        "\n"
        "A curated list of the repositories [@octo](https://github.com/octo) has starred, "
        "sorted into categories by [starsort](https://github.com/ZaidShabanDev/starsort).\n"
        "\n"
        "_3 repos in 2 categories. Last updated 2026-11-08._\n"
        "\n"
        "## Contents\n"
        "\n"
        "- [Python](#python)\n"
        "- [Other](#other)\n"
        "\n"
        "## Python\n"
        "\n"
        "- [b/beta](https://github.com/b/beta) `Python`\n"
        "- [a/alpha](https://github.com/a/alpha) - Tool A `Python`\n"
        "\n"
        "## Other\n"
        "\n"
        "- [c/gamma](https://github.com/c/gamma) - Misc\n"
    )


def test_categories_sorted_alphabetically_with_other_last():
    categories = {name: [make_repo(f"x/{name}")] for name in ["Other", "Zig", "AI Agents"]}

    result = render_markdown(categories, "octo", date(2026, 11, 8))

    assert result.index("## AI Agents") < result.index("## Zig") < result.index("## Other")


def test_anchor_avoids_clash_with_contents_heading():
    result = render_markdown({"Contents": [make_repo("a/one")]}, "octo", date(2026, 11, 8))

    assert "- [Contents](#contents-1)" in result


def test_description_whitespace_is_collapsed():
    assert clean_description("line one\n\n   line two") == "line one line two"


def test_long_description_is_truncated():
    result = clean_description("x" * 200)

    assert len(result) == 120
    assert result.endswith("...")


def test_repo_line_without_description_or_language():
    assert format_repo_line(make_repo("a/one")) == "- [a/one](https://github.com/a/one)"


def test_write_uses_utf8_and_lf_line_endings(tmp_path):
    path = tmp_path / "out" / "stars.md"
    content = "# عنوان 🚀\n\n- item\n"

    write_markdown(content, path)

    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.decode("utf-8") == content
