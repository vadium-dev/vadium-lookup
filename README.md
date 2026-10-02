# Vadium Lookup

Free, unpriced trust-record lookup for an ERC-8004 `agentId`: your own
recorded history (if any) plus ERC-8004's public reputation feedback,
shown both raw and Sybil-adjusted — diversity-weighted by funding
cluster, so a group of reviewers that all trace back to one funding
source counts as roughly one independent voice, not many.

No payment required anywhere in this service. It exists to test
whether agents discover and use it, not to generate revenue — the
collateral/dispute-resolution product this is a front door for is
separate and not part of this repo.

## Run locally

```bash
pip install -r requirements.txt
export BASESCAN_API_KEY="your-key"   # optional — without it, raw numbers
                                       # still work, Sybil-adjustment is skipped
uvicorn mvp.app:app --reload --port 8000
```

```bash
curl "http://localhost:8000/lookup/1"
```

## Environment variables

| Variable | Required | Default |
|---|---|---|
| `BASESCAN_API_KEY` | No — Sybil-adjustment disabled without it | none |
| `BASE_RPC_URL` | No | `https://mainnet.base.org` |
| `ERC8004_REPUTATION_REGISTRY_ADDRESS` | No | Base mainnet's real deployed address |
| `X402_PAY_TO_ADDRESS` | No — enables the optional paid route below | none (route disabled) |
| `CDP_API_KEY_ID` / `CDP_API_KEY_SECRET` | Only if `X402_PAY_TO_ADDRESS` is set | none |
| `DATABASE_URL` | Yes, for `report_outcome` to work | none — `check_agent_trust` still works without it; outcome reporting fails loudly instead of silently writing to a local file (see `mvp/outcomes.py`) |

## Optional paid route (`/lookup-paid/{agentId}`)

Same lookup as the free route, priced at $0.001 in USDC on Base mainnet via
[x402](https://x402.org). This isn't a monetization play — it exists to
measure willingness-to-pay directly and because x402 Bazaar only indexes
services that have processed a real payment. The free `/lookup/{agentId}`
route stays the default everywhere (MCP, this README).

Disabled entirely unless `X402_PAY_TO_ADDRESS` is set. Requires the `paid`
extra:

```bash
pip install -r requirements.txt   # includes x402[fastapi] + cdp-sdk
export X402_PAY_TO_ADDRESS="0x..."       # your receiving address
export CDP_API_KEY_ID="..."              # CDP facilitator auth
export CDP_API_KEY_SECRET="..."
```

## Deploying

**Moved to the Atesta Hetzner box, 2026-10-02** — `docker-compose.yml` +
`Dockerfile` are the real, current deploy path, at
`vadium-lookup.atesta.io`. Moved off Render specifically because its
free-tier disk doesn't survive a redeploy, confirmed by testing it
directly: `report_outcome` writes need real persistent storage (see
`docs/mcp-server-spec.md`), and `DATABASE_URL` now points at a Postgres
instance on that same box rather than a local file. `render.yaml` is
kept for reference / as a fallback deploy target, not the live one —
don't assume it's current without checking `vadium-lookup.atesta.io`'s
DNS first.

## MCP server

Exposed two ways — pick based on whether the caller wants a local
subprocess or a network connection:

**Remote (streamable-http, what ChatGPT/Dots and most real clients
use)**: `https://vadium-lookup.atesta.io/mcp` — requires completing an
OAuth connection first (see below); point any MCP-over-HTTP client at
it, it'll be redirected through the connection flow automatically.

**Local (stdio, for Claude Desktop-style configs)**:

```bash
pip install git+https://github.com/vadium-dev/vadium-lookup.git
export BASESCAN_API_KEY="your-key"   # optional, same as above
vadium-lookup-mcp
```

(Not yet published to PyPI — `pip install vadium-lookup` will work once
it is, this is the real, currently-working install path. OAuth only
applies to the remote transport — stdio has no auth layer, since it's
already a local, already-trusted subprocess.)

Two tools, both gated behind the same OAuth connection on the remote
transport (`docs/oauth-trust-spec.md`) — a one-time per-connection step
for whoever's doing the connecting, not a per-call one:

- `check_agent_trust(agent_id)` — read-only, same raw-plus-Sybil-
  adjusted result as the HTTP endpoint, plus any self-reported outcomes
  (see below).
- `report_outcome(agent_id, outcome, evidence_ref)` — a write, requires
  `DATABASE_URL` to be set. Self-reported, explicitly never blended
  into the ERC-8004 numbers (see `docs/mcp-server-spec.md`'s
  "Anti-gaming" section for why), but as of the OAuth mechanism, each
  report is tied to whatever identity completed that connection's
  sign-in step — by default a verified Google email address, or
  (`VADIUM_AUTH_METHOD=wallet`) a wallet address proven by signature,
  not verified to own any on-chain identity either way (see
  `docs/oauth-trust-spec.md` for the full history of that decision) —
  just a stable identity across every report that connection makes,
  instead of none at all.
