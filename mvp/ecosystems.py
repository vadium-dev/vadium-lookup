"""Canonical, known ecosystem names (docs/seller-normalization-spec.md).

The database column backing this (seller_identities.ecosystem) is free
text, deliberately — a genuinely new ecosystem should be a data row,
not a migration. But the MCP tool boundary still validates against this
known set rather than accepting any string verbatim: without that,
inconsistent spelling from different callers ("chatgpt_apps" vs.
"ChatGPT-Apps") would silently fragment what should be one ecosystem
into two that can never be linked, defeating the whole point of
normalizing identity in the first place.

Adding a real new ecosystem is still cheap — add its name here, nothing
else needs to change structurally.
"""

ERC8004 = "erc8004"
CHATGPT_APPS = "chatgpt_apps"
HUBSPOT_MARKETPLACE = "hubspot_marketplace"
ZENDESK_MARKETPLACE = "zendesk_marketplace"
MUSE = "muse"

KNOWN_ECOSYSTEMS = {ERC8004, CHATGPT_APPS, HUBSPOT_MARKETPLACE, ZENDESK_MARKETPLACE, MUSE}


class UnknownEcosystem(ValueError):
    pass


def normalize(ecosystem: str) -> str:
    """Case/separator-insensitive match against the known set — "ChatGPT
    Apps", "chatgpt-apps", and "chatgpt_apps" all resolve to the same
    canonical string, so a caller's minor spelling variance doesn't
    fragment identity the way an un-normalized free-text column would.
    """
    candidate = ecosystem.strip().lower().replace("-", "_").replace(" ", "_")
    if candidate not in KNOWN_ECOSYSTEMS:
        raise UnknownEcosystem(
            f"Unknown ecosystem {ecosystem!r}. Known ecosystems: {sorted(KNOWN_ECOSYSTEMS)}. "
            "If this is a real new ecosystem, add it to mvp/ecosystems.py first."
        )
    return candidate
