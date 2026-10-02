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

from mcp.server.mcpserver import MCPServer

from mvp.lookup import lookup
from mvp import outcomes

mcp = MCPServer(
    name="vadium-lookup",
    description=(
        "Free trust-record lookup for an ERC-8004 agentId: own-ledger history "
        "plus ERC-8004's public reputation feedback, shown both raw and "
        "Sybil-adjusted (diversity-weighted by funding cluster, so a group of "
        "reviewers tracing to the same funding source counts closer to one "
        "independent voice than many). No payment required."
    ),
    version="0.2.0",
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
    automatically, with no human confirmation required — the whole point
    of putting this on a reachable transport in the first place.

    Args:
        agent_id: The ERC-8004 agentId — an on-chain identity (ERC-721 token id).
    """
    return lookup(agent_id)


@mcp.tool()
def report_outcome(agent_id: int, outcome: str, evidence_ref: str | None = None) -> dict:
    """Report how a completed transaction with an ERC-8004 agent actually
    went, after the fact — for an integrator who already called
    check_agent_trust before hiring or paying this agent.

    This is self-reported by whoever calls it, not independently verified
    at call time, and is never blended into check_agent_trust's raw or
    Sybil-adjusted ERC-8004 numbers — there's currently no caller-identity
    signal available to weight it by the way on-chain reviewer feedback
    is (see docs/mcp-server-spec.md's "Anti-gaming" section for why this
    is a deliberate simplification, not an oversight). Shown separately,
    labeled unverified.

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
    return outcomes.record_outcome(agent_id, outcome, evidence_ref)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
