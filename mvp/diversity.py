"""ADR-004's diversity-weighting (docs/phase3-design-decisions.md), applied
to someone else's real feedback data instead of our own.

Method matches the Sybil-detection approach the 2606.26028 paper itself
used (verified against the paper directly, not a summary of it, before
writing this — see docs/erc8004-issue-99-comment.md): cluster reviewers
by shared *first funder* — the address that sent each reviewer wallet
its very first native-token balance. A cluster of N reviewers that all
trace back to one funder is treated as roughly one independent voice,
not N, using an inverse-Simpson diversity index rather than a hard
cutoff — the same principle, not the same operational thresholds, as
ADR-008's collusion detection.

Requires a block-explorer API for "first funder" (a smart contract
cannot answer this — it needs historical chain-wide transaction data no
contract can scan; see the reasoning already captured in this project's
"Inside Vadium" mechanism walkthrough). Degrades honestly, not silently,
when no API key is configured.
"""

import os
import time

import requests

BASESCAN_API_KEY = os.environ.get("BASESCAN_API_KEY")
BASESCAN_API_URL = "https://api.basescan.org/api"

_funder_cache: dict[str, str | None] = {}


def _first_funder(address: str) -> str | None:
    """The sender of this address's earliest incoming native-token
    transfer. Cached in-process since the same funder/reviewer shows up
    across many lookups.
    """
    if address in _funder_cache:
        return _funder_cache[address]
    if not BASESCAN_API_KEY:
        return None

    resp = requests.get(
        BASESCAN_API_URL,
        params={
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": 1,
            "offset": 1,
            "sort": "asc",
            "apikey": BASESCAN_API_KEY,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    result = data.get("result")
    funder = result[0]["from"].lower() if result and isinstance(result, list) and result else None
    _funder_cache[address] = funder
    time.sleep(0.21)  # Basescan free tier: 5 req/s
    return funder


def cluster_reviewers(addresses: list[str]) -> tuple[dict[str, list[str]], bool]:
    """Groups addresses by shared first-funder. Second return value is
    False when clustering couldn't actually run (no API key) — the
    caller must surface that honestly rather than presenting an
    unweighted average as if it were adjusted.
    """
    if not BASESCAN_API_KEY:
        return {a: [a] for a in addresses}, False

    clusters: dict[str, list[str]] = {}
    for addr in addresses:
        funder = _first_funder(addr) or addr  # no discoverable funder: treat as its own root
        clusters.setdefault(funder, []).append(addr)
    return clusters, True


def inverse_simpson(cluster_sizes: list[int]) -> float:
    """Effective number of independent groups. All N in one cluster -> 1.0.
    N singleton clusters -> N. This is the number itself, not a 0-1 index —
    directly readable as "effective independent reviewers."
    """
    total = sum(cluster_sizes)
    if total == 0:
        return 0.0
    sum_sq = sum((n / total) ** 2 for n in cluster_sizes)
    return 1.0 / sum_sq if sum_sq > 0 else 0.0


def adjusted_score(feedback: list[dict]) -> dict:
    """feedback: list of {"client": address, "value": float, ...} from
    erc8004_client.get_all_feedback(). Returns raw vs. diversity-adjusted
    side by side — never blended into one number, per the design
    decision made when this was scoped (docs/design-partner-outreach.md,
    BlockRun candidate note on the same "label the source" principle).
    """
    if not feedback:
        return {
            "raw_count": 0, "raw_mean": None,
            "effective_independent_reviewers": 0.0, "adjusted_mean": None,
            "clustering_ran": False, "note": "no feedback entries to adjust",
        }

    addresses = [f["client"] for f in feedback]
    clusters, clustering_ran = cluster_reviewers(addresses)

    raw_mean = sum(f["value"] for f in feedback) / len(feedback)

    if not clustering_ran:
        return {
            "raw_count": len(feedback), "raw_mean": raw_mean,
            "effective_independent_reviewers": None, "adjusted_mean": None,
            "clustering_ran": False,
            "note": "BASESCAN_API_KEY not configured — cannot compute the "
                    "Sybil-adjusted number, showing raw only so it isn't "
                    "mistaken for an adjusted one",
        }

    by_client = {f["client"]: f["value"] for f in feedback}
    cluster_means = []
    for members in clusters.values():
        vals = [by_client[m] for m in members if m in by_client]
        if vals:
            cluster_means.append(sum(vals) / len(vals))
    adjusted_mean = sum(cluster_means) / len(cluster_means) if cluster_means else None

    effective_reviewers = inverse_simpson([len(m) for m in clusters.values()])

    return {
        "raw_count": len(feedback),
        "raw_mean": round(raw_mean, 3),
        "effective_independent_reviewers": round(effective_reviewers, 1),
        "adjusted_mean": round(adjusted_mean, 3) if adjusted_mean is not None else None,
        "cluster_count": len(clusters),
        "largest_cluster_size": max((len(m) for m in clusters.values()), default=0),
        "clustering_ran": True,
        "note": "adjusted_mean weights each independent funding cluster equally, "
                "instead of each raw feedback entry equally",
    }
