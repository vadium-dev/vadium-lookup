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

from fastapi import FastAPI, HTTPException, Query
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

# Abuse guard, not a paywall: a simple fixed-window limiter is enough at
# MVP volume — swap for something real before this sees production
# traffic, but don't add payment as the "real" version of this.
_RATE_LIMIT = 30  # requests
_RATE_WINDOW = 60  # seconds
_calls: dict[str, list[float]] = defaultdict(list)


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
    reviewers: str | None = Query(
        default=None,
        description="Comma-separated reviewer addresses, temporary workaround "
                     "until on-chain reviewer discovery is wired up — see "
                     "mvp/erc8004_client.py",
    ),
):
    _check_rate_limit(caller_key="global")  # per-IP keying belongs at the reverse-proxy layer once deployed
    known = [a.strip() for a in reviewers.split(",")] if reviewers else None
    try:
        return lookup(agent_id, known_reviewers=known)
    except Exception as e:  # noqa: BLE001 — a lookup failure should be a clear message, not a stack trace to the caller
        return JSONResponse(status_code=502, content={"error": str(e)})


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
