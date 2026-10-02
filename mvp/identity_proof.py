"""Wallet-signature identity proof for the OAuth authorization step
(docs/oauth-trust-spec.md).

Deliberately does NOT check that the signing wallet owns an ERC-8004
agent identity on-chain — that was the original design and was dropped
before shipping: the human completing /authorize is whoever is hiring
an agent through a Dots/ChatGPT connection, not an agent operator, and
almost none of them would already own an ERC-8004 identity. Requiring
on-chain ownership here would make report_outcome unusable for its
actual audience, not more secure. What this module verifies is only
"this is the same wallet across every call this OAuth connection makes"
— a stable, user-chosen identity instead of none at all.
"""

from eth_account import Account
from eth_account.messages import encode_defunct


def challenge_message(request_id: str) -> str:
    """Deterministic from request_id alone — nothing extra to store
    server-side, and binding the signature to one specific pending
    authorization prevents replaying a signature across requests.
    """
    return (
        "Sign to connect your wallet to Vadium.\n"
        f"Request: {request_id}\n"
        "This will not trigger a transaction or cost any gas."
    )


def recover_signer(request_id: str, signature: str) -> str:
    """Returns the checksummed address that produced `signature` over
    this request's challenge message. Pure cryptographic recovery — no
    network call, nothing to fail against an RPC endpoint.

    Raises on a malformed signature (bad hex, wrong length) — the
    caller should treat that as "verification failed," same as a
    recovered address that doesn't match what the client claimed.
    """
    message = encode_defunct(text=challenge_message(request_id))
    return Account.recover_message(message, signature=signature)
