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

import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from mcp.server.transport_security import TransportSecuritySettings

from mvp.lookup import lookup
from mvp.mcp_server import mcp
from mvp.oauth_provider import google_callback, verify_identity_page, verify_identity_submit

_STATIC_DIR = Path(__file__).parent / "static"

# Mounting a streamable-http MCP app under FastAPI doesn't work with a
# bare app.mount() — confirmed by actually running it, not assumed: it
# throws "Task group is not initialized. Make sure to use run()." on the
# first real request, because FastAPI's mount() does not propagate a
# mounted sub-app's own lifespan, and the MCP session manager needs its
# task group started via mcp.session_manager.run() for the app's actual
# lifetime. This lifespan wrapper is that fix, not boilerplate.
#
# transport_security must be set explicitly too — also found by actually
# deploying, not assumed: the SDK auto-enables DNS-rebinding protection
# scoped to 127.0.0.1/localhost ONLY when `host` is left at its default,
# which silently 421s every real request once this is actually deployed
# (confirmed: worked locally, broke on first live request against
# vadium-lookup.onrender.com). The fix is not to disable the protection —
# it's to scope it to the real hostnames this service actually serves.
_ALLOWED_HOSTS = ["vadium-lookup.atesta.io", "127.0.0.1:*", "localhost:*"]
# streamable_http_path="/mcp" (not "/" as before OAuth) — now that
# OAuth is wired in, this sub-app owns its OWN absolute paths (/mcp,
# /authorize, /token, /register, /revoke, /.well-known/...), because the
# SDK computes the RFC 9728 protected-resource-metadata path as relative
# to the domain root unconditionally, with no awareness of a mount
# prefix. Nesting this whole sub-app under a "/mcp" FastAPI mount (the
# pre-OAuth setup) silently doubled that prefix for every OAuth route
# except the transport endpoint itself — confirmed directly: the
# WWW-Authenticate header advertised
# /.well-known/oauth-protected-resource/mcp but that path 404'd, because
# it actually lived at /mcp/.well-known/oauth-protected-resource/mcp.
# Fix: mount this app at FastAPI's root ("/", at the bottom of this
# file, after every other route) instead of under "/mcp" — see the
# app.mount() call below.
_mcp_app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_ALLOWED_HOSTS,
        allowed_origins=[f"https://{h}" for h in _ALLOWED_HOSTS if "127.0.0.1" not in h and "localhost" not in h]
        + ["http://127.0.0.1:*", "http://localhost:*"],
    ),
)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(
    title="Vadium Lookup (MVP)",
    description=(
        "Free trust-record lookup for an ERC-8004 agentId: our own Phase 2 "
        "evidence-ledger history, plus ERC-8004's public reputation feedback "
        "shown both raw and Sybil-adjusted (diversity-weighted by funding "
        "cluster). No payment required — this endpoint exists to test "
        "discoverability, not to generate revenue."
    ),
    lifespan=_lifespan,
)

# Mounted, not a separate service — see docs/mcp-server-spec.md for why.
# Same `mcp` instance the stdio console script (`vadium-lookup-mcp`) runs;
# this just adds a second, network-reachable transport for the same
# tools, so a remote agent (e.g. a ChatGPT "Dot") can call check_agent_trust
# directly instead of needing a local subprocess.
#
# streamable_http_path="/" (set above) is required, not cosmetic: the
# sub-app defaults to routing at "/mcp" *internally*, which combined with
# mounting it at "/mcp" below would serve the real endpoint at /mcp/mcp,
# not /mcp. Verified directly against a running instance, not assumed.
# The human half of /authorize (docs/oauth-trust-spec.md). Two
# identity-proofing paths coexist here: Google Sign-In (the active
# default — VadiumOAuthProvider.authorize() redirects straight to
# Google itself, and google_callback is where Google redirects back
# to), and the original wallet-signature page (verify_identity_page /
# verify_identity_submit) — fully intact but only reached if
# VADIUM_AUTH_METHOD=wallet is set. Plain Starlette routes, not FastAPI
# path operations, since they return raw HTML/JSON/redirects rather
# than being part of this app's own schema.
app.add_route("/oauth/google/callback", google_callback, methods=["GET"])
app.add_route("/oauth/verify-identity", verify_identity_page, methods=["GET"])
app.add_route("/oauth/verify-identity/submit", verify_identity_submit, methods=["POST"])

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


