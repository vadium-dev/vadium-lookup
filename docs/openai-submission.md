# OpenAI Apps directory submission — prep

Scoped 2026-10-03 against the real requirements
(`developers.openai.com/plugins/deploy/submission`), not assumed.

## What's done

- `/privacy` and `/terms` — real pages, added in `mvp/app.py` (were
  404 before; OpenAI requires working `privacyPolicyURL`/
  `termsOfServiceURL`).
- Logo check: `mvp/static/logo.svg`, 200×200 viewBox, square — well
  above OpenAI's 48×48 minimum. SVG vs. a raster format (PNG) for the
  actual upload isn't confirmed — check the dashboard's upload widget
  when we get there; easy to export a PNG from the same SVG if needed.
- Domain verification mechanism confirmed: a plain-text token hosted at
  `https://vadium-lookup.atesta.io/.well-known/openai-apps-challenge`.
  Route is wired in `app.py` reading from `OPENAI_APPS_CHALLENGE_TOKEN`
  env var — **not set yet**, since the real token only exists once the
  submission flow is started in OpenAI's dashboard (see "What you need
  to do" below).

## Metadata fields, drafted and ready to paste into the dashboard

| Field | Value |
|---|---|
| `name` | `vadium-lookup` |
| `version` | `0.4.0` |
| `description` | Free trust-record lookup for any seller an agent might hire or pay — an ERC-8004 on-chain agent today, with ChatGPT apps and other ecosystems supported by the same tools. Returns raw and Sybil-adjusted on-chain reputation plus self-reported outcomes from other integrators, clearly labeled and never blended together. |
| `author` | Atesta (name), no public email/URL beyond the site itself yet |
| `displayName` (≤30) | `Vadium Lookup` |
| `shortDescription` (≤30) | `Check an agent's trust record` |
| `longDescription` (≤4000) | Same as `description` above, expanded: explains the raw vs. Sybil-adjusted distinction, the self-reported `report_outcome` mechanism, and that it's free — see `docs/mcp-server-spec.md` for the full rationale if the reviewer wants more. |
| `developerName` (≤80) | `Atesta` |
| `category` | Closest fit from whatever the dashboard offers — likely "Productivity" or a "Finance/Crypto" category if one exists; the dashboard's own option list wasn't checked live (needs a logged-in session) |
| `logo` | `https://vadium-lookup.atesta.io/logo.svg` |
| `composerIcon` | Same asset, unless the dashboard wants a different crop/format |
| `websiteURL` | `https://vadium-lookup.atesta.io/` |
| `supportURL` | `https://vadium-lookup.atesta.io/` (no dedicated support page exists yet — using the root until/unless one is built) |
| `privacyPolicyURL` | `https://vadium-lookup.atesta.io/privacy` |
| `termsOfServiceURL` | `https://vadium-lookup.atesta.io/terms` |

## Status: filled into the real dashboard (2026-10-03)

All 5 positive test cases and the 3 negative test cases below are
**already entered and saved** in the live submission draft
(`platform.openai.com/plugins` → Vadium Lookup → Review information),
confirmed by actually driving the browser and taking it to "Progress
saved." Metadata (name, descriptions, developer, category, all four
URLs) was also confirmed to have come through correctly from
`plugin.json`/`mcp.json` — visible on the plugin's own detail page.

**Correction to the original draft below**: the real dashboard's
"Negative test cases" step has a different definition than first
assumed — not malformed/invalid input (wrong ecosystem, bad outcome
value), but prompts where the plugin **should not trigger at all**,
to test against false-positive invocation. The three actually entered:
1. "What's John Smith's reputation like as a freelance contractor?" —
   a person's reputation, unrelated to ERC-8004 agents.
2. "What's Bitcoin's reputation as a long-term investment?" — mentions
   crypto and "reputation" but names no specific agent/seller.
3. "Check this restaurant's Yelp rating before I make a reservation."
   — a rating lookup, but on an unrelated consumer platform.

