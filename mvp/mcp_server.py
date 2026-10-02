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

from mvp.lookup import lookup
from mvp import outcomes
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
        "Free trust-record lookup for an ERC-8004 agentId: own-ledger history "
        "plus ERC-8004's public reputation feedback, shown both raw and "
        "Sybil-adjusted (diversity-weighted by funding cluster, so a group of "
        "reviewers tracing to the same funding source counts closer to one "
        "independent voice than many). No payment required."
    ),
    version="0.3.0",
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


@mcp.tool(annotations={"readOnlyHint": True})
def check_agent_trust(agent_id: int) -> dict:
    """Look up an ERC-8004 agent's trust record before hiring or paying it.

    Returns both the raw ERC-8004 reputation number (the same one every
    other lookup tool shows as-is) and a Sybil-adjusted version that
    discounts reviewer clusters tracing back to a shared funding source —
    real, measured evidence exists that most ERC-8004 feedback on Base is
    exactly this kind of coordinated cluster, not independent reviewers.
    Also returns any self-reported outcomes for this agent (see
    report_outcome below), kept separate and labeled unverified — never
    blended into the raw or Sybil-adjusted numbers.

    Explicitly annotated read-only: per OpenAI's own documented approval
    model (docs/mcp-server-spec.md), this lets a calling agent invoke it
    automatically, with no per-call human confirmation — the OAuth
    connection itself (docs/oauth-trust-spec.md) is the one-time setup
    step; nothing after that re-prompts the user for this tool.

    Args:
        agent_id: The ERC-8004 agentId — an on-chain identity (ERC-721 token id).
    """
    return lookup(agent_id)


@mcp.tool()
def report_outcome(agent_id: int, outcome: str, evidence_ref: str | None = None) -> dict:
    """Report how a completed transaction with an ERC-8004 agent actually
    went, after the fact — for an integrator who already called
    check_agent_trust before hiring or paying this agent.

    Self-reported, still never blended into check_agent_trust's raw or
    Sybil-adjusted ERC-8004 numbers (see docs/mcp-server-spec.md's
    "Anti-gaming" section) — but as of the OAuth mechanism
    (docs/oauth-trust-spec.md), each report is now tied to the wallet
    address that completed this connection's sign-in step, recorded
    alongside the report rather than being fully anonymous. That address
    is not verified to own any particular on-chain identity — seeing
    "docs/oauth-trust-spec.md" above for why that check was deliberately
    not required — only that it's the same caller across every report
    this connection makes.

    Deliberately NOT marked read-only: this writes a new record, so
    OpenAI's and any other compliant MCP host's approval model requires
    explicit user confirmation before it runs — correct behavior for a
    write, not a limitation.

    Args:
        agent_id: The ERC-8004 agentId this outcome is about.
        outcome: One of "completed", "disputed", "no_response".
        evidence_ref: Optional URI or hash pointing at supporting evidence
            (a transcript, a delivered-artifact hash) — the same
            evidence-URI pattern ERC-8004's giveFeedback() already uses.
    """
    access_token = get_access_token()
    verified_subject = access_token.subject if access_token else None
    return outcomes.record_outcome(agent_id, outcome, evidence_ref, verified_subject=verified_subject)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
