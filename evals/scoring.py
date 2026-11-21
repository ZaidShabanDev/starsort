"""Pure scoring for the categorizer evals: no AI and no strands_evals, so pytest can check it for free."""

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from starsort.models import OTHER_CATEGORY


@dataclass(frozen=True)
class GroupingScore:
    together: float  # share of repo pairs the golden set puts together that landed in the same category
    apart: float  # share of repo pairs from different golden groups that landed in different categories
    split_groups: dict[str, list[str]] = field(default_factory=dict)  # golden group -> the categories it was spread over
    mixed_categories: dict[str, list[str]] = field(default_factory=dict)  # category -> the golden groups it mixed


def invert(categories: dict[str, list[str]]) -> dict[str, str]:
    return {repo: name for name, repos in categories.items() for repo in repos}


def missing_repos(assignments: dict[str, str], expected: list[str]) -> list[str]:
    return [name for name in expected if name not in assignments]


def other_repos(assignments: dict[str, str]) -> list[str]:
    return sorted(repo for repo, category in assignments.items() if category == OTHER_CATEGORY)


def category_count(assignments: dict[str, str]) -> int:
    return len({category for category in assignments.values() if category != OTHER_CATEGORY})


def grouping_score(assignments: dict[str, str], groups: dict[str, str | None]) -> GroupingScore:
    """Compares which repos ended up together, not the category names. A group of None means "no expectation".
    Repos in Other never count as together: Other is where the categorizer gives up."""
    labelled = sorted(repo for repo, group in groups.items() if group and repo in assignments)

    def bucket(repo: str) -> str:
        category = assignments[repo]
        return f"{OTHER_CATEGORY}:{repo}" if category == OTHER_CATEGORY else category

    together = together_hits = apart = apart_hits = 0
    for first, second in combinations(labelled, 2):
        same_bucket = bucket(first) == bucket(second)
        if groups[first] == groups[second]:
            together += 1
            together_hits += same_bucket
        else:
            apart += 1
            apart_hits += not same_bucket

    spread: dict[str, set[str]] = defaultdict(set)
    mixed: dict[str, set[str]] = defaultdict(set)
    for repo in labelled:
        spread[groups[repo]].add(assignments[repo])
        if assignments[repo] != OTHER_CATEGORY:
            mixed[assignments[repo]].add(groups[repo])

    return GroupingScore(
        together=together_hits / together if together else 1.0,
        apart=apart_hits / apart if apart else 1.0,
        split_groups={group: sorted(names) for group, names in spread.items() if len(names) > 1},
        mixed_categories={name: sorted(found) for name, found in mixed.items() if len(found) > 1},
    )
