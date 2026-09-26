"""Orchestrates one lookup: our own ledger + ERC-8004's public feedback,
raw and Sybil-adjusted, clearly labeled by source. This is the whole
MVP — see docs/IDEA.md for why it's free and what it's actually testing
(discoverability, not revenue).
"""

from mvp import erc8004_client, own_ledger, diversity


def lookup(agent_id: int, known_reviewers: list[str] | None = None) -> dict:
    """`known_reviewers` is a temporary, explicit workaround for
    discover_reviewers() not being wired up yet (see erc8004_client.py) —
    pass the reviewer addresses if you already know them; omit them and
    the response says plainly why the ERC-8004 section is empty, instead
    of silently looking like "no feedback exists."
    """
    result = {
        "agent_id": agent_id,
        "vadium_native": own_ledger.summarize(agent_identity=str(agent_id)),
    }

    reviewers = known_reviewers
    if reviewers is None:
        reviewers, note = erc8004_client.discover_reviewers(agent_id)
        if note:  # a real RPC/contract error, not "zero reviewers"
            result["erc8004_public"] = {"available": False, "note": note}
            return result

    if not reviewers:
        # A genuinely empty reviewer list — the contract itself reverts
        # with "clientAddresses required" if you call getSummary/
        # readAllFeedback with none, so this has to be handled here
        # rather than left to surface as an exception (found by actually
        # calling the live contract with an empty list — see
        # docs/erc8004-mvp-verification.md).
        result["erc8004_public"] = {
            "available": True,
            "raw": {"count": 0, "value": None, "reviewer_count_considered": 0,
                     "source": "ERC-8004 ReputationRegistry (raw, unweighted)"},
            "sybil_adjusted": diversity.adjusted_score([]),
        }
        return result

    try:
        feedback = erc8004_client.get_all_feedback(agent_id, reviewers)
        raw = erc8004_client.get_raw_summary(agent_id, reviewers)
    except Exception as e:  # noqa: BLE001 — contract reverts, RPC errors: report, don't crash
        result["erc8004_public"] = {"available": False, "note": str(e)}
        return result

    result["erc8004_public"] = {
        "available": True,
        "raw": raw,
        "sybil_adjusted": diversity.adjusted_score(feedback),
    }
    return result
