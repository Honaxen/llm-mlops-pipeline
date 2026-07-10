# LLM MLOps Pipeline

An end-to-end CI/CD pipeline connecting evaluation, safety checks, deployment, and rollback for LLM systems — the layer that turns individual ML projects into something that stays production-ready over time.

---

## What This Project Demonstrates

Every other project in this portfolio builds, serves, evaluates, or secures a piece of an LLM system in isolation.
This one connects those pieces into a pipeline that runs automatically on every change.

| Concern | Solution |
|---|---|
| Does a new change break quality? | Automated evaluation gate, compared against the previous version |
| Does a new change break safety? | Automated safety gate running the red-team suite from `llm-safety-redteam` |
| Which model/prompt version is live? | File-based model registry with versioning + metadata |
| How do I deploy without downtime? | Canary traffic router, per-version metrics |
| What if the new version is worse? | Automatic rollback based on live canary metrics |
| Did this change regress anything? | Threshold-based comparison against the previous version's eval results |

---

## Architecture

```
Push to main / workflow_dispatch
  ↓
Unit Tests
  ↓
Evaluation Gate  →  compare vs previous version, fail on regression
  ↓
Safety Gate  →  run llm-safety-redteam attack suite, fail on regression
  ↓
Model Registry  →  version + tag the model/prompt that passed both gates
  ↓
Canary Deployment  →  route a fraction of traffic to the new version
  ↓
Live Monitoring  →  poll canary metrics (error rate, latency)
  ↓
Automatic Promote / Rollback
```

---

## Project Structure

```
llm-mlops-pipeline/
├── .github/workflows/
│   └── pipeline.yml           — test, eval gate, safety gate, version registration
├── regression/
│   └── compare_eval.py        — eval regression check, CI gate (non-zero exit on fail)
├── registry/
│   ├── model_registry.py      — register/promote/rollback CLI
│   └── versions.json          — generated registry state
├── deployment/canary/
│   └── canary_router.py       — traffic split + per-version metrics
├── rollback/
│   └── monitor.py             — polls canary metrics, decides promote/rollback
├── tests/
│   └── test_pipeline.py       — 14/14 passing
├── docs/
│   └── architecture.md
└── requirements.txt
```

---

## Getting Started

```bash
pip install -r requirements.txt
```

### 1. Register a candidate version

```bash
python registry/model_registry.py register v1.3.0 \
  --eval_results regression/regression_report.json \
  --safety_passed true \
  --note "Added AWQ quantization support"
```

### 2. Check for regressions before promoting

```bash
python regression/compare_eval.py \
  --baseline registry/versions/v1.2.0/eval_results.json \
  --current registry/versions/v1.3.0-candidate/eval_results.json \
  --threshold 0.03
```

Example output *(illustrative — replace with your own run)*:
```
=== Evaluation Regression Check ===
Metric               Baseline   Current    Delta      Status
------------------------------------------------------------
faithfulness         0.91       0.93       0.02       ok
relevance            0.87       0.85       -0.02      ok
completeness         0.84       0.79       -0.05      REGRESSED

FAILED -- regression in: completeness
```

### 3. Run the canary router

Point it at two running instances of `llm-inference-optimizer`'s serving layer (stable + candidate), then:

```bash
cd deployment/canary
uvicorn canary_router:app --host 0.0.0.0 --port 9000
```

### 4. Monitor the canary and let it decide

```bash
python rollback/monitor.py \
  --version v1.3.0 \
  --router_url http://localhost:9000 \
  --min_requests 20 \
  --max_error_rate 5.0 \
  --max_latency_increase 20.0 \
  --rollback_to v1.2.0
```

Example output *(illustrative — replace with your own run)*:
```
=== Canary Health Check ===
Stable:  100 requests, 0.0% errors, 412.3ms avg latency
Canary:  34 requests, 1.2% errors, 430.1ms avg latency

Decision: PROMOTE
Reason:   canary healthy over 34 requests: 1.2% errors, 430.1ms avg latency
```

### 5. Run tests

```bash
pytest tests/ -v
```

### 6. Trigger the full CI/CD pipeline

Push to `main`, or run manually with a version to register:

```bash
gh workflow run pipeline.yml -f version=v1.3.0
```

---

## Registry CLI Reference

```bash
python registry/model_registry.py list              # see all versions and their stage
python registry/model_registry.py show v1.3.0        # full metadata for one version
python registry/model_registry.py promote v1.3.0     # promote (blocked if gates failed)
python registry/model_registry.py rollback v1.2.0    # revert production to this version
```

---

## Stack

Python · GitHub Actions · FastAPI · httpx · pytest

---

## What I Learned

**A registry that can't refuse a bad promotion isn't a gate, it's a log.**
The first version of `model_registry.py` just recorded whatever it was told. Adding the check that blocks promotion when `eval_passed` or `safety_passed` is `False` is what actually turned it into an enforcement point instead of a record-keeping tool.

**Rollback decisions need to fail closed.**
Every threshold in `monitor.py` is written to roll back on ambiguity rather than promote on it. An unnecessary rollback costs some velocity; a missed one ships a broken version to all traffic. Those two mistakes are not equally bad, so the logic shouldn't treat them as equal.

**Separating decision logic from execution made testing possible.**
`compare_eval.compare()` and `monitor.decide()` are both pure functions — no file I/O, no network calls. That's what let 14 tests run in 0.03 seconds without a live model or a running server anywhere in the loop.

**A canary router doesn't need to be complicated to be useful.**
No service mesh, no cluster — just a FastAPI process splitting traffic by a random draw and tracking two counters per backend. That's enough to simulate the actual decision this pipeline needs to make: is the new version healthy enough to trust with more traffic?

**GitHub Actions treats `.github/workflows/` differently from every other path.**
Pushing a workflow file needs a token with the `workflow` scope specifically — a good reminder that CI/CD tooling has its own permission model separate from the code it runs.

---

## Related Projects

- [rag-evaluation-framework](https://github.com/Honaxen/rag-evaluation-framework) — the quality gate this pipeline runs on every push
- [llm-safety-redteam](https://github.com/Honaxen/llm-safety-redteam) — the safety gate this pipeline runs on every push
- [llm-inference-optimizer](https://github.com/Honaxen/llm-inference-optimizer) — the serving stack this pipeline deploys and monitors

---

## Author

[Honaxen](https://github.com/Honaxen)