"""MCP server wrapper around the same free lookup mvp/app.py exposes
over HTTP — this is the actual discovery channel with real precedent
(docs/IDEA.md §6.2: agent/skill discovery runs on registries and
install counts, not brand; PayCrow got real usage off one GitHub star
specifically because it shipped this way). No payment anywhere here,
same as the HTTP endpoint.
"""

from mcp.server.mcpserver import MCPServer

from mvp.lookup import lookup

mcp = MCPServer(
    name="vadium-lookup",
    description=(
        "Free trust-record lookup for an ERC-8004 agentId: own-ledger history "
        "plus ERC-8004's public reputation feedback, shown both raw and "
        "Sybil-adjusted (diversity-weighted by funding cluster, so a group of "
        "reviewers tracing to the same funding source counts closer to one "
        "independent voice than many). No payment required."
    ),
    version="0.1.0",
)


@mcp.tool()
def check_agent_trust(agent_id: int) -> dict:
    """Look up an ERC-8004 agent's trust record before hiring or paying it.

    Returns both the raw ERC-8004 reputation number (the same one every
    other lookup tool shows as-is) and a Sybil-adjusted version that
    discounts reviewer clusters tracing back to a shared funding source —
    real, measured evidence exists that most ERC-8004 feedback on Base is
    exactly this kind of coordinated cluster, not independent reviewers.

    Args:
        agent_id: The ERC-8004 agentId — an on-chain identity (ERC-721 token id).
    """
    return lookup(agent_id)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
