# OAuth trust mechanism for `report_outcome`

Scoped 2026-10-02, per the explicit decision to put real identity behind
self-reported outcomes ("I would slap a proper oauth trust mechanism").
`check_agent_trust` (the read) stays exactly as open as it is today —
nothing here touches it. This only gates the write.

## Why this shape, not the obvious one

The obvious design is "turn on OAuth for the MCP server." Checked
against the real installed SDK (`mcp==2.2.0`, not assumed) before
building anything: `mcp.server.auth.middleware.bearer_auth.RequireAuthMiddleware`
wraps the **entire** mounted Starlette app for one `MCPServer` instance —
there is no per-tool auth hook in the transport layer. Turning on
`AuthSettings` on the existing single server would force every caller,
including a client just running the free, read-only `check_agent_trust`,
through a full OAuth/PKCE dance. That directly breaks the "readOnlyHint
lets a host auto-invoke it with no human approval" property the read
path depends on (see `docs/mcp-server-spec.md`).

Flagged this tradeoff directly and asked: keep the split (free read,
gated write) or accept that `check_agent_trust` also sits behind the
OAuth connection. Decision: **gate both tools on the same `/mcp`
endpoint** — one `MCPServer` instance, `AuthSettings` applied to the
whole mount. In practice this is a one-time per-connection cost, not a
per-call one: a Dots/ChatGPT user completes the OAuth connection once
(like connecting any other app), and every tool call after that —
`check_agent_trust` included — rides the same bearer token with no
further friction. The two-endpoint split documented in an earlier draft
of this spec was not built.

## Identity mechanism, second revision: Google Sign-In (2026-10-02)

After the wallet-signature design below shipped and was fully tested,
a follow-up question exposed a real adoption blocker in it: completing
`personal_sign` requires a browser wallet *extension already installed*
(MetaMask or similar) — not just "any browser." Most of Dots' actual
audience (people paying for ChatGPT Pro to hire agents, not necessarily
crypto-native people) won't have one. Confirmed this wasn't a minor
friction point but a hard requirement before deciding what to do about
it.

Decision: switch the **active default** identity-proofing step to
**Google Sign-In** (`mvp/google_oauth.py`) — a real third-party OIDC
provider, which is exactly the pattern
`OAuthAuthorizationServerProvider.authorize()`'s own docstring
describes (redirect to a third party, exchange on the way back), rather
than the custom page the wallet flow uses. Google verifies the email
for us; for a caller already signed into Google in their browser, this
can be a single click — lower friction than the wallet flow, not just
less exclusionary. `subject` on the issued token becomes the verified
email address instead of a wallet address.

**The wallet-signature flow was not removed.** Per explicit instruction,
it stays fully implemented and tested (`mvp/identity_proof.py`, the
`/oauth/verify-identity` page) but dormant — selected only by setting
`VADIUM_AUTH_METHOD=wallet` (default: `google`). Both paths write to the
exact same `subject` column and the exact same downstream code; nothing
about `report_outcome` or `check_agent_trust` needs to know which one
produced a given token.

**Live deployment note**: Google OAuth requires a real client ID/secret
from a Google Cloud project (Pranava has an existing one this will be
added to — not yet done as of this writing). Until that exists, the
Hetzner deployment is pinned to `VADIUM_AUTH_METHOD=wallet` via
`docker-compose.yml`'s own default, specifically so a missing Google
credential doesn't silently break every new connection attempt. Flip it
once the credentials are in place.

A misconfigured Google setup fails as a clean OAuth `temporarily_unavailable`
error at `/authorize` (`GoogleOAuthNotConfigured`, caught and converted
in `VadiumOAuthProvider.authorize()`), not an unhandled 500 — this was
deliberately exercised before shipping, not assumed to be fine.

## What the wallet-signature step actually proves (corrected 2026-10-02, now the non-default path)

The original version of this spec required the signing wallet to
already own an ERC-8004 agent identity (`IdentityRegistry.balanceOf(address)
> 0`). **Dropped before implementation** — caught by asking a basic
feasibility question: who actually completes the `/authorize` step?

OAuth's authorization-code flow puts a browser in front of a *human*,
not the calling agent — for a Dots/ChatGPT connection this is the one-
time "connect this app" step the end user clicks through, same as
connecting Gmail. That end user is someone *hiring* an agent through
Dots, not an agent *operator*. ERC-8004 identities are registered by
agent builders/operators; requiring report_outcome's caller to already
own one would exclude almost the entire actual audience for this tool.
Gating on-chain ownership here doesn't strengthen the identity check —
it just makes the tool unusable for the people it's for.

