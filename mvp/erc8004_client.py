"""Read-only client for ERC-8004's ReputationRegistry.

ABI is the real, complete one — `mvp/reputation_registry_abi.json`,
pulled directly from the reference implementation's own repo
(github.com/erc-8004/erc-8004-contracts, abis/ReputationRegistry.json),
not hand-typed from the EIP text. Verified against live Base mainnet
state 2026-09-26 — see docs/erc8004-mvp-verification.md. Only read
functions are ever called; nothing here can write to the registry.
"""

import json
import os
from pathlib import Path

from web3 import Web3

_ABI = json.loads((Path(__file__).parent / "reputation_registry_abi.json").read_text())

# Defaults below are real, not placeholders — verified 2026-09-26 by
# calling the live contract (see docs/erc8004-mvp-verification.md): the
# address returned deployed bytecode, and getSummary() cleanly reverted
# with "clientAddresses required" (the exact constraint the EIP text
# describes) rather than an ABI-mismatch error, confirming both the
# address and this module's ABI are correct against real Base mainnet
# state. Sourced from the README of https://github.com/erc-8004/erc-8004-contracts
# — re-check there if this ever stops matching (a third party's
# contract, not one we control; still overridable via env var).
BASE_RPC_URL = os.environ.get("BASE_RPC_URL", "https://mainnet.base.org")
REGISTRY_ADDRESS = os.environ.get(
    "ERC8004_REPUTATION_REGISTRY_ADDRESS",
    "0x8004BAa17C55a88189AE136b182e5fdA19dE9b63",
)
IDENTITY_REGISTRY_ADDRESS = os.environ.get(
    "ERC8004_IDENTITY_REGISTRY_ADDRESS",
    "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
)

def _client() -> Web3:
    return Web3(Web3.HTTPProvider(BASE_RPC_URL))


def _contract(w3: Web3):
    return w3.eth.contract(address=Web3.to_checksum_address(REGISTRY_ADDRESS), abi=_ABI)


def discover_reviewers(agent_id: int) -> tuple[list[str], str | None]:
    """Real on-chain enumeration via getClients(agentId) — the registry's
    own direct answer to "who reviewed this agent", no event-log scanning
    needed. Returns (reviewer_addresses, note); note is only set on a
    genuine RPC/contract error, not for the (common, honest) case of zero
    reviewers.
    """
    try:
        w3 = _client()
        clients = _contract(w3).functions.getClients(agent_id).call()
        return list(clients), None
    except Exception as e:  # noqa: BLE001 — surface as data, not a 500
        return [], f"reviewer discovery failed: {e}"


def get_raw_summary(agent_id: int, client_addresses: list[str], tag1: str = "", tag2: str = "") -> dict:
    """The number every other lookup tool (phion.systems, Quicknode's
    explorer, etc.) shows as-is. We show it too, labeled as raw, precisely
    so the Sybil-adjusted number next to it means something by contrast.
    """
    contract = _contract(_client())
    checksummed = [Web3.to_checksum_address(a) for a in client_addresses]
    count, value, decimals = contract.functions.getSummary(agent_id, checksummed, tag1, tag2).call()
    return {
        "count": count,
        "value": value / (10 ** decimals) if decimals else value,
        "reviewer_count_considered": len(checksummed),
        "source": "ERC-8004 ReputationRegistry (raw, unweighted)",
    }


def get_all_feedback(agent_id: int, client_addresses: list[str], tag1: str = "", tag2: str = "", include_revoked: bool = False) -> list[dict]:
    """Per-reviewer feedback entries — the input diversity-weighting
    (mvp/diversity.py) actually needs, since it operates per reviewer,
    not on a pre-aggregated summary.
    """
    contract = _contract(_client())
    checksummed = [Web3.to_checksum_address(a) for a in client_addresses]
    clients, idxs, values, decimals, tag1s, tag2s, revoked = contract.functions.readAllFeedback(
        agent_id, checksummed, tag1, tag2, include_revoked
    ).call()
    return [
        {
            "client": clients[i],
            "feedback_index": idxs[i],
            "value": values[i] / (10 ** decimals[i]) if decimals[i] else values[i],
            "tag1": tag1s[i],
            "tag2": tag2s[i],
            "revoked": revoked[i],
        }
        for i in range(len(clients))
    ]
