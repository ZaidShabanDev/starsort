from datetime import datetime, timezone

from starsort.categorizer import (
    build_batch_prompt,
    categorize_repos,
    group_assignments,
    group_by_language,
    limit_categories,
    normalize_category,
)
from starsort.models import BatchCategorization, Repo, RepoAssignment


def make_repo(full_name: str, language: str | None = "Python", description: str | None = None) -> Repo:
    return Repo(
        full_name=full_name,
        url=f"https://github.com/{full_name}",
        description=description,
        language=language,
        topics=["demo"],
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def answer(pairs: dict[str, str]) -> BatchCategorization:
    return BatchCategorization(assignments=[RepoAssignment(full_name=k, category=v) for k, v in pairs.items()])


class FakeAsker:
    """Stands in for the Strands agent: returns canned answers and records each prompt."""

    def __init__(self, *responses: BatchCategorization):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> BatchCategorization:
        self.prompts.append(prompt)
        return self.responses.pop(0)


def test_group_by_language_puts_missing_language_in_other():
    repos = [make_repo("a/one", "Python"), make_repo("b/two", None), make_repo("c/three", "Python")]

    categories = group_by_language(repos)

    assert {name: [r.full_name for r in group] for name, group in categories.items()} == {
        "Python": ["a/one", "c/three"],
        "Other": ["b/two"],
    }


def test_repos_are_sent_in_batches_and_later_batches_see_earlier_categories():
    repos = [make_repo("a/one"), make_repo("b/two"), make_repo("c/three")]
    ask = FakeAsker(
        answer({"a/one": "AI Agents", "b/two": "Graphics"}),
        answer({"c/three": "AI Agents"}),
    )

    assignments = categorize_repos(repos, ask, batch_size=2)

    assert assignments == {"a/one": "AI Agents", "b/two": "Graphics", "c/three": "AI Agents"}
    assert len(ask.prompts) == 2
    assert "(none yet)" in ask.prompts[0]
    assert "- AI Agents\n- Graphics" in ask.prompts[1]


def test_existing_categories_are_offered_in_the_first_prompt():
    ask = FakeAsker(answer({"a/one": "AWS"}))

    categorize_repos([make_repo("a/one")], ask, existing_categories=["AWS", "Other"])

    assert "- AWS" in ask.prompts[0]
    assert "- Other" not in ask.prompts[0]


def test_skipped_repo_goes_to_other_and_unknown_repos_are_ignored():
    ask = FakeAsker(answer({"a/one": "AWS", "made/up": "Hallucinated"}))

    assignments = categorize_repos([make_repo("a/one"), make_repo("b/two")], ask)

    assert assignments == {"a/one": "AWS", "b/two": "Other"}


def test_names_match_case_insensitively():
    ask = FakeAsker(answer({"a/one": "AI Agents", "B/Two": "  ai   agents ", "c/three": "other"}))

    assignments = categorize_repos([make_repo("a/one"), make_repo("b/two"), make_repo("c/three")], ask)

    assert assignments == {"a/one": "AI Agents", "b/two": "AI Agents", "c/three": "Other"}


def test_normalize_blank_name_becomes_other():
    assert normalize_category("   ", []) == "Other"


def test_prompt_contains_repo_data_and_truncates_long_descriptions():
    prompt = build_batch_prompt([make_repo("a/one", description="x" * 1000)], ["AWS"])

    assert '"full_name": "a/one"' in prompt
    assert '"topics": [' in prompt
    assert "x" * 300 in prompt
    assert "x" * 301 not in prompt


def test_limit_categories_folds_smallest_into_other():
    assignments = {
        "r/1": "Big", "r/2": "Big", "r/3": "Big",
        "r/4": "Medium", "r/5": "Medium",
        "r/6": "Small A",
        "r/7": "Small B",
    }

    limited = limit_categories(assignments, max_categories=3)

    assert set(limited.values()) == {"Big", "Medium", "Other"}
    assert limited["r/6"] == "Other" and limited["r/7"] == "Other"


def test_limit_categories_leaves_small_lists_alone():
    assignments = {"r/1": "AWS", "r/2": "Other"}

    assert limit_categories(assignments, max_categories=3) == assignments


def test_group_assignments_keeps_repo_order():
    repos = [make_repo("a/one"), make_repo("b/two"), make_repo("c/three")]

    groups = group_assignments(repos, {"a/one": "AWS", "b/two": "Graphics", "c/three": "AWS"})

    assert {name: [r.full_name for r in group] for name, group in groups.items()} == {
        "AWS": ["a/one", "c/three"],
        "Graphics": ["b/two"],
    }
