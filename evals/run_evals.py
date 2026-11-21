"""Scores the categorizer on a fixed golden set of repos with Strands Evals. Calls Bedrock, so it costs a few cents.

    uv run python -m evals.run_evals                                     # prints the scores
    uv run python -m evals.run_evals --save evals/results/baseline.json  # keeps them to compare later runs
"""

import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

from strands_evals import Case, Experiment, eval_task
from strands_evals.evaluators import Evaluator, OutputEvaluator
from strands_evals.types.evaluation import EvaluationData, EvaluationOutput

from evals.scoring import category_count, grouping_score, invert, missing_repos, other_repos
from starsort.categorizer import MAX_CATEGORIES, categorize_repos, limit_categories
from starsort.models import Repo
from starsort.providers import Provider, build_model, make_asker

GOLDEN_FILE = Path(__file__).with_name("golden_repos.json")
WEB_MODEL = "us.anthropic.claude-sonnet-4-6"  # bedrock_model_id in infra/web/variables.tf
MIN_CATEGORIES = 5
MAX_OTHER_SHARE = 0.1
MIN_TOGETHER = 0.8
MIN_APART = 0.9
SHUFFLE_SEED = 7  # fixed, so every run sends the repos in the same order

# Production sends 50 repos per AI call; small batches show whether names drift between calls (the duplicate-list bug).
CASES = [
    Case(name="one batch", input={"label": "one batch", "batch_size": 50}),
    Case(name="small batches", input={"label": "small batches", "batch_size": 8}),
]

MERGE_RUBRIC = """
The output maps category names to the GitHub repositories in each category.
Decide whether any two categories overlap so much that a person organizing their stars would merge them
(for example "AI Agents" and "AI Coding Assistants" when both hold AI coding tools).
Score 1.0 if no two categories should be merged.
Score 0.5 if exactly one pair should be merged.
Score 0.0 if two or more pairs should be merged.
Name every pair you would merge in your reasoning."""

results: list[dict] = []


def load_golden() -> list[dict]:
    return json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))


def to_repo(item: dict) -> Repo:
    return Repo(
        full_name=item["full_name"],
        url=f"https://github.com/{item['full_name']}",
        description=item.get("description"),
        topics=item.get("topics") or [],
        language=item.get("language"),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def record(case: EvaluationData, check: str, output: EvaluationOutput) -> list[EvaluationOutput]:
    results.append({"case": case.input["label"], "check": check, "score": output.score, "passed": output.test_pass, "reason": output.reason})
    return [output]


class CoverageCheck(Evaluator):
    """Every repo gets a category, and few end up in Other."""

    def __init__(self, expected: list[str]):
        super().__init__()
        self.expected = expected

    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        assignments = invert(evaluation_case.actual_output)
        missing = missing_repos(assignments, self.expected)
        other = other_repos(assignments)
        share = (len(missing) + len(other)) / len(self.expected)
        passed = not missing and share <= MAX_OTHER_SHARE
        reason = f"missing: {missing or 'none'}; in Other: {other or 'none'}"
        return record(evaluation_case, "coverage", EvaluationOutput(score=1 - share, test_pass=passed, reason=reason))


class CategoryCountCheck(Evaluator):
    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        count = category_count(invert(evaluation_case.actual_output))
        passed = MIN_CATEGORIES <= count <= MAX_CATEGORIES
        reason = f"{count} categories (want {MIN_CATEGORIES}-{MAX_CATEGORIES})"
        return record(evaluation_case, "category count", EvaluationOutput(score=float(passed), test_pass=passed, reason=reason))


class GroupingCheck(Evaluator):
    """Repos the golden set puts together land in one category, and different groups stay apart."""

    def __init__(self, groups: dict[str, str | None]):
        super().__init__()
        self.groups = groups

    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        score = grouping_score(invert(evaluation_case.actual_output), self.groups)
        passed = score.together >= MIN_TOGETHER and score.apart >= MIN_APART
        reason = (
            f"together {score.together:.0%}, apart {score.apart:.0%}; "
            f"split: {score.split_groups or 'none'}; mixed: {score.mixed_categories or 'none'}"
        )
        output = EvaluationOutput(score=(score.together + score.apart) / 2, test_pass=passed, reason=reason)
        return record(evaluation_case, "grouping", output)


class MergeJudge(OutputEvaluator):
    """LLM judge: are any two categories near-duplicates?"""

    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        outputs = super().evaluate(evaluation_case)
        for output in outputs:
            record(evaluation_case, "no near-duplicates (AI judge)", output)
        return outputs

    async def evaluate_async(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        outputs = await super().evaluate_async(evaluation_case)
        for output in outputs:
            record(evaluation_case, "no near-duplicates (AI judge)", output)
        return outputs


def build_task(golden: list[dict], model_id: str):
    repos = [to_repo(item) for item in golden]
    random.Random(SHUFFLE_SEED).shuffle(repos)

    @eval_task()
    def categorize(case: Case) -> dict:
        ask = make_asker(build_model(Provider.bedrock, model_id))
        try:
            assignments = limit_categories(categorize_repos(repos, ask, batch_size=case.input["batch_size"]))
        except Exception as error:  # Strands Evals swallows task errors, so show them before it does
            print(f"[ERROR] {case.name}: {error!r}", file=sys.stderr)
            raise
        categories: dict[str, list[str]] = {}
        for repo, category in sorted(assignments.items()):
            categories.setdefault(category, []).append(repo)
        return {"output": categories}

    return categorize


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=os.environ.get("STARSORT_MODEL", WEB_MODEL), help="Bedrock model to categorize with")
    parser.add_argument("--judge-model", default=WEB_MODEL, help="Bedrock model for the AI judge; keep it fixed when comparing models")
    parser.add_argument("--save", type=Path, help="write the scores to this JSON file")
    args = parser.parse_args()

    golden = load_golden()
    groups = {item["full_name"]: item.get("group") for item in golden}
    evaluators = [
        CoverageCheck([item["full_name"] for item in golden]),
        CategoryCountCheck(),
        GroupingCheck(groups),
        MergeJudge(rubric=MERGE_RUBRIC, model=build_model(Provider.bedrock, args.judge_model), include_inputs=False),
    ]
    print(f"Categorizing {len(golden)} golden repos with {args.model}, judged by {args.judge_model} ({len(CASES)} cases)...")
    Experiment(cases=CASES, evaluators=evaluators).run_evaluations(build_task(golden, args.model))

    for result in results:
        mark = "PASS" if result["passed"] else "FAIL"
        print(f"[{mark}] {result['case']:<14} {result['check']:<30} {result['score']:.2f}  {result['reason']}")
    expected = len(CASES) * len(evaluators)
    passed = len(results) == expected and all(result["passed"] for result in results)
    if len(results) < expected:
        print(f"Only {len(results)} of {expected} checks ran: a case failed before it could be scored (see [ERROR] above).")
    print("All checks passed." if passed else "Some checks failed.")

    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        summary = {"model": args.model, "judge_model": args.judge_model, "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "results": results}
        args.save.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Saved to {args.save}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