# ============================================================
# Paid twin of the same lookup, via x402 — NOT the "real" version of
# this product, a deliberate second instrument. The free route above
# stays the default and the one everywhere else (MCP, README) points
# to. This one exists for two things the free route structurally can't
# measure: (1) willingness-to-pay, directly, not inferred from someone
# else's comparable; (2) x402 Bazaar's discovery mechanism is
# payment-triggered — a service is only auto-indexed once the CDP
# facilitator processes a real payment against it, so a free-only
# endpoint is invisible there no matter how it's listed.
#
# Disabled entirely (route never registered) unless X402_PAY_TO_ADDRESS
# is set — no half-configured payment route silently accepting
# requests it can't actually settle.
# ============================================================

X402_PAY_TO_ADDRESS = os.environ.get("X402_PAY_TO_ADDRESS")

if X402_PAY_TO_ADDRESS:
    from cdp.x402 import create_facilitator_config
    from x402.extensions.bazaar import (
        OutputConfig,
        bazaar_resource_server_extension,
        declare_discovery_extension,
    )
    from x402.http import HTTPFacilitatorClient, PaymentOption
    from x402.http.middleware.fastapi import PaymentMiddlewareASGI
    from x402.http.types import RouteConfig
    from x402.mechanisms.evm.exact import ExactEvmServerScheme
    from x402.schemas import Network

    from x402.server import x402ResourceServer

    _BASE_MAINNET: Network = "eip155:8453"

    # create_facilitator_config() reads CDP_API_KEY_ID/CDP_API_KEY_SECRET
    # from the environment and points at Coinbase's CDP facilitator
    # either way — without those set, verify/settle calls will fail
    # (expected, not a crash) until real credentials are added; the 402
    # challenge itself is generated locally and works regardless.
    _facilitator = HTTPFacilitatorClient(create_facilitator_config())
    _x402_server = x402ResourceServer(_facilitator)
    _x402_server.register(_BASE_MAINNET, ExactEvmServerScheme())
    # Registering this is what actually makes the route's `extensions`
    # dict below get enriched (HTTP method, route template) and surfaced
    # to the CDP facilitator's Bazaar catalog — a route with a bare
    # `extensions={"bazaar": ...}` dict and no registered extension is
    # still invisible to the indexer. Confirmed 2026-09-29: this was
    # missing entirely, which is why the service never showed up in the
    # live catalog (checked directly against the 19,128-entry public
    # discovery/resources feed) despite the route being live.
    _x402_server.register_extension(bazaar_resource_server_extension)

    _PAID_PRICE = "$0.001"  # matches the x402 quickstart's own example; trivial by design, not a revenue price

    _LOOKUP_BAZAAR_EXTENSION = declare_discovery_extension(
        path_params_schema={
            "properties": {"agent_id": {"type": "string", "description": "ERC-8004 agentId to look up"}},
            "required": ["agent_id"],
        },
        output=OutputConfig(
            example={
                "agent_id": 95910,
                "vadium_native": {"disputes": 0, "objective_slashes": 0, "note": "no Vadium-native history yet"},
                "erc8004_public": {
                    "available": True,
                    "raw": {"count": 3, "value": 2, "reviewer_count_considered": 3,
                            "source": "ERC-8004 ReputationRegistry (raw, unweighted)"},
                    "sybil_adjusted": {"score": 1.4, "note": "diversity-weighted by funding cluster"},
                },
            },
        ),
    )

    app.add_middleware(
        PaymentMiddlewareASGI,
        routes={
            # This SDK's own path syntax (":param" / "[param]" / "*"), NOT
            # FastAPI's "{param}" — confirmed by reading
            # x402_http_server_base.py's _parse_route_pattern directly
            # after "{agent_id}" silently matched nothing and let requests
            # through unprotected. Verify against real traffic again if
            # this SDK version ever changes.
            "GET /lookup-paid/:agent_id": RouteConfig(
                accepts=[
                    PaymentOption(
                        scheme="exact",
                        pay_to=X402_PAY_TO_ADDRESS,
                        price=_PAID_PRICE,
                        network=_BASE_MAINNET,
                    ),
                ],
                mime_type="application/json",
                description=(
                    "Same lookup as /lookup/{agentId} — this route exists to "
                    "measure willingness-to-pay and x402 Bazaar discoverability, "
                    "not because the result is different or better paid."
                ),
                service_name="Vadium Lookup",
                tags=["reputation", "trust", "erc-8004", "agent-identity"],
                icon_url="https://vadium-lookup.atesta.io/logo.svg",
                extensions=_LOOKUP_BAZAAR_EXTENSION,
            ),
        },
        server=_x402_server,
    )

    @app.get("/lookup-paid/{agent_id}")
    def get_lookup_paid(
        agent_id: int,
        request: Request,
        reviewers: str | None = Query(default=None),
        via: str = Query(default="x402-paid"),
    ):
        caller_ip = _client_ip(request)
        _check_rate_limit(caller_key=caller_ip)  # still a guard, even though payment itself throttles most abuse
        _discovery_counts[via] += 1
        print(
            f"lookup_paid agent_id={agent_id} via={via} caller_ip={caller_ip} "
            f"at={datetime.now(timezone.utc).isoformat()}",
            flush=True,
        )
        known = [a.strip() for a in reviewers.split(",")] if reviewers else None
        try:
            return lookup(agent_id, known_reviewers=known)
        except Exception as e:  # noqa: BLE001
            return JSONResponse(status_code=502, content={"error": str(e)})
