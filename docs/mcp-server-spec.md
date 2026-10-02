# Vadium MCP server — spec

Status: `check_agent_trust` exists today, stdio-only. This spec covers
making it remotely reachable and adding one new tool. Written 2026-10-02
after verifying the installed `mcp` SDK's real API directly (not assumed)
and confirming ChatGPT's actual tool-approval model via its own docs —
see citations inline.

## Why this now

`docs/IDEA.md` §6.2 already established that agent/skill discovery runs
on registries and install counts, not brand — PayCrow is the cited proof
(one GitHub star, real usage, because it shipped as an MCP server an
agent could find and wire in with zero human sales cycle). OpenAI's
"Dots" (launched 2026-09-29) sharpens this further: an always-on agent
connected to 4,000+ apps, doing proactive research and purchasing on a
user's behalf. If Vadium's lookup is reachable as an MCP tool, a Dot
checking an unfamiliar counterparty before transacting can call it
directly — no integration code written by anyone, the same mechanism
already working for PayCrow, on a much larger distribution surface.

## Confirmed: read tools run automatically, write tools don't

Verified against OpenAI's own docs (`help.openai.com/en/articles/11487775`,
`learn.chatgpt.com/docs/agent-approvals-security`), not assumed:

- Default permission level is **"Important actions"** — reads happen
  automatically; actions with a meaningful effect outside ChatGPT,
  that expose sensitive info, or are hard to undo require confirmation
  ("Allow once" / "Always allow").
- This is driven by the MCP tool's own `readOnlyHint` annotation. A tool
  without it is treated as a write and gated behind confirmation
  regardless of what it actually does internally.

**Consequence for this spec**: `check_agent_trust` must be annotated
`readOnlyHint=True` to get automatic invocation. `report_outcome` must
*not* be, since it's a write and should require confirmation — that's
correct behavior, not a limitation to route around.

## Tool 1 — `check_agent_trust` (exists, needs transport only)

No change to the tool itself. `mvp/mcp_server.py`'s existing
implementation is correct: wraps `mvp.lookup.lookup()`, same Sybil-
adjusted/raw split as the HTTP endpoint, same "no reputation data" vs.
fabricated-neutral-score honesty already required by
`docs/phase3-backlog.md` B1.

**Change needed**: add the `readOnlyHint` annotation explicitly (don't
rely on inference), and expose it over `streamable-http` transport
mounted on the existing FastAPI app, not only `stdio`.

## Tool 2 — `report_outcome` (new)

```python
@mcp.tool()
def report_outcome(agent_id: int, outcome: str, evidence_ref: str | None = None) -> dict:
    """Report a completed transaction's outcome for an ERC-8004 agent,
    after the fact — for an integrator who already called
    check_agent_trust before hiring/paying this agent, to feed back what
    actually happened.

    This is self-reported by the calling integrator, not independently
    verified at call time — see "Anti-gaming" below for how it's weighted.

    Args:
        agent_id: The ERC-8004 agentId this outcome is about.
        outcome: One of "completed", "disputed", "no_response".
        evidence_ref: Optional URI/hash pointing at supporting evidence
            (a transcript, a delivered-artifact hash) — same
            evidence-URI pattern ERC-8004's giveFeedback() already uses.
    """
```

**Not read-only** — omit `readOnlyHint` so ChatGPT and any other MCP
host gates it behind confirmation, consistent with it being a real write
to the evidence ledger, not a lookup.

**Anti-gaming, corrected after checking what's actually buildable today**:
the spec originally said this would reuse `diversity.py`'s funder-
clustering approach, the same as ERC-8004 reviewer feedback. That's
wrong and worth stating plainly rather than quietly fixing — funder-
clustering needs a wallet address to trace back to a first funder; an
MCP tool caller has no on-chain identity at all, so there's nothing to
cluster. Applying ADR-004's *principle* ("never a naive count") correctly
here means something simpler and more honest: self-reported outcomes
are stored and shown in a **separate, clearly-labeled field**, and never
blended into `check_agent_trust`'s raw/Sybil-adjusted ERC-8004 numbers.
No weighting formula is applied because none is honestly available yet
— the safeguard is strict separation, not a half-built weighting scheme
dressed up as more rigorous than it is. Revisit once there's a real
caller-identity signal (e.g. a registered, ERC-8004-identified
integrator) worth weighting by.

**Not implementing yet**: automatic verification of a reported outcome
against independent evidence (e.g. checking the referenced transcript
actually supports the claim). That's real work with no backend to wrap
today — logged as a self-reported, diversity-weighted, clearly-labeled
signal for now, upgraded later if real usage shows it's worth it.

**Update, 2026-10-02 — caller identity exists now, and both tools got
richer (see `docs/oauth-trust-spec.md` and `mvp/trust_lookups.py`)**:
the "no on-chain identity at all" line above is no longer quite true —
OAuth now ties every call to a stable `verified_subject` (a Google
email by default, or a wallet address in the dormant opt-in wallet
mode). That still isn't the ERC-8004-identified integrator this section
imagined, so the anti-gaming stance above is unchanged: self-reports
stay separate, unweighted, never blended into the ERC-8004 numbers.
What it does enable: `check_agent_trust` now accepts optional `task_id`
/ `task_description`, and is persisted for the first time (previously a
pure stateless read with no record at all); `report_outcome` gained the
same two fields plus `detail` (free-text explanation, distinct from
`evidence_ref`) and returns `preceded_by_lookup` — the specific prior
`check_agent_trust` call it correlates to, matched by `task_id` when the
caller supplies and reuses one, or heuristically by
(`verified_subject`, `agent_id`, most-recent-prior-timestamp) otherwise.
`summarize()`'s output grew a `recent_issues` list — the last few
non-"completed" reports' actual detail text, not just a bare count,
since "2 disputed" on its own says nothing about what went wrong.

## Hosting

Mount on the existing `vadium-lookup` FastAPI app (`mvp/app.py`), not a
new service:

```python
from mvp.mcp_server import mcp
app.mount("/mcp", mcp.streamable_http_app())
```

Verified directly against the installed `mcp` SDK (v2.x, confirmed via
a scratch venv install, not assumed from memory): `MCPServer` exposes
`streamable_http_app()`, returning a mountable ASGI app — this is the
real, current API, not the `FastMCP`-era v1 interface mcp_server.py's
own class name briefly suggested before checking.

Result: reachable at `https://vadium-lookup.onrender.com/mcp`, same
Render deployment, same domain, zero new infrastructure. The `stdio`
entry point (`vadium-lookup-mcp` console script) stays for local/Claude
Desktop use — this adds a transport, it doesn't replace the existing one.

**Follow-up once live**: fill in the `.well-known/agent-registration.json`
`services` list with the real MCP endpoint — currently and correctly
omitted per that file's own comment, since no network URL existed to
list honestly until now.

## Discovery

Submit to OpenAI's Apps/connector directory once the endpoint is live —
same motion as the x402 Bazaar and Circle Marketplace listings already
done, a new channel added to an existing list, not new kind of work.

## Explicitly out of scope for this spec

- `buy_hedge_coverage`, `file_dispute`, `register_agent` tools — no live
  backend to wrap; spec'd conceptually in `docs/IDEA.md` §1.1/§1.2, not
  here.
- Automatic verification of `report_outcome` claims against independent
  evidence.
- A separate hosted service — deliberately reusing existing
  infrastructure per this project's own stated philosophy (Phase 2:
  "don't build a new observability SDK... ride existing adoption").