So the mechanism that ships: at `/authorize`, instead of redirecting
to a real third-party IdP (the SDK's documented pattern for this hook),
we serve our **own** HTML page asking the caller to sign a short
challenge message with a wallet, via `personal_sign` (no wallet-connect
library needed — any injected `window.ethereum` provider supports this
directly, and it's a pure off-chain signature: zero gas, no
transaction). We recover the signing address (`eth_account`, already a
transitive dependency via `web3==8.0.0` — confirmed importable, no new
dependency) and that address becomes the OAuth token's `subject`,
**with no on-chain ownership check**.

What this does and doesn't buy: it's weaker than true ERC-8004 proof —
anyone can generate a fresh wallet for free, so it isn't a hard Sybil
wall. What it does provide: a *stable, user-chosen* identity across every
call that OAuth connection ever makes, instead of nothing at all (no
subject previously existed for self-reports), and it stops a report's
authenticity from resting on an easily-rotated API key or anonymous
session. It intentionally leans on the same account-level reasoning
already settled earlier ("it's the user who might have maligned
interest, not every dot") plus the platform-level backstop that a Dots
connection in the first place requires a paid ChatGPT account — the
wallet signature adds a persistent identity on top of that, it doesn't
replace it.

## Data model (new Postgres tables, same `vadium_lookup` database)

```sql
CREATE TABLE oauth_clients (
    client_id TEXT PRIMARY KEY,
    client_secret TEXT,              -- NULL for public clients (PKCE-only)
    redirect_uris TEXT[] NOT NULL,
    client_name TEXT,
    registered_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE oauth_pending_authorizations (
    request_id TEXT PRIMARY KEY,     -- shown to the user, not the auth code
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
    redirect_uri TEXT NOT NULL,
    redirect_uri_provided_explicitly BOOLEAN NOT NULL,
    scopes TEXT[] NOT NULL,
    state TEXT,
    code_challenge TEXT NOT NULL,
    resource TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL  -- short-lived; the signing page has ~10 min
);

CREATE TABLE oauth_authorization_codes (
    code TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
    redirect_uri TEXT NOT NULL,
    redirect_uri_provided_explicitly BOOLEAN NOT NULL,
    scopes TEXT[] NOT NULL,
    code_challenge TEXT NOT NULL,
    resource TEXT,
    subject TEXT NOT NULL,           -- the verified wallet address
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE oauth_access_tokens (
    token TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
    scopes TEXT[] NOT NULL,
    resource TEXT,
    subject TEXT NOT NULL,
    expires_at TIMESTAMPTZ
);

CREATE TABLE oauth_refresh_tokens (
    token TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
    scopes TEXT[] NOT NULL,
    resource TEXT,
    subject TEXT NOT NULL,
    expires_at TIMESTAMPTZ
);
```

`reported_outcomes` (existing, `mvp/outcomes.py`) gets one new nullable
column: `verified_subject TEXT` — the wallet address from the token
that made the call. Nullable so existing rows (recorded before this
shipped) don't need backfilling or a fake value.

## Request flow

```
Dots/ChatGPT                  vadium-lookup                    caller's browser
     |                              |                                 |
     |-- POST /mcp/secure -------->|  (no token)                      |
     |<---------- 401 + WWW-Authenticate: Bearer realm=... -----------|
     |-- GET .well-known/oauth-authorization-server ----------------->|
     |-- POST /register (DCR) ----->|  stores oauth_clients row        |
     |-- redirect user to /authorize?client_id=...&code_challenge=... |
     |                              |-- serves sign-challenge page -->|
     |                              |                      (user signs with wallet)
     |                              |<-- POST verify {address,sig} ---|
     |                              |  recovers signer (no on-chain    |
     |                              |  ownership check — see above)    |
     |                              |  stores oauth_authorization_codes
     |                              |-- redirect user to redirect_uri?code=...&state=... -->|
     |<-- POST /token (code + verifier) --|                                 |
     |<---------- access_token ------------|                                 |
     |-- POST /mcp, Authorization: Bearer <token> ---------------------------->|
     |<---------- tool result (check_agent_trust or report_outcome) ----------|
```

## What this does not do

- Does not keep `check_agent_trust` friction-free per connection — see
  the decision above. It does keep it friction-free per *call*: one
  OAuth connection, then every subsequent call rides the same token.
- Does not require the caller's wallet to own an ERC-8004 identity —
  see the corrected design above; this was the original plan and was
  dropped as infeasible for Dots' actual (non-operator) user base.
- Does not verify the specific `agent_id` passed to `report_outcome`
  against the caller's own identity — the subject only has to own *an*
  ERC-8004 identity, not the one being reported on. Reporting is about
  an integrator's experience with an agent they hired, not self-review.
- Does not yet implement rate-limiting or banning by `verified_subject`
  — this spec makes that possible (the column exists, the identity is
  stable across calls) but building the policy itself is a follow-up,
  not scoped here.
- Does not change the fact that `self_reported` outcomes are still
  never blended into the ERC-8004 numbers (`mvp/outcomes.py`,
  `mvp/lookup.py`) — identity-gating who can write a report is orthogonal
  to whether self-reports count as independently-verified reputation.
