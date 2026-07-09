"""
A thin routing layer that splits incoming traffic between a "stable"
backend and a "canary" (candidate) backend, and tracks per-version
metrics separately -- request count, error count, and latency.

This simulates what a real canary deployment does at the infrastructure
level (e.g. an Istio VirtualService or a Kubernetes ingress weight split)
without needing an actual cluster: point STABLE_URL and CANARY_URL at two
running instances of llm-inference-optimizer's vllm_server.py (different
ports, or different model versions), and this router does the split.

Traffic is routed by a simple random draw against `canary_weight` --
good enough to simulate gradual rollout (start at 5%, watch metrics,
increase). rollback/monitor.py reads the per-version stats this router
exposes at /canary-metrics to decide whether to promote or roll back.

Usage:
    uvicorn canary_router:app --host 0.0.0.0 --port 9000
"""

import random
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

STABLE_URL = "http://localhost:8000"
CANARY_URL = "http://localhost:8001"

# Fraction of traffic sent to the canary. Meant to be increased manually
# (or by an external script) as confidence in the canary grows --
# start small, e.g. 0.05, not 0.5.
CANARY_WEIGHT = 0.10

# Per-version stats, reset only on router restart. In a real system this
# would live in Prometheus/a time series store; kept in-memory here since
# this router's whole job is to be a simple, inspectable simulation.
stats = {
    "stable": {"requests": 0, "errors": 0, "total_latency_ms": 0.0},
    "canary": {"requests": 0, "errors": 0, "total_latency_ms": 0.0},
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(timeout=60.0)
    yield
    await app.state.client.aclose()


app = FastAPI(title="Canary Router", lifespan=lifespan)


def choose_backend() -> tuple[str, str]:
    """Returns (label, base_url). Random draw against CANARY_WEIGHT."""
    if random.random() < CANARY_WEIGHT:
        return "canary", CANARY_URL
    return "stable", STABLE_URL


@app.post("/generate")
async def generate(request: Request):
    label, base_url = choose_backend()
    body = await request.json()

    start = time.perf_counter()
    try:
        response = await request.app.state.client.post(f"{base_url}/generate", json=body)
        elapsed_ms = (time.perf_counter() - start) * 1000

        stats[label]["requests"] += 1
        stats[label]["total_latency_ms"] += elapsed_ms

        if response.status_code >= 500:
            stats[label]["errors"] += 1

        return JSONResponse(
            content=response.json(),
            status_code=response.status_code,
            headers={"X-Routed-To": label},
        )

    except httpx.RequestError as e:
        stats[label]["requests"] += 1
        stats[label]["errors"] += 1
        raise HTTPException(status_code=502, detail=f"{label} backend unreachable: {e}")


@app.get("/canary-metrics")
async def canary_metrics():
    """
    Summary stats per version -- this is what rollback/monitor.py polls
    to decide whether the canary is healthy enough to promote.
    """
    result = {}
    for label, s in stats.items():
        avg_latency = (s["total_latency_ms"] / s["requests"]) if s["requests"] else 0.0
        error_rate = (s["errors"] / s["requests"] * 100) if s["requests"] else 0.0
        result[label] = {
            "requests": s["requests"],
            "errors": s["errors"],
            "error_rate_pct": round(error_rate, 2),
            "avg_latency_ms": round(avg_latency, 2),
        }
    result["canary_weight"] = CANARY_WEIGHT
    return result


@app.get("/health")
async def health():
    return {"status": "ok", "stable_url": STABLE_URL, "canary_url": CANARY_URL}
