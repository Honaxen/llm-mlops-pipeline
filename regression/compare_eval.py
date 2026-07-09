"""
Compares a new evaluation run against a baseline (the previous version's
eval results) and flags any metric that regressed beyond a threshold.

Designed to work with the JSON output shape produced by
rag-evaluation-framework: a flat dict of metric name -> score (0-1),
e.g. {"faithfulness": 0.91, "relevance": 0.87, "completeness": 0.84, "precision": 0.89}

Exits with a non-zero status code when a regression is detected, so this
script can be used directly as a CI gate -- a failing exit code blocks
the GitHub Actions workflow from proceeding to deployment.

Usage:
    python compare_eval.py \
        --baseline registry/versions/v1.2.0/eval_results.json \
        --current  registry/versions/v1.3.0-candidate/eval_results.json \
        --threshold 0.03 \
        --output regression/regression_report.json
"""

import argparse
import json
import sys
from pathlib import Path


def load_scores(path: str) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def compare(baseline: dict, current: dict, threshold: float) -> dict:
    """
    A metric "regresses" when it drops by more than `threshold` (absolute)
    compared to the baseline. Metrics only in one file are reported but
    don't count as regressions -- that usually means the eval suite itself
    changed, which is a different problem than a quality drop.
    """
    all_metrics = sorted(set(baseline.keys()) | set(current.keys()))
    comparisons = []
    regressions = []

    for metric in all_metrics:
        baseline_score = baseline.get(metric)
        current_score = current.get(metric)

        if baseline_score is None or current_score is None:
            comparisons.append({
                "metric": metric,
                "baseline": baseline_score,
                "current": current_score,
                "delta": None,
                "regressed": False,
                "note": "metric missing from one side, skipped",
            })
            continue

        delta = round(current_score - baseline_score, 4)
        regressed = delta < -abs(threshold)

        comparisons.append({
            "metric": metric,
            "baseline": baseline_score,
            "current": current_score,
            "delta": delta,
            "regressed": regressed,
        })

        if regressed:
            regressions.append(metric)

    return {
        "threshold": threshold,
        "comparisons": comparisons,
        "regressions": regressions,
        "passed": len(regressions) == 0,
    }


def print_summary(result: dict):
    print("\n=== Evaluation Regression Check ===")
    header = f"{'Metric':<20} {'Baseline':<10} {'Current':<10} {'Delta':<10} {'Status':<10}"
    print(header)
    print("-" * len(header))

    for c in result["comparisons"]:
        if c["delta"] is None:
            print(f"{c['metric']:<20} {'-':<10} {'-':<10} {'-':<10} {'skipped':<10}")
            continue
        status = "REGRESSED" if c["regressed"] else "ok"
        print(f"{c['metric']:<20} {c['baseline']:<10} {c['current']:<10} {c['delta']:<10} {status:<10}")

    print()
    if result["passed"]:
        print(f"PASSED -- no metric dropped more than {result['threshold']}")
    else:
        print(f"FAILED -- regression in: {', '.join(result['regressions'])}")


def main(args):
    baseline = load_scores(args.baseline)
    current = load_scores(args.current)

    result = compare(baseline, current, args.threshold)
    print_summary(result)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nSaved regression report to {output_path}")

    # Non-zero exit on failure is what makes this usable as a CI gate --
    # GitHub Actions treats any non-zero exit as a failed step.
    if not result["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare eval results against a baseline")
    parser.add_argument("--baseline", required=True, help="Path to baseline eval_results.json")
    parser.add_argument("--current", required=True, help="Path to current eval_results.json")
    parser.add_argument("--threshold", type=float, default=0.03,
                         help="Max allowed absolute drop before flagging a regression")
    parser.add_argument("--output", default="regression_report.json")
    args = parser.parse_args()

    main(args)
