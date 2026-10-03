# Seller identity normalization across ecosystems

Scoped 2026-10-03, triggered by a simple but important correction: the
ChatGPT "Dots" ecosystem's 4,000+ connected apps (Gmail, Notion,
Zendesk integrations, the long tail of small third-party apps found by
searching the live directory) are almost entirely **not** ERC-8004
on-chain agents, and never will be — there's no reason a mainstream
SaaS product would register an on-chain identity on Base. `vadium-lookup`
as originally built only understands ERC-8004 `agentId`s, which gives it
essentially zero coverage of what Dots actually chooses between in real
usage. Decision: generalize "who is being scored" into a cross-ecosystem
concept — a **seller** — rather than treat ERC-8004 agents and
ChatGPT-connected apps as two unrelated products.

## What a "seller" has to represent

Confirmed directly (browsing the live ChatGPT plugins directory, not
just reading docs — `chatgpt.com/plugins`) that at least four
ecosystems with incompatible native identity shapes already matter:

| Ecosystem | Native ID shape | Example |
|---|---|---|
| ERC-8004 (Base/Arc) | numeric token ID, scoped to a registry contract + chain | `agentId=95910` on `0x8004...` |
| ChatGPT Apps | opaque OpenAI-assigned string, visible in the app's own URL | `plugins_6aac006819508191a02506fb32a88714` |
| HubSpot / Zendesk Marketplace | platform-specific app ID | numeric app IDs, platform-scoped |
| Muse (Meta) | none observed — closed, Meta-brokered partnerships only | n/a |

No shared ID format exists, and the next ecosystem's shape is
unknowable in advance — the schema has to absorb that rather than
assume it.

## Schema: `mvp/sellers.py`

Two tables, not one wide one:

- **`sellers`** — our own normalized, ecosystem-agnostic record:
  `id`, `canonical_name`, `primary_website`, `created_at`. This is what
  reputation should actually roll up under.
- **`seller_identities`** — one row per *sighting* of a seller in a
  specific ecosystem: `seller_id` (FK), `ecosystem` (free text, not an
  enum — a new ecosystem is a data row, never a migration),
  `external_id` (always stored as text regardless of native shape),
  `external_url`, `metadata` (JSONB — everything ecosystem-specific:
  `{chain, registry_address}` for ERC-8004, `{developer, category,
  skills, version}` for ChatGPT — absorbed without forcing a sparse
  shared table). `UNIQUE (ecosystem, external_id)`.

The same real business can have an ERC-8004 agent **and** a ChatGPT
plugin **and** a HubSpot app — three `seller_identities` rows, one
`sellers` row, once we're confident they're the same. Until then, each
sighting gets its own fresh `sellers` row — `resolve_or_create_identity`
never guesses a link on creation.

## Linking: automatic detection, manual confirmation

Per explicit decision: matching is **automatic, but marks rather than
merges**. On every new identity, `_detect_link_candidates` checks for
any other `sellers` row sharing the same normalized `primary_website`
(bare-domain comparison — `https://Notion.so/` and `notion.so` are
recognized as the same key) and, if found, inserts a
`seller_link_candidates` row (`status='pending'`) — **not** a merge.
Website is currently the only matching signal; deliberately simple
rather than adding fuzzier heuristics (name similarity, etc.) before
there's real data showing website-matching alone is insufficient.

A human reviews pending candidates periodically
(`list_pending_link_candidates`) and calls either:
- `confirm_link(candidate_id)` — re-points every `seller_identities`
  row from the newer seller onto the older ("surviving") one. The
  losing `sellers` row is left in place (not deleted) as a valid
  historical FK target, just no longer the current home for any
  identity.
- `reject_link(candidate_id)` — marks it rejected, permanently (no
  re-flagging the same pair on a later re-sighting).

Tested directly end-to-end before shipping (website match creates
exactly one candidate, no false positive for an unrelated domain,
idempotent on re-resolution, confirm actually merges, reject actually
doesn't, a rejected pair never re-flags) — not assumed correct from
reading the code.

## Why `seller_identity_id`, not `seller_id`, belongs on outcome data

The critical design choice for making merges cheap: once
`trust_lookups`/`reported_outcomes` reference a seller, they should
store the **immutable** `seller_identity_id` (which ecosystem sighting
this particular call used), not the **mutable** `seller_id` (which
sellers row it currently rolls up under). `rolled_up_seller_id()`
resolves identity → current seller at *query* time via a join, and
`sibling_identity_ids()` returns every identity currently under a
seller. This means a `confirm_link` merge immediately and correctly
changes what every *historical* lookup/outcome rolls up under — via the
join, not by rewriting any historical row — with zero data migration
needed at merge time.

## What's explicitly not done yet

- **`check_agent_trust`/`report_outcome` still take a bare ERC-8004
  `agent_id: int`**, not `(ecosystem, external_id)`. This module is the
  normalization foundation, built and tested in isolation; rewiring the
  live tool signatures (and making `lookup()`'s ERC-8004-specific
  on-chain query conditional on ecosystem) is a breaking change to
  already-shipped, tested tools and is being treated as its own
  deliberate next step, not bundled into this one.
- No review UI — `list_pending_link_candidates` /
  `confirm_link` / `reject_link` are plain functions, callable from a
  script. Worth a real interface once there's enough real candidate
  volume to make one worth building.
- No backfill of existing ERC-8004 `agent_id` data in
  `trust_lookups`/`reported_outcomes` into this new structure — that's
  part of the signature-rewiring step above, not done here.
