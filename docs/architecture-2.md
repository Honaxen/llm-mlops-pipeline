# Architecture

## Overview

This project connects the isolated pieces built in earlier repos --
evaluation, safety testing, serving -- into one pipeline that runs
automatically on every change and decides, without a human in the loop,
whether a new version is safe to promote:

```
Push to main / workflow_dispatch
    |
    v
Unit Tests (tests/)                    "Does the pipeline code itself work?"
    |
    v
Evaluation Gate (regression/)          "Did quality regress vs the last version?"
    |
    v
Safety Gate (llm-safety-redteam)       "Did safety regress vs the last version?"
    |
    v
Model Registry (registry/)             "Record this version, with its gate results."
    |
    v
Canary Deployment (deployment/canary/) "Route a small slice of real traffic to it."
    |
    v
Live Monitoring + Rollback (rollback/) "Watch it. Promote if healthy, revert if not."
```

Nothing here trains or serves a model. Every heavy-lifting piece (the
model itself, its evaluation suite, its red-team suite) is a separate
repo. This project is the glue: it decides, based on their outputs,
whether a change is allowed to reach production, and reacts automatically
if it turns out to be wrong.

---

## Stage 1: Unit Tests

`tests/test_pipeline.py` covers the pure decision logic in this repo:
`compare_eval.py`'s regression comparison, `model_registry.py`'s
register/promote/rollback state transitions, and `monitor.py`'s
promote-vs-rollback decision function. All three are tested as plain
functions against temp files or fixtures, not against a live model --
the same separation used in `llm-inference-optimizer` and
`llm-safety-redteam`, where anything needing a real model or a running
server is treated as a manual/integration concern, not a unit test.

---

## Stage 2: Evaluation Gate

`regression/compare_eval.py` takes two flat `metric -> score` JSON files
(the shape `rag-evaluation-framework` already produces) and flags any
metric that dropped by more than a threshold. It exits non-zero on
failure specifically so it can gate a CI step directly -- no wrapper
logic needed in the workflow file, just `run: python compare_eval.py ...`
and GitHub Actions handles the rest.

Metrics present on only one side (baseline or current) are reported but
not treated as regressions -- that usually means the eval suite itself
changed, which is a different problem than the model getting worse, and
conflating the two would make the gate noisy and easy to start ignoring.

---

## Stage 3: Safety Gate

The workflow checks out `llm-safety-redteam` as a separate step and runs
its attack suite against the candidate model, failing the build if any
category's attack success rate exceeds 10%. This is the same repo used
standalone for manual red-teaming, now wired into CI so a regression in
safety behavior is caught automatically, not just when someone remembers
to run it by hand.

This job targets a self-hosted runner rather than GitHub's default
hosted runners, because it needs a local Ollama instance -- something a
hosted runner can't provide. In a real setup, this runner would be a
machine (or persistent container) with Ollama already running and the
target model pulled.

---

## Stage 4: Model Registry

`registry/model_registry.py` is a deliberately simple, file-based
registry (`versions.json`, no database). Every version passes through
states: `candidate -> production -> superseded` (when a newer version
is promoted) or `-> rolled_back` (when `rollback/monitor.py` reverts it).

The registry actively **refuses to promote** a version whose recorded
`eval_passed` or `safety_passed` is `False` -- promotion isn't just a
label change, it's a gate in itself. This is what stops a human (or a
misconfigured workflow) from manually forcing a bad version live and
skipping Stages 2-3 entirely.

---

## Stage 5: Canary Deployment

`deployment/canary/canary_router.py` sits in front of two backends --
"stable" (current production) and "canary" (the new candidate) -- and
splits traffic between them by a configurable weight, tracking request
count, error count, and latency separately per version.

This simulates, without needing a real cluster, what a canary rollout
does at the infrastructure level: expose the new version to a small,
real slice of traffic before trusting it with all of it. It's designed
to point at two running instances of `llm-inference-optimizer`'s serving
layer (different ports or different model versions) -- this repo doesn't
reimplement serving, it routes to it.

---

## Stage 6: Monitoring + Automatic Rollback

`rollback/monitor.py` polls the canary router's `/canary-metrics`
endpoint and applies three fixed, explainable rules -- not a black-box
scoring model:

1. Not enough canary traffic yet -> **WAIT**, decide nothing.
2. Canary error rate above threshold -> **ROLLBACK**.
3. Canary latency more than X% worse than stable -> **ROLLBACK**.
4. Otherwise, once there's enough traffic -> **PROMOTE**.

The rollback threshold checks fail closed on purpose: an unnecessary
rollback costs some deployment velocity, but a missed rollback ships a
broken version to 100% of traffic. That asymmetry is why every rule above
is written to roll back on ambiguity rather than promote on it.

On a PROMOTE decision, this script calls `model_registry.py promote`
directly. On ROLLBACK, it calls `model_registry.py rollback` (given an
explicit target version) and exits non-zero -- which, wired into a
scheduled CI job polling a live canary, is what makes rollback actually
automatic rather than something a human has to notice and trigger.

---

## Why This Order

Each stage exists because the one before it isn't sufficient alone:

- Passing unit tests doesn't mean the *model* is good -- that's what the
  eval gate checks.
- Passing eval doesn't mean the model is *safe* -- that's a separate gate
  because quality and safety can regress independently of each other.
- Passing both gates doesn't mean it's safe to trust with 100% of
  production traffic immediately -- that's what the canary stage is for.
- And a canary that looks fine on synthetic pre-deploy checks can still
  degrade under real traffic -- which is why monitoring and rollback run
  continuously, not just once at deploy time.

The pipeline's job is to make each of those failure modes someone else's
problem to build (a model, an eval suite, a red-team suite) and this
repo's problem to enforce.
