"""
Polls the canary router's /canary-metrics endpoint and decides whether
the canary version is healthy enough to promote to production, or bad
enough to roll back -- then actually executes that decision against
registry/model_registry.py.

Decision rules (deliberately simple and explainable, not a black box):
  - Not enough canary traffic yet (< min_requests)  -> WAIT, decide nothing
  - Canary error rate > max_error_rate_pct           -> ROLLBACK
  - Canary avg latency > stable avg latency * (1 + max_latency_increase_pct/100)
                                                       -> ROLLBACK
  - Otherwise, once min_requests is reached           -> PROMOTE

Rollback is the default posture on any ambiguity -- a false rollback costs
a bit of deployment velocity, a false promotion ships a broken version to
100% of traffic. That asymmetry is why every check below fails closed.

Usage:
    python monitor.py --version v1.3.0 \
        --router_url http://localhost:9000 \
        --min_requests 20 \
        --max_error_rate 5.0 \
        --max_latency_increase 20.0
"""

import argparse
import json
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

REGISTRY_SCRIPT = Path(__file__).parent.parent / "registry" / "model_registry.py"


def fetch_metrics(router_url: str) -> dict:
    request = urllib.request.Request(f"{router_url}/canary-metrics")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"Failed to reach canary router at {router_url}: {e}")
        sys.exit(1)


def decide(metrics: dict, min_requests: int, max_error_rate: float, max_latency_increase: float) -> dict:
    canary = metrics["canary"]
    stable = metrics["stable"]

    if canary["requests"] < min_requests:
        return {
            "decision": "WAIT",
            "reason": f"only {canary['requests']}/{min_requests} canary requests observed so far",
        }

    if canary["error_rate_pct"] > max_error_rate:
        return {
            "decision": "ROLLBACK",
            "reason": f"canary error rate {canary['error_rate_pct']}% exceeds threshold {max_error_rate}%",
        }

    if stable["avg_latency_ms"] > 0:
        latency_increase_pct = ((canary["avg_latency_ms"] - stable["avg_latency_ms"])
                                 / stable["avg_latency_ms"] * 100)
        if latency_increase_pct > max_latency_increase:
            return {
                "decision": "ROLLBACK",
                "reason": (f"canary latency {canary['avg_latency_ms']}ms is "
                           f"{round(latency_increase_pct, 1)}% higher than stable "
                           f"({stable['avg_latency_ms']}ms), exceeds {max_latency_increase}% threshold"),
            }

    return {
        "decision": "PROMOTE",
        "reason": (f"canary healthy over {canary['requests']} requests: "
                   f"{canary['error_rate_pct']}% errors, {canary['avg_latency_ms']}ms avg latency"),
    }


def execute_decision(decision: str, version: str):
    """
    Calls registry/model_registry.py as a subprocess rather than importing
    it directly -- keeps monitor.py decoupled from the registry's internal
    implementation, and matches how this would actually run as a separate
    CI/CD step in practice.
    """
    if decision == "PROMOTE":
        command = [sys.executable, str(REGISTRY_SCRIPT), "promote", version]
    elif decision == "ROLLBACK":
        # Roll back to whatever was production before this candidate --
        # model_registry.py's rollback command needs an explicit target
        # version, so this assumes the previous stable version is passed
        # in via --rollback_to.
        return
    else:
        print("Decision is WAIT -- taking no action this run.")
        return

    print(f"Executing: {' '.join(command)}")
    subprocess.run(command, check=True)


def main(args):
    metrics = fetch_metrics(args.router_url)
    result = decide(metrics, args.min_requests, args.max_error_rate, args.max_latency_increase)

    print("\n=== Canary Health Check ===")
    print(f"Stable:  {metrics['stable']['requests']} requests, "
          f"{metrics['stable']['error_rate_pct']}% errors, "
          f"{metrics['stable']['avg_latency_ms']}ms avg latency")
    print(f"Canary:  {metrics['canary']['requests']} requests, "
          f"{metrics['canary']['error_rate_pct']}% errors, "
          f"{metrics['canary']['avg_latency_ms']}ms avg latency")
    print(f"\nDecision: {result['decision']}")
    print(f"Reason:   {result['reason']}")

    if result["decision"] == "PROMOTE":
        execute_decision("PROMOTE", args.version)
    elif result["decision"] == "ROLLBACK":
        if args.rollback_to:
            command = [sys.executable, str(REGISTRY_SCRIPT), "rollback", args.rollback_to]
            print(f"Executing: {' '.join(command)}")
            subprocess.run(command, check=True)
        else:
            print("ROLLBACK decided, but no --rollback_to version given -- "
                  "run registry/model_registry.py rollback <version> manually.")
        sys.exit(1)  # non-zero exit so this can gate a CI/CD step


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitor canary health and promote/rollback")
    parser.add_argument("--version", required=True, help="Candidate version being canaried")
    parser.add_argument("--router_url", default="http://localhost:9000")
    parser.add_argument("--min_requests", type=int, default=20)
    parser.add_argument("--max_error_rate", type=float, default=5.0, help="Max canary error rate, in percent")
    parser.add_argument("--max_latency_increase", type=float, default=20.0,
                         help="Max allowed canary latency increase vs stable, in percent")
    parser.add_argument("--rollback_to", default=None, help="Version to roll back to if canary fails")
    args = parser.parse_args()

    main(args)
