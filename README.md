# LLM MLOps Pipeline

Work in progress -- this README is a placeholder and will be replaced once the project is complete.

An end-to-end CI/CD pipeline connecting evaluation, safety checks, deployment, and rollback for LLM systems -- the layer that turns individual ML projects into something that stays production-ready over time.

---

## What This Project Will Demonstrate

Every other project in this portfolio builds, serves, evaluates, or secures a piece of an LLM system in isolation.
This one connects those pieces into a pipeline that runs automatically on every change.

Concern -> Solution (planned)
- Does a new change break quality?        -> Automated evaluation gate (rag-evaluation-framework) on every push
- Does a new change break safety?          -> Automated safety gate (llm-safety-redteam) on every push
- Which model/prompt version is live?      -> Model registry with versioning + metadata
- How do I deploy without downtime?        -> Canary / blue-green deployment simulation
- What if the new version is worse?        -> Automatic rollback based on live metrics
- Did this change regress anything?        -> Automated comparison against the previous version's eval results

---

## Planned Architecture

Push to main
  -> CI: Evaluation Gate (regression/)        run eval, compare vs previous version
  -> CI: Safety Gate (llm-safety-redteam)      run red-team suite, block on regressions
  -> Model Registry (registry/)                version + tag the model/prompt that passed
  -> Canary Deployment (deployment/canary/)    route a fraction of traffic to the new version
  -> Live Monitoring (rollback/)               watch Prometheus metrics from the canary
  -> Automatic Rollback (rollback/)            revert if metrics degrade, promote if they hold

---

## Project Structure

llm-mlops-pipeline/
  .github/workflows/   - CI/CD pipeline definitions
  registry/             - model/prompt versioning + metadata
  deployment/canary/    - canary/blue-green deploy simulation
  rollback/             - metric monitoring + automatic rollback
  regression/           - eval comparison vs previous version
  tests/
  docs/

---

## Stack

Python - GitHub Actions - Prometheus - Docker - pytest

---

## Status

- [ ] GitHub Actions workflow (eval + safety gates on push)
- [ ] Model/prompt registry with versioning
- [ ] Canary/blue-green deployment simulation
- [ ] Metric-based automatic rollback
- [ ] Regression detection vs previous version

---

## Related Projects

- [rag-evaluation-framework](https://github.com/Honaxen/rag-evaluation-framework) -- the quality gate this pipeline runs on every push
- [llm-safety-redteam](https://github.com/Honaxen/llm-safety-redteam) -- the safety gate this pipeline runs on every push
- [llm-inference-optimizer](https://github.com/Honaxen/llm-inference-optimizer) -- the serving stack this pipeline deploys and monitors

---

## Author

[Honaxen](https://github.com/Honaxen)
