"""MCP server wrapper around the same free lookup mvp/app.py exposes
over HTTP — this is the actual discovery channel with real precedent
(docs/IDEA.md §6.2: agent/skill discovery runs on registries and
install counts, not brand; PayCrow got real usage off one GitHub star
specifically because it shipped this way). No payment anywhere here,
same as the HTTP endpoint.

Two transports: `stdio` (the original, for local/Claude-Desktop-style
clients, run via the `vadium-lookup-mcp` console script) and
`streamable-http` (mounted onto mvp/app.py's existing FastAPI app at
/mcp — see docs/mcp-server-spec.md for why this is a mount, not a
separate service). Both serve the same `mcp` instance and the same
tools; nothing about the tools themselves differs by transport.
"""

import os

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer

from mvp import ecosystems, outcomes, seller_lookup
from mvp.oauth_provider import VadiumOAuthProvider

# Both tools sit behind the same OAuth connection (docs/oauth-trust-spec.md)
# — the SDK's bearer-auth middleware wraps the whole mounted transport,
# not individual tools, so there's no way to gate report_outcome alone
# without a second server/mount. That's a one-time per-connection cost
# for a caller, not a per-call one: once a host like ChatGPT completes
# the OAuth dance for this server, every tool call after that — including
# check_agent_trust — rides the same bearer token with no further friction.
#
# issuer_url is the bare domain, deliberately WITHOUT a /mcp path —
# confirmed directly against a live deployment, not assumed: /authorize,
# /token, /register, /revoke are registered at fixed constant paths
# (mcp.server.auth.routes.AUTHORIZATION_PATH etc., e.g. plain "/authorize")
# that do NOT incorporate issuer_url's own path, even though the
# advertised metadata document (built from issuer_url) does — so an
# issuer_url of ".../mcp" makes the metadata document advertise
# ".../mcp/authorize", a route that 404s, while the real route sits at
# the bare ".../authorize". resource_server_url is the opposite case: a
# SEPARATE, genuinely path-aware mechanism (RFC 9728 protected-resource
# metadata) that correctly incorporates its own path, so it keeps "/mcp".
_ISSUER_URL = os.environ.get("VADIUM_ISSUER_URL", "https://vadium-lookup.atesta.io")
_RESOURCE_SERVER_URL = os.environ.get("VADIUM_RESOURCE_SERVER_URL", "https://vadium-lookup.atesta.io/mcp")
_VERIFY_PAGE_URL = os.environ.get(
    "VADIUM_VERIFY_PAGE_URL", "https://vadium-lookup.atesta.io/oauth/verify-identity"
)

oauth_provider = VadiumOAuthProvider(verify_page_url=_VERIFY_PAGE_URL)

mcp = MCPServer(
    name="vadium-lookup",
    description=(
        "Free trust-record lookup for any seller an agent might hire or "
        "pay — an ERC-8004 on-chain agent, a ChatGPT-connected app, or "
        "any other supported ecosystem (mvp/ecosystems.py). ERC-8004 "
        "sellers additionally get ERC-8004's own public reputation "
        "feedback, shown both raw and Sybil-adjusted (diversity-weighted "
        "by funding cluster). No payment required."
    ),
    version="0.4.0",
    auth_server_provider=oauth_provider,
    auth=AuthSettings(
        issuer_url=_ISSUER_URL,
        resource_server_url=_RESOURCE_SERVER_URL,
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=["vadium"], default_scopes=["vadium"]
        ),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=["vadium"],
        # Explicit rather than relying on the deprecated implicit default:
        # we don't want to reject a client that omits the RFC 8707
        # `resource` indicator on its authorize/token requests, and this
        # provider doesn't do separate audience validation of its own.
        validate_token_resource=False,
    ),
)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True})
def check_agent_trust(ecosystem: str, external_id: str) -> dict:
    """Look up a seller's trust record before hiring or paying it — an
    ERC-8004 on-chain agent, a ChatGPT-connected app, or any other
    supported ecosystem (see `ecosystem` below).

    For ecosystem="erc8004", returns the raw ERC-8004 reputation number
    (the same one every other lookup tool shows as-is) and a
    Sybil-adjusted version that discounts reviewer clusters tracing back
    to a shared funding source — real, measured evidence exists that
    most ERC-8004 feedback on Base is exactly this kind of coordinated
    cluster, not independent reviewers. Other ecosystems have no
    on-chain registry to query, so that section is explicitly marked
    not applicable rather than silently empty. Every ecosystem gets any
    self-reported outcomes for this seller (see report_outcome below),
    kept separate and labeled unverified — never blended into the raw
    or Sybil-adjusted numbers, and rolled up across any other ecosystem
    identity confirmed to be the same real seller
    (docs/seller-normalization-spec.md).

    A pure read with no side effects — nothing about calling this tool
    is recorded anywhere.

    Args:
        ecosystem: Which ecosystem this seller belongs to. One of:
            "erc8004" (an on-chain agent — external_id is its numeric
            agentId as a string), "chatgpt_apps" (a ChatGPT-connected
            app — external_id is its "plugins_<hash>" ID, visible in
            the app's own chatgpt.com/plugins/... URL),
            "hubspot_marketplace", "zendesk_marketplace", "muse".
        external_id: That ecosystem's own native identifier for the
            seller — always passed as a string regardless of its native
            shape (an ERC-8004 agentId is numeric on-chain, but still
            passed here as e.g. "95910").
    """
    ecosystem = ecosystems.normalize(ecosystem)
    return seller_lookup.check_seller_trust(ecosystem, external_id)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
