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

See `render.yaml` — Render's free tier needs no credit card. Set
`BASESCAN_API_KEY` in the service's Environment tab after the first
deploy.

## MCP server

Same lookup, exposed as an MCP tool for agents/coding assistants that
discover tools this way rather than by calling an HTTP endpoint
directly:

```bash
pip install git+https://github.com/vadium-dev/vadium-lookup.git
export BASESCAN_API_KEY="your-key"   # optional, same as above
vadium-lookup-mcp
```

(Not yet published to PyPI — `pip install vadium-lookup` will work once
it is, this is the real, currently-working install path.)

Exposes one tool, `check_agent_trust(agent_id)`, returning the same
raw-plus-Sybil-adjusted result as the HTTP endpoint.
