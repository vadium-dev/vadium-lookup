# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Vadium Lookup — a free, unpriced ERC-8004 agent trust-record lookup,
exposed both as an HTTP endpoint and an MCP server (listed in the
ChatGPT Apps directory). Raw on-chain reputation plus a Sybil-adjusted
version (diversity-weighted by reviewer funding cluster), self-reported
outcomes from integrators (labeled unverified, never blended into the
on-chain numbers), and — as of 2026-10-03 — generalized beyond ERC-8004
to any "seller" ecosystem via a normalization layer. See `README.md` for
the full pitch and run instructions; this file is about working in the
code, not about the product itself.

## Part of the Vadium project — two sibling repos exist

This repo is one of three. **Read
`/Users/swathiselvaraju/code/arc/docs/vadium-project-map.md` before
assuming this repo's self-reported-outcomes mechanism is the whole
reputation story** — `report_outcome` turned out to have no natural
incentive for a ChatGPT integrator to ever use (see
`docs/chatgpt-apps-warm-start.md`), which is why a *separate* repo exists
to solve that differently rather than patching it here.

- `/Users/swathiselvaraju/code/arc` — Arc Treasury Guardian, the long-term
  architectural roadmap this repo's reputation-lookup idea is a fast,
  pragmatic wedge of (see `docs/phase3-design-decisions.md`'s ADR-009 for
  the specific connection to the other sibling below).
- `/Users/swathiselvaraju/code/vadium-attest` — capability/fulfillment
  testing research program, generating independently-verified trust
  signal for ChatGPT-ecosystem sellers where self-reporting doesn't work.

These are siblings, not isolated projects — read files there by absolute
path whenever cross-referencing helps.

## Running locally

```bash
pip install -r requirements.txt
export BASESCAN_API_KEY="your-key"   # optional — Sybil-adjustment skipped without it
uvicorn mvp.app:app --reload --port 8000
curl "http://localhost:8000/lookup/1"
```

Full environment variable reference (including `DATABASE_URL`, needed for
`report_outcome` to work, and the optional x402 paid route) is in
`README.md` — don't duplicate that table here, it's one README away.

Syntax-check after edits:

```bash
python3 -m py_compile mvp/*.py
```

No formal test suite beyond `tests/test_oauth_e2e.py`, a live
end-to-end test against a **real running deployment** (defaults to
production `vadium-lookup.atesta.io` unless `VADIUM_TEST_BASE_URL` is
set) — read its own docstring before running it; it leaves real OAuth
client/token rows behind (harmless, but not something to point at a
database you need pristine).

## Architecture

`mvp/app.py` is the FastAPI app — the free `/lookup/{agentId}` HTTP route,
plus `/privacy`, `/terms`, and `/.well-known/openai-apps-challenge`
(domain verification for the OpenAI Apps directory submission). An
optional paid `/lookup-paid/{agentId}` route (x402, Base mainnet) exists
to measure willingness-to-pay and for x402 Bazaar indexing — inert unless
`X402_PAY_TO_ADDRESS` is set, not the default path anywhere.

`mvp/mcp_server.py` mounts the MCP server (`streamable-http`, at `/mcp` —
also runnable as `stdio` via the `vadium-lookup-mcp` console script) onto
the same FastAPI app, behind OAuth (`mvp/oauth_provider.py` +
`mvp/oauth_store.py`, `docs/oauth-trust-spec.md`). Two tools:
`check_agent_trust` (read-only — this annotation is load-bearing, see
below) and `report_outcome` (a write).

**The lookup pipeline**, generalized 2026-10-03 from a bare ERC-8004
`agentId` to any `(ecosystem, external_id)` pair
(`docs/seller-normalization-spec.md`):

1. `mvp/ecosystems.py` validates/normalizes the `ecosystem` string at the
   tool boundary (case/separator-insensitive) — prevents silent
   fragmentation from inconsistent caller spelling.
2. `mvp/sellers.py` resolves `(ecosystem, external_id)` to a normalized
   `seller_identities` row, creating one plus a `sellers` row on first
   sighting. Cross-ecosystem identity linking (matching
   `primary_website`) is automatic-but-marked, never auto-merged — a
   human confirms via `confirm_link`/`reject_link`.
3. `mvp/seller_lookup.py` orchestrates the actual trust lookup per
   ecosystem: for `"erc8004"`, `mvp/erc8004_client.py` (read-only
   ReputationRegistry client) plus `mvp/diversity.py` (Sybil-adjustment —
   clusters reviewers by shared first-funder address via Etherscan V2,
   requires a **paid** plan for Base chain access, degrades honestly to
   raw-only if unavailable, never silently). Other ecosystems get that
   section marked not-applicable, not silently empty.
4. `mvp/outcomes.py` attaches any self-reported outcomes
   (`completed`/`disputed`/`no_response`), rolled up across every linked
   identity via `mvp/sellers.py`, always labeled unverified, never
   blended into the ERC-8004 numbers (`docs/mcp-server-spec.md`'s
   "Anti-gaming" section explains why).

`mvp/own_ledger.py` is a read-only consumer of the separately-anchored
Phase 2 evidence ledger (arc's `docs/phase2-architecture.md`) — kept
strictly read-only on purpose, this repo doesn't own that ledger's schema.

**`readOnlyHint` is a real constraint, not documentation**: OpenAI's
automated Apps-directory review actually calls tools and cross-checks for
side effects — `check_agent_trust` was once found to persist a lookup
row despite being declared read-only, and that logging had to be removed
entirely (not just hidden) rather than the annotation changed, since the
annotation was correct and the behavior wasn't. Any change to
`check_agent_trust` must stay a pure read.

Identity for `report_outcome`'s `verified_subject` comes from whichever
OAuth method is configured (`VADIUM_AUTH_METHOD` — `google` by default,
`wallet` available but dormant) via `mvp/google_oauth.py` or
`mvp/identity_proof.py` — see `docs/oauth-trust-spec.md` for why neither
is required to prove ownership of anything on-chain, just stability
across one connection's reports.

## Docs index

- `docs/oauth-trust-spec.md` — the OAuth trust mechanism in full.
- `docs/seller-normalization-spec.md` — the cross-ecosystem identity
  design (`sellers`/`seller_identities`/`seller_link_candidates`).
- `docs/mcp-server-spec.md` — tool contracts, anti-gaming rationale.
- `docs/hetzner-deployment.md` — live deploy specifics, backup setup, and
  known gotchas (e.g. the compose file's real path vs. a previously-
  documented wrong one — check this doc before redeploying).
- `docs/openai-submission.md` — the ChatGPT Apps directory submission:
  test cases, reviewer credentials, video walkthrough notes.
- `docs/chatgpt-apps-warm-start.md` — the problem statement that led to
  `vadium-attest` existing as a separate repo; this doc's own
  methodology content has moved there.
