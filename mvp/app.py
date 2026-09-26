"""Free, unpriced HTTP endpoint — the whole point is measuring
discoverability, not revenue (see the discussion in docs/IDEA.md this
was scoped against). Rate-limited per caller as an abuse guard only,
never a paywall.

Run locally:
    .venv/bin/uvicorn mvp.app:app --reload --port 8000

Then:
    curl "http://localhost:8000/lookup/123"
    curl "http://localhost:8000/lookup/123?reviewers=0xabc...,0xdef..."
"""

import time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from mvp.lookup import lookup

app = FastAPI(
    title="Vadium Lookup (MVP)",
    description=(
        "Free trust-record lookup for an ERC-8004 agentId: our own Phase 2 "
        "evidence-ledger history, plus ERC-8004's public reputation feedback "
        "shown both raw and Sybil-adjusted (diversity-weighted by funding "
        "cluster). No payment required — this endpoint exists to test "
        "discoverability, not to generate revenue."
    ),
)

# Abuse guard, not a paywall: a simple fixed-window limiter, per caller —
# fixed 2026-09-27, was keyed "global" (one shared bucket for every
# caller), which meant one aggressive or buggy caller could exhaust the
# whole quota for everyone else, including whichever discovery channel
# we most want signal from. Keyed by IP now, not a real caller identity
# (we have none pre-payment), which is an honest limitation, not a
# pretense of strong identity.
_RATE_LIMIT = 30  # requests
_RATE_WINDOW = 60  # seconds
_calls: dict[str, list[float]] = defaultdict(list)

# Per-channel discovery counters (ADR discussion: "measure discoverability
# irrespective of mode" — MCP, ERC-8004 self-listing, marketplace listings
# all point at this same endpoint with a different `via` value so we can
# actually compare, not guess). In-memory, resets on redeploy/restart —
# real numbers also go to stdout below, which Render retains in its own
# log history, so a restart doesn't erase the only record.
_discovery_counts: dict[str, int] = defaultdict(int)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()  # first hop = real client, per Render's proxy convention
    return request.client.host if request.client else "unknown"


def _check_rate_limit(caller_key: str):
    now = time.time()
    recent = [t for t in _calls[caller_key] if now - t < _RATE_WINDOW]
    if len(recent) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="rate limit exceeded — try again shortly")
    recent.append(now)
    _calls[caller_key] = recent


@app.get("/lookup/{agent_id}")
def get_lookup(
    agent_id: int,
    request: Request,
    reviewers: str | None = Query(
        default=None,
        description="Comma-separated reviewer addresses, temporary workaround "
                     "until on-chain reviewer discovery is wired up — see "
                     "mvp/erc8004_client.py",
    ),
    via: str = Query(
        default="direct",
        description="Which discovery channel sent this caller here — e.g. "
                     "mcp, erc8004-registry, circle-marketplace. Set this in "
                     "each listing's documented URL so discoverability is "
                     "measurable per channel, not guessed at.",
    ),
):
    caller_ip = _client_ip(request)
    _check_rate_limit(caller_key=caller_ip)
    _discovery_counts[via] += 1
    print(
        f"lookup agent_id={agent_id} via={via} caller_ip={caller_ip} "
        f"at={datetime.now(timezone.utc).isoformat()}",
        flush=True,
    )
    known = [a.strip() for a in reviewers.split(",")] if reviewers else None
    try:
        return lookup(agent_id, known_reviewers=known)
    except Exception as e:  # noqa: BLE001 — a lookup failure should be a clear message, not a stack trace to the caller
        return JSONResponse(status_code=502, content={"error": str(e)})


@app.get("/stats")
def stats():
    """In-memory only — real, durable record is the stdout log lines
    above, visible in Render's own log history. This is a convenience
    view, not the source of truth, and resets on redeploy/restart.
    """
    return {"discovery_counts_by_channel": dict(_discovery_counts)}


@app.get("/")
def root():
    return {
        "service": "Vadium Lookup (MVP)",
        "by": "Atesta",
        "status": "free, no payment required",
        "usage": "GET /lookup/{agentId}",
        "note": "This is Phase 2 (evidence ledger + public reputation lookup), "
                "not the collateral/dispute-resolution product — that's Phase 3, "
                "designed but not yet built.",
    }
