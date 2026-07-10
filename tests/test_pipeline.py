"""
Unit tests for the pure/file-local logic in this pipeline:
  - regression/compare_eval.py's compare() -- pure function, no I/O
  - registry/model_registry.py -- file-based registry, tested against a
    temp file so these tests never touch the real versions.json
  - rollback/monitor.py's decide() -- pure function, no I/O

canary_router.py and the GitHub Actions workflow itself aren't covered
here -- they need a running server / a real CI environment respectively,
the same reasoning applied to serving/vllm_server.py's live-model paths
in llm-inference-optimizer.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "regression"))
sys.path.insert(0, str(Path(__file__).parent.parent / "registry"))
sys.path.insert(0, str(Path(__file__).parent.parent / "rollback"))

from compare_eval import compare  # noqa: E402
import model_registry  # noqa: E402
from monitor import decide  # noqa: E402


# --- compare_eval.compare() tests ---

def test_compare_passes_when_no_metric_drops():
    baseline = {"faithfulness": 0.90, "relevance": 0.85}
    current = {"faithfulness": 0.91, "relevance": 0.86}
    result = compare(baseline, current, threshold=0.03)
    assert result["passed"] is True
    assert result["regressions"] == []


def test_compare_flags_regression_beyond_threshold():
    baseline = {"faithfulness": 0.90, "relevance": 0.85}
    current = {"faithfulness": 0.80, "relevance": 0.86}
    result = compare(baseline, current, threshold=0.03)
    assert result["passed"] is False
    assert "faithfulness" in result["regressions"]
    assert "relevance" not in result["regressions"]


def test_compare_allows_drop_within_threshold():
    baseline = {"faithfulness": 0.90}
    current = {"faithfulness": 0.88}  # drop of 0.02, threshold 0.03
    result = compare(baseline, current, threshold=0.03)
    assert result["passed"] is True


def test_compare_skips_metrics_missing_from_one_side():
    baseline = {"faithfulness": 0.90}
    current = {"faithfulness": 0.91, "new_metric": 0.75}
    result = compare(baseline, current, threshold=0.03)
    assert result["passed"] is True
    skipped = [c for c in result["comparisons"] if c["metric"] == "new_metric"]
    assert skipped[0]["note"] == "metric missing from one side, skipped"


# --- model_registry tests ---

@pytest.fixture
def temp_registry(tmp_path, monkeypatch):
    """Point the registry at a throwaway file for the duration of each test."""
    registry_file = tmp_path / "versions.json"
    monkeypatch.setattr(model_registry, "REGISTRY_FILE", registry_file)
    return registry_file


def test_register_version_creates_entry(temp_registry):
    model_registry.register_version("v1.0.0", note="initial release")
    registry = model_registry.load_registry()
    assert "v1.0.0" in registry["versions"]
    assert registry["versions"]["v1.0.0"]["stage"] == "candidate"
    assert registry["versions"]["v1.0.0"]["note"] == "initial release"


def test_register_version_does_not_overwrite_existing(temp_registry):
    model_registry.register_version("v1.0.0", note="first")
    model_registry.register_version("v1.0.0", note="second")  # should be a no-op
    registry = model_registry.load_registry()
    assert registry["versions"]["v1.0.0"]["note"] == "first"


def test_promote_sets_production_version(temp_registry):
    model_registry.register_version("v1.0.0")
    model_registry.registry_cache = None  # no-op guard in case of future caching
    reg = model_registry.load_registry()
    reg["versions"]["v1.0.0"]["eval_passed"] = True
    reg["versions"]["v1.0.0"]["safety_passed"] = True
    model_registry.save_registry(reg)

    model_registry.promote_version("v1.0.0")
    registry = model_registry.load_registry()
    assert registry["production_version"] == "v1.0.0"
    assert registry["versions"]["v1.0.0"]["stage"] == "production"


def test_promote_refuses_when_eval_failed(temp_registry):
    model_registry.register_version("v1.0.0")
    reg = model_registry.load_registry()
    reg["versions"]["v1.0.0"]["eval_passed"] = False
    model_registry.save_registry(reg)

    model_registry.promote_version("v1.0.0")
    registry = model_registry.load_registry()
    assert registry["production_version"] is None


def test_promote_supersedes_previous_production(temp_registry):
    for version in ["v1.0.0", "v1.1.0"]:
        model_registry.register_version(version)
        reg = model_registry.load_registry()
        reg["versions"][version]["eval_passed"] = True
        reg["versions"][version]["safety_passed"] = True
        model_registry.save_registry(reg)

    model_registry.promote_version("v1.0.0")
    model_registry.promote_version("v1.1.0")

    registry = model_registry.load_registry()
    assert registry["production_version"] == "v1.1.0"
    assert registry["versions"]["v1.0.0"]["stage"] == "superseded"


def test_rollback_reverts_production_version(temp_registry):
    for version in ["v1.0.0", "v1.1.0"]:
        model_registry.register_version(version)
        reg = model_registry.load_registry()
        reg["versions"][version]["eval_passed"] = True
        reg["versions"][version]["safety_passed"] = True
        model_registry.save_registry(reg)

    model_registry.promote_version("v1.1.0")
    model_registry.rollback_version("v1.0.0")

    registry = model_registry.load_registry()
    assert registry["production_version"] == "v1.0.0"
    assert registry["versions"]["v1.1.0"]["stage"] == "rolled_back"


# --- monitor.decide() tests ---

def make_metrics(canary_requests, canary_errors_pct, canary_latency, stable_latency=100.0):
    return {
        "stable": {"requests": 100, "error_rate_pct": 0.0, "avg_latency_ms": stable_latency},
        "canary": {"requests": canary_requests, "error_rate_pct": canary_errors_pct, "avg_latency_ms": canary_latency},
    }


def test_decide_waits_below_min_requests():
    metrics = make_metrics(canary_requests=5, canary_errors_pct=0.0, canary_latency=100.0)
    result = decide(metrics, min_requests=20, max_error_rate=5.0, max_latency_increase=20.0)
    assert result["decision"] == "WAIT"


def test_decide_rolls_back_on_high_error_rate():
    metrics = make_metrics(canary_requests=50, canary_errors_pct=8.0, canary_latency=100.0)
    result = decide(metrics, min_requests=20, max_error_rate=5.0, max_latency_increase=20.0)
    assert result["decision"] == "ROLLBACK"
    assert "error rate" in result["reason"]


def test_decide_rolls_back_on_high_latency_increase():
    metrics = make_metrics(canary_requests=50, canary_errors_pct=0.0, canary_latency=150.0, stable_latency=100.0)
    result = decide(metrics, min_requests=20, max_error_rate=5.0, max_latency_increase=20.0)
    assert result["decision"] == "ROLLBACK"
    assert "latency" in result["reason"]


def test_decide_promotes_when_healthy():
    metrics = make_metrics(canary_requests=50, canary_errors_pct=1.0, canary_latency=105.0, stable_latency=100.0)
    result = decide(metrics, min_requests=20, max_error_rate=5.0, max_latency_increase=20.0)
    assert result["decision"] == "PROMOTE"