The three "invalid input" scenarios originally drafted below (bad
ecosystem name, bad outcome value, no-prior-lookup correlation) are
still real and already verified live against the server (see the
8-case test run earlier) — just not what this particular dashboard
field wants. Worth keeping as internal QA cases regardless.

Still outstanding on the dashboard's "Supporting content" step: the
video walkthrough URL and release notes — not filled in, since
recording the video needs a human.

## Test cases (5 positive + 3 negative), drafted

**Positive 1 — basic trust check on a real ERC-8004 agent**
- Prompt: "Before I hire agent 95910, check its trust record."
- Expected tool: `check_agent_trust(ecosystem="erc8004", external_id="95910")`
- Expected result: raw + Sybil-adjusted ERC-8004 reputation numbers, `self_reported` section present (empty or populated), no error.

**Positive 2 — trust check with task context**
- Prompt: "I'm about to pay agent 42 for a same-day logistics job — check them first."
- Expected tool: `check_agent_trust(ecosystem="erc8004", external_id="42", task_description="same-day logistics job")`
- Expected result: same as above, `task_description` accepted without affecting the returned trust data.

**Positive 3 — reporting a successful outcome**
- Prompt: "That logistics job with agent 42 went fine — mark it completed."
- Expected tool: `report_outcome(ecosystem="erc8004", external_id="42", outcome="completed")`
- Expected result: `stored: true`, confirmation the outcome was recorded.

**Positive 4 — reporting a dispute with detail**
- Prompt: "Agent 42 never delivered — file that as disputed, they went silent after taking payment."
- Expected tool: `report_outcome(ecosystem="erc8004", external_id="42", outcome="disputed", detail="went silent after taking payment")`
- Expected result: `stored: true`, `detail` text echoed back in the response.

**Positive 5 — checking an agent with a prior disputed report**
- Prompt: "Check agent 42's trust record again."
- Expected tool: `check_agent_trust(ecosystem="erc8004", external_id="42")`
- Expected result: `self_reported.recent_issues` includes the disputed report from test case 4, with its detail text — demonstrating the self-reported data round-trips and surfaces.

**Negative 1 — invalid ecosystem**
- Prompt: "Check the trust record for seller 123 on ecosystem 'fakechain'."
- Expected behavior: tool call fails cleanly with an `UnknownEcosystem` error naming the known ecosystems — not a crash, not a fabricated result.

**Negative 2 — invalid outcome value**
- Prompt: "Report agent 42's outcome as 'amazing'."
- Expected behavior: rejected with a clear `ValueError` naming the three valid outcome values (`completed`, `disputed`, `no_response`) — the model should not retry with a made-up value that happens to be accepted.

**Negative 3 — reporting without a prior check**
- Prompt: "Report outcome 'completed' for agent 999999 that I've never looked up."
- Expected behavior: succeeds (no hard requirement to call check_agent_trust first), but `preceded_by_lookup` in the response is `null` — this confirms the model doesn't fabricate a false correlation when none exists.

## Video walkthrough

Not something I can record — needs a real screen capture. Suggested
script, covering the 5 positive cases above in order: (1) a plain trust
check, (2) one with task context, (3) reporting success, (4) reporting
a dispute with detail, (5) re-checking the agent and showing the
dispute surfaced in `recent_issues`. Under 2–3 minutes total should
cover it.

## What you need to do (can't be done from here)

1. **Start the submission in OpenAI's dashboard** (requires your own
   OpenAI developer account login) — this is what actually generates
   the real domain-verification token.
2. **Send me that token** (not a secret in the credential sense, but
   follow the usual `.env.local` pattern anyway) so I can set
   `OPENAI_APPS_CHALLENGE_TOKEN` on the server and confirm
   `/.well-known/openai-apps-challenge` serves it correctly before you
   click "verify" in the dashboard.
3. **Record the video walkthrough** per the script above.
4. **Pick the actual `category`** from whatever options the dashboard
   offers — I couldn't see that list without a logged-in session.
5. **A quick look at `/privacy` and `/terms`** before submitting — I
   drafted them to accurately describe what the service actually does
   today (OAuth identity, task/outcome data collected, stored on our
   own server, never sold), but they're not reviewed by anyone but me.
