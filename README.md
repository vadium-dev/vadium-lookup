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

## Deploying

See `render.yaml` — Render's free tier needs no credit card. Set
`BASESCAN_API_KEY` in the service's Environment tab after the first
deploy.
