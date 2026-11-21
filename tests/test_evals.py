import json
from pathlib import Path

from evals.scoring import category_count, grouping_score, invert, missing_repos, other_repos
from starsort.models import OTHER_CATEGORY

GROUPS = {"a/1": "AI", "a/2": "AI", "w/1": "AWS", "w/2": "AWS", "x/1": None}


def test_perfect_grouping_scores_full_marks_whatever_the_names():
    assignments = {"a/1": "Agents", "a/2": "Agents", "w/1": "Cloud", "w/2": "Cloud", "x/1": "Agents"}

    score = grouping_score(assignments, GROUPS)

    assert (score.together, score.apart) == (1.0, 1.0)
    assert (score.split_groups, score.mixed_categories) == ({}, {})


def test_a_group_split_over_two_categories_loses_together_pairs():
    assignments = {"a/1": "AI Agents", "a/2": "AI Coding Assistants", "w/1": "AWS", "w/2": "AWS"}

    score = grouping_score(assignments, GROUPS)

    assert score.together == 0.5
    assert score.apart == 1.0
    assert score.split_groups == {"AI": ["AI Agents", "AI Coding Assistants"]}


def test_two_groups_in_one_category_lose_apart_pairs():
    assignments = {"a/1": "Stuff", "a/2": "Stuff", "w/1": "Stuff", "w/2": "Stuff"}

    score = grouping_score(assignments, GROUPS)

    assert score.together == 1.0
    assert score.apart == 0.0
    assert score.mixed_categories == {"Stuff": ["AI", "AWS"]}


def test_repos_in_other_never_count_as_together():
    assignments = {"a/1": OTHER_CATEGORY, "a/2": OTHER_CATEGORY, "w/1": "AWS", "w/2": "AWS"}

    assert grouping_score(assignments, GROUPS).together == 0.5


def test_coverage_helpers():
    assignments = invert({"AI": ["a/1"], OTHER_CATEGORY: ["w/1"]})

    assert missing_repos(assignments, ["a/1", "w/1", "w/2"]) == ["w/2"]
    assert other_repos(assignments) == ["w/1"]
    assert category_count(assignments) == 1


def test_golden_set_is_well_formed():
    golden = json.loads((Path(__file__).parents[1] / "evals" / "golden_repos.json").read_text(encoding="utf-8"))
    names = [item["full_name"] for item in golden]

    assert len(names) == len(set(names))
    groups = [item["group"] for item in golden if item["group"]]
    assert 5 <= len(set(groups)) <= 15
    assert all(groups.count(group) >= 2 for group in set(groups)), "every group needs a pair to compare"
