"""
A lightweight, file-based registry for tracking model/prompt versions
through the pipeline: registered -> passed gates -> promoted to production
-> (possibly) rolled back.

No database here on purpose -- a single JSON file is enough for a
portfolio-scale registry, keeps the whole thing readable in a diff, and
plays well with being committed alongside the code it's versioning.

Usage as a CLI:
    python model_registry.py register v1.3.0 \
        --eval_results ../regression/regression_report.json \
        --safety_passed true \
        --note "Added AWQ quantization support"

    python model_registry.py promote v1.3.0
    python model_registry.py rollback v1.3.0
    python model_registry.py list
    python model_registry.py show v1.3.0
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_FILE = Path(__file__).parent / "versions.json"


def load_registry() -> dict:
    if not REGISTRY_FILE.exists():
        return {"versions": {}, "production_version": None}
    with open(REGISTRY_FILE, "r") as f:
        return json.load(f)


def save_registry(registry: dict):
    with open(REGISTRY_FILE, "w") as f:
        json.dump(registry, f, indent=2)


def register_version(version: str, eval_results_path: str = None, safety_passed: bool = None, note: str = ""):
    registry = load_registry()

    if version in registry["versions"]:
        print(f"Version {version} already exists -- use a new version string.")
        return

    eval_summary = None
    if eval_results_path:
        with open(eval_results_path, "r") as f:
            eval_summary = json.load(f)

    registry["versions"][version] = {
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "stage": "candidate",
        "eval_passed": eval_summary.get("passed") if eval_summary else None,
        "eval_summary": eval_summary,
        "safety_passed": safety_passed,
        "note": note,
    }

    save_registry(registry)
    print(f"Registered {version} as candidate.")


def promote_version(version: str):
    """
    Moves a version to production. Only allowed if it previously passed
    both gates -- promoting a version that failed eval or safety checks
    would defeat the whole point of having gates in the first place.
    """
    registry = load_registry()
    entry = registry["versions"].get(version)

    if entry is None:
        print(f"Version {version} not found.")
        return

    if entry["eval_passed"] is False:
        print(f"Refusing to promote {version}: eval gate failed.")
        return
    if entry["safety_passed"] is False:
        print(f"Refusing to promote {version}: safety gate failed.")
        return

    previous_production = registry.get("production_version")
    if previous_production and previous_production in registry["versions"]:
        registry["versions"][previous_production]["stage"] = "superseded"

    entry["stage"] = "production"
    entry["promoted_at"] = datetime.now(timezone.utc).isoformat()
    registry["production_version"] = version

    save_registry(registry)
    print(f"Promoted {version} to production" +
          (f" (superseded {previous_production})" if previous_production else ""))


def rollback_version(version: str):
    """
    Reverts production back to a previously known-good version.
    Used both manually and by rollback/monitor.py when live metrics
    degrade after a promotion.
    """
    registry = load_registry()
    entry = registry["versions"].get(version)

    if entry is None:
        print(f"Version {version} not found.")
        return

    current_production = registry.get("production_version")
    if current_production and current_production in registry["versions"]:
        registry["versions"][current_production]["stage"] = "rolled_back"

    entry["stage"] = "production"
    entry["rolled_back_to_at"] = datetime.now(timezone.utc).isoformat()
    registry["production_version"] = version

    save_registry(registry)
    print(f"Rolled back production to {version}" +
          (f" (from {current_production})" if current_production else ""))


def list_versions():
    registry = load_registry()
    if not registry["versions"]:
        print("No versions registered yet.")
        return

    print(f"{'Version':<15} {'Stage':<12} {'Eval':<8} {'Safety':<8} {'Registered'}")
    print("-" * 70)
    for version, entry in registry["versions"].items():
        marker = " <-- production" if version == registry.get("production_version") else ""
        eval_status = "pass" if entry["eval_passed"] else ("fail" if entry["eval_passed"] is False else "-")
        safety_status = "pass" if entry["safety_passed"] else ("fail" if entry["safety_passed"] is False else "-")
        print(f"{version:<15} {entry['stage']:<12} {eval_status:<8} {safety_status:<8} "
              f"{entry['registered_at'][:19]}{marker}")


def show_version(version: str):
    registry = load_registry()
    entry = registry["versions"].get(version)
    if entry is None:
        print(f"Version {version} not found.")
        return
    print(json.dumps({version: entry}, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Model/prompt version registry")
    subparsers = parser.add_subparsers(dest="command", required=True)

    register_parser = subparsers.add_parser("register")
    register_parser.add_argument("version")
    register_parser.add_argument("--eval_results", default=None)
    register_parser.add_argument("--safety_passed", type=lambda x: x.lower() == "true", default=None)
    register_parser.add_argument("--note", default="")

    promote_parser = subparsers.add_parser("promote")
    promote_parser.add_argument("version")

    rollback_parser = subparsers.add_parser("rollback")
    rollback_parser.add_argument("version")

    subparsers.add_parser("list")

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("version")

    args = parser.parse_args()

    if args.command == "register":
        register_version(args.version, args.eval_results, args.safety_passed, args.note)
    elif args.command == "promote":
        promote_version(args.version)
    elif args.command == "rollback":
        rollback_version(args.version)
    elif args.command == "list":
        list_versions()
    elif args.command == "show":
        show_version(args.version)


if __name__ == "__main__":
    main()