else:
    print("X402_PAY_TO_ADDRESS not set — /lookup-paid route disabled, free /lookup route unaffected", flush=True)


# ============================================================
# ERC-8004 agent registration file — the "agentURI" a registered
# agentId's tokenURI points to (see ERC8004SPEC.md's registration-v1
# format). Deliberately only lists what's real and live: no A2A/OASF/
# ENS/DID entries we don't actually implement, no "crypto-economic" or
# "tee-attestation" trust claims we can't back yet. `services` now
# includes a real MCP entry — the earlier version of this comment said
# it was omitted because the MCP server only ran over stdio, with no
# network URL to honestly list; that's no longer true as of the
# streamable-http mount (docs/mcp-server-spec.md), so the omission
# would now be stale, not honest.
#
# `registrations` is genuinely unknown until after on-chain registration
# mints an agentId (register() first, read the agentId back, then
# setAgentURI() to point at this file) — omitted, not faked, until
# ERC8004_AGENT_ID is set post-registration. See ADR-016
# (docs/phase3-design-decisions.md) for why `payTo` never appears here:
# this file's identity is agentWallet, not whichever wallet a given
# payment happens to land in.
# ============================================================

_ERC8004_IDENTITY_REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
_ERC8004_AGENT_ID = os.environ.get("ERC8004_AGENT_ID")


@app.get("/.well-known/agent-registration.json")
def agent_registration():
    doc = {
        "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
        "name": "Vadium Lookup",
        "description": (
            "Free ERC-8004 trust-record lookup by agentId: raw public "
            "reputation feedback plus a Sybil-adjusted score, diversity-"
            "weighted by funding cluster so a group of reviewers tracing "
            "back to one funder counts as roughly one independent voice. "
            "An optional paid twin exists at $0.001 USDC via x402, to "
            "measure willingness-to-pay directly rather than infer it."
        ),
        "image": "https://vadium-lookup.atesta.io/logo.svg",
        "services": [
            {"name": "web", "endpoint": "https://vadium-lookup.atesta.io/"},
            {"name": "mcp", "endpoint": "https://vadium-lookup.atesta.io/mcp"},
        ],
        "x402Support": True,
        "active": True,
        "supportedTrust": ["reputation"],
    }
    if _ERC8004_AGENT_ID:
        doc["registrations"] = [
            {
                "agentId": int(_ERC8004_AGENT_ID),
                "agentRegistry": f"eip155:8453:{_ERC8004_IDENTITY_REGISTRY}",
            }
        ]
    return doc


@app.get("/logo.svg")
def logo():
    return FileResponse(_STATIC_DIR / "logo.svg", media_type="image/svg+xml")


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


# Mounted at root, and LAST — order matters. Every route above is tried
# first (Starlette matches in registration order); only a path none of
# them match (/mcp, /authorize, /token, /register, /revoke,
# /.well-known/oauth-authorization-server,
# /.well-known/oauth-protected-resource/mcp) falls through to this
# sub-app. Mounting at "/" rather than "/mcp" is required, not a style
# choice — see the long comment above _mcp_app's construction.
app.mount("/", _mcp_app)