def report_outcome(
    ecosystem: str,
    external_id: str,
    outcome: str,
    evidence_ref: str | None = None,
    task_id: str | None = None,
    task_description: str | None = None,
    detail: str | None = None,
    seller_name: str | None = None,
    seller_website: str | None = None,
) -> dict:
    """Report how a completed transaction with a seller actually went,
    after the fact — for an integrator who already called
    check_agent_trust before hiring or paying this seller. Works for any
    ecosystem check_agent_trust does (ERC-8004 on-chain agent, ChatGPT
    app, etc.) — see `ecosystem` below.

    Self-reported, still never blended into check_agent_trust's raw or
    Sybil-adjusted ERC-8004 numbers (see docs/mcp-server-spec.md's
    "Anti-gaming" section) — but as of the OAuth mechanism
    (docs/oauth-trust-spec.md), each report is now tied to the identity
    that completed this connection's sign-in step, recorded alongside
    the report rather than being fully anonymous. That identity is not
    verified to own any particular on-chain presence — see
    docs/oauth-trust-spec.md for why that check was deliberately not
    required — only that it's the same caller across every report this
    connection makes.

    Deliberately NOT marked read-only: this writes a new record, so
    OpenAI's and any other compliant MCP host's approval model requires
    explicit user confirmation before it runs — correct behavior for a
    write, not a limitation.

    Args:
        ecosystem: Which ecosystem this seller belongs to — same values
            as check_agent_trust's (mvp/ecosystems.py): "erc8004",
            "chatgpt_apps", "hubspot_marketplace", "zendesk_marketplace",
            "muse".
        external_id: That ecosystem's own native identifier for the
            seller, as a string — the same value you used (or would
            use) for this seller in check_agent_trust.
        outcome: One of "completed", "disputed", "no_response".
        evidence_ref: Optional URI or hash pointing at supporting evidence
            (a transcript, a delivered-artifact hash) — the same
            evidence-URI pattern ERC-8004's giveFeedback() already uses.
        task_id: Optional, your own identifier for the task this outcome
            relates to — stored alongside the report. Purely informational.
        task_description: Optional short description of the task.
            Purely informational.
        detail: Optional free text explaining what actually happened —
            distinct from evidence_ref, which points AT evidence rather
            than describing it. Especially worth filling in when outcome
            isn't "completed": "disputed" alone doesn't say what went
            wrong; this does, and gets surfaced back to future callers.
        seller_name: Optional display name for this seller — used only
            the first time we see this (ecosystem, external_id) pair, to
            label it for our own records. Safe to omit.
        seller_website: Optional website for this seller — used only on
            first sighting, and is what enables (human-reviewed, never
            automatic) cross-ecosystem identity linking
            (docs/seller-normalization-spec.md). Safe to omit.
    """
    ecosystem = ecosystems.normalize(ecosystem)
    access_token = get_access_token()
    verified_subject = access_token.subject if access_token else None
    return outcomes.record_outcome(
        ecosystem,
        external_id,
        outcome,
        evidence_ref,
        verified_subject=verified_subject,
        task_id=task_id,
        task_description=task_description,
        detail=detail,
        seller_name=seller_name,
        seller_website=seller_website,
    )


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
