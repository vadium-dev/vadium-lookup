# Warm-starting trust ratings for ChatGPT-ecosystem apps

> **Status:** the problem statement below is still current, but the
> methodology, literature review, and implementation moved to their own
> repo — [`vadium-attest`](../../vadium-attest) — once it became clear this
> was a research program in its own right, not a module of this service.
> This doc stays as the origin/problem-statement reference `vadium-attest`
> links back to; the design decisions and working code now live there.

## Why this exists

`check_agent_trust` is genuinely useless for a ChatGPT user evaluating a
ChatGPT app: there's no ERC-8004-style on-chain reputation for that
ecosystem, and self-reported outcomes via `report_outcome` depend on an
incentive that doesn't exist — nobody naturally bothers reporting how a
Notion or Linear connector performed, and ChatGPT itself never records it
automatically. Waiting for that data to accumulate organically means
waiting forever.

This doc proposes generating the rating ourselves instead: a staged,
adaptive capability test battery run directly against an app's own MCP
server, independent of anyone's cooperation or incentive — the same
pattern ADR-009 (`docs/phase3-design-decisions.md`) already uses to let a
new arbiter build trust via calibration cases before real dispute volume
exists, pointed at sellers instead of jurors.

Feasibility was checked directly, not assumed: Notion, Linear, Asana, and
Atlassian are all real, currently-listed ChatGPT apps, and all four expose
an independently reachable, OAuth-protected MCP endpoint at a
company-controlled URL (`mcp.notion.com/mcp`, `mcp.linear.app/mcp`, etc.)
— the same pattern this project's own `mvp/mcp_server.py` uses. A plain
HTTP client can talk to these directly, at normal API latency, with no
browser or UI automation involved.

## Stages

**Stage 0 — Discovery (prerequisite, not scored).** Connect, complete
`initialize`, call `tools/list`. This is not optional scaffolding — its
output determines which later-stage tests actually run for *this* app,
rather than assuming a fixed tool set exists everywhere. It also produces
one scored signal on its own: **annotation completeness** — the fraction
of tools that declare both `readOnlyHint` and `destructiveHint`. We have
first-hand expertise measuring exactly this, having just been caught and
corrected on it ourselves by OpenAI's own review. An app that doesn't
declare its destructive operations correctly can surprise a real user with
an action they didn't knowingly confirm — a concrete, objective,
independently-checkable safety signal no self-report could ever produce.

**Stage 1 — Per-tool smoke tests.** For each discovered read-only tool,
the simplest valid call, generated from its declared input schema.
Measures per-tool success rate and p50/p95 latency. Cheap, mechanical,
catches outright breakage.

**Stage 2 — Realistic round-trip scenarios (the part that actually
matters).** Not "did the API respond" but "did the task a real user would
attempt actually work, verified independently" — create something, read
it back, assert the content matches exactly what was sent, then clean up.
This is the same "verify independently, don't trust the self-report"
principle `mvp/outcomes.py` and `mvp/diversity.py` already apply
elsewhere in this project, pointed at a different kind of claim.

**Stage 3 — Adaptive deep-dive, conditional.** Only runs where Stage 2
was ambiguous (e.g., a partial content mismatch). If a create→fetch
round-trip shows formatting loss, Stage 3 characterizes exactly which
constructs survive and which don't — a far more useful finding for a user
("this app flattens nested bullet lists on create") than a blunt
pass/fail. This is the actual "adaptive" part of "staged, adaptive test":
depth is spent where Stage 2 found something worth investigating, not
applied uniformly everywhere.

**Stage 4 — Cleanup verification.** Re-fetch anything created in Stage
2/3 by ID and confirm it's actually gone — never just trust the delete
call's own return value.

## Eval mechanism

Three tiers, cheapest and most certain first:

1. **Deterministic assertion** — exact field/content match, status code,
   schema validation. Used everywhere a test has one correct expected
   value (nearly all of Stage 1/2). No judgment call, no added cost.
2. **Schema/contract validation** — validate every response against its
   Stage-0-declared schema even when not asserting a specific value;
   catches silent drift between documented and actual behavior.
3. **LLM-as-judge** — reserved for genuinely open-ended outputs with no
   single correct string (e.g., judging whether an error message is
   actually clear to a human, or whether a natural-language search answer
   addresses the question asked). Cheap per call; needs a written rubric
   per scenario, tuned before it's trusted — this is the one tier that
   needs iteration, not a one-shot build.

Every test case resolves to `PASS` / `PARTIAL` / `FAIL` / `SKIP`, plus
latency and raw request/response evidence. Per-app rollup reports category
scores side by side — never one blended number — matching how
`diversity.py` keeps raw vs. adjusted and `outcomes.py` keeps self-reported
vs. nothing separate and labeled rather than merged.

## What's actually useful to a user (not to us)

Reframed from "does the API work" to "would I trust this inside a real
ChatGPT conversation":

1. **Task reliability** — independently-verified round-trip success, not
   a self-report.
2. **Data integrity under real use** — does content/formatting/fields
   survive create→read intact.
3. **Safety behavior** — are destructive actions correctly gated and
   declared; is an out-of-scope or unauthorized action refused rather than
   silently attempted.
4. **Error transparency** — does a failure explain what went wrong, or
   fail silently? This is the exact "it said it worked but nothing
   happened" trust gap this whole project exists to close.
5. **Responsiveness** — p50/p95 latency; a slow tool breaks conversational
   flow regardless of correctness.
6. **Scope-match** — does the tool set actually cover what the app's own
   ChatGPT directory listing promises, or is it a thinner slice.
7. **Documentation/contract transparency** — already demonstrated without
   running a single test: Notion publishes exact tool names, parameters,
   and rate limits (`developers.notion.com/guides/mcp/mcp-supported-tools`);
   Linear documents none of it. That asymmetry is itself a real,
   objective, zero-cost signal about how seriously each vendor treats
   their MCP integration as a stable contract.

## Implementation

See [`vadium-attest`](../../vadium-attest) — methodology, literature
review, task specs, and harness now live there, with the "milestones,"
"accuracy," and "completion" vocabulary refined beyond the Stage 0–4 sketch
originally drafted in this doc. That repo's `README.md` is the current
entry point.
