"""Ecosystem-aware trust lookup (docs/seller-normalization-spec.md) —
the entry point mcp_server.py's check_agent_trust calls.

ERC-8004 stays exactly as it was: reuses mvp/lookup.py's existing
on-chain + Phase 2 ledger + self-reported logic completely unchanged,
since that function's own signature (agent_id: int) is still what
app.py's HTTP /lookup/{agent_id} route depends on. Every other
ecosystem has no on-chain ERC-8004 registry and no Phase 2 ledger to
query at all — those sections are explicitly marked not-applicable
rather than silently omitted (which would look like "nothing found")
or guessed at.
"""

from mvp import outcomes
from mvp.lookup import lookup as _erc8004_lookup


def check_seller_trust(ecosystem: str, external_id: str) -> dict:
    if ecosystem == "erc8004":
        return _erc8004_lookup(int(external_id))

    return {
        "ecosystem": ecosystem,
        "external_id": external_id,
        "vadium_native": {
            "available": False,
            "note": f"the Phase 2 evidence ledger is ERC-8004-specific; not applicable to ecosystem {ecosystem!r}",
        },
        "self_reported": outcomes.summarize(ecosystem, external_id),
        "erc8004_public": {
            "available": False,
            "note": f"ecosystem {ecosystem!r} has no on-chain ERC-8004 registry to query",
        },
    }
