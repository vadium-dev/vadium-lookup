"""Normalized seller identity across ecosystems — ERC-8004 agents on
Base/Arc, ChatGPT Apps (plugins_<hash>), enterprise marketplaces
(HubSpot, Zendesk), and whatever comes next. See
docs/seller-normalization-spec.md for the full design rationale.

Two-table split, not one wide table: `sellers` is our own normalized,
ecosystem-agnostic record; `seller_identities` is one row per *sighting*
of a seller in a specific ecosystem. The same real business might have
an ERC-8004 agent AND a ChatGPT plugin AND a HubSpot app — three
identities, one seller, once we're confident enough to link them.
Where we're not confident, each sighting gets its own seller row,
never forced to guess.

Linking is automatic-but-marked, not automatic-merge: a matching
primary_website between two otherwise-unrelated sellers creates a
seller_link_candidates row (status='pending'), not an immediate merge.
A human confirms or rejects it later (confirm_link / reject_link) —
getting this wrong silently would blend two different real businesses'
reputations together, which is worse than a brief review backlog.
"""

import json
from datetime import datetime, timezone

from mvp.db import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sellers (
    id SERIAL PRIMARY KEY,
    canonical_name TEXT,
    primary_website TEXT,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS seller_identities (
    id SERIAL PRIMARY KEY,
    seller_id INT NOT NULL REFERENCES sellers(id),
    ecosystem TEXT NOT NULL,
    external_id TEXT NOT NULL,
    external_url TEXT,
    metadata JSONB,
    discovered_at TIMESTAMPTZ NOT NULL,
    UNIQUE (ecosystem, external_id)
);

CREATE TABLE IF NOT EXISTS seller_link_candidates (
    id SERIAL PRIMARY KEY,
    seller_id_a INT NOT NULL REFERENCES sellers(id),
    seller_id_b INT NOT NULL REFERENCES sellers(id),
    matched_on TEXT NOT NULL,
    matched_value TEXT NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewed_at TIMESTAMPTZ,
    CHECK (seller_id_a < seller_id_b),
    UNIQUE (seller_id_a, seller_id_b)
);
"""


def _ensure_schema(conn) -> None:
    conn.execute(_SCHEMA)


def _normalize_website(website: str | None) -> str | None:
    """Bare-domain normalization so "https://Notion.so/" and
    "notion.so" are recognized as the same matching key — without this,
    the single strongest linking signal available would miss trivial
    formatting differences between how each ecosystem presents a URL.
    """
    if not website:
        return None
    w = website.strip().lower()
    for prefix in ("https://", "http://"):
        if w.startswith(prefix):
            w = w[len(prefix):]
    w = w.split("/")[0]
    if w.startswith("www."):
        w = w[4:]
    return w or None


def resolve_or_create_identity(
    ecosystem: str,
    external_id: str,
    canonical_name: str | None = None,
    primary_website: str | None = None,
    external_url: str | None = None,
    metadata: dict | None = None,
) -> int:
    """Returns the seller_identities.id for (ecosystem, external_id),
    creating both it and a fresh sellers row on first sighting. Never
    auto-merges into an existing seller even when primary_website
    matches one — see _detect_link_candidates.

    Info often arrives incrementally — a bare check_agent_trust call
    (no name/website) can create the identity before a later
    report_outcome call supplies them, or vice versa. Found by testing:
    silently freezing the seller's name/website at whatever the first
    call happened to provide would permanently lose a website supplied
    later, and skip link detection forever for that seller. So an
    already-existing identity still gets its seller's canonical_name /
    primary_website filled in (only when currently empty — never
    overwritten once set) and link-detection re-run at that point, if
    this call is the first one to actually supply a website.
    """
    website = _normalize_website(primary_website)
    now = datetime.now(timezone.utc)

    with connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT id, seller_id FROM seller_identities WHERE ecosystem = %s AND external_id = %s",
            (ecosystem, external_id),
        ).fetchone()
        if row:
            identity_id, seller_id = row
            if canonical_name or website:
                current = conn.execute(
                    "SELECT canonical_name, primary_website FROM sellers WHERE id = %s", (seller_id,)
                ).fetchone()
                new_name = current[0] or canonical_name
                new_website = current[1] or website
                if new_name != current[0] or new_website != current[1]:
                    conn.execute(
                        "UPDATE sellers SET canonical_name = %s, primary_website = %s WHERE id = %s",
                        (new_name, new_website, seller_id),
                    )
                # Only just learned this seller's website for the first time —
                # link detection couldn't have run before, so run it now.
                if website and not current[1]:
                    _detect_link_candidates(conn, seller_id, website, now)
            return identity_id

        seller_row = conn.execute(
            "INSERT INTO sellers (canonical_name, primary_website, created_at) VALUES (%s, %s, %s) RETURNING id",
            (canonical_name, website, now),
        ).fetchone()
        seller_id = seller_row[0]

        identity_row = conn.execute(
            "INSERT INTO seller_identities "
            "(seller_id, ecosystem, external_id, external_url, metadata, discovered_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (seller_id, ecosystem, external_id, external_url, json.dumps(metadata) if metadata else None, now),
        ).fetchone()
        identity_id = identity_row[0]

        if website:
            _detect_link_candidates(conn, seller_id, website, now)

    return identity_id


def _detect_link_candidates(conn, new_seller_id: int, website: str, now: datetime) -> None:
    """Marks (doesn't merge) every other seller sharing the same
    normalized website. Idempotent — the UNIQUE constraint on
    (seller_id_a, seller_id_b) means re-detecting an already-flagged
    pair is a no-op, not a duplicate row.
    """
    matches = conn.execute(
        "SELECT id FROM sellers WHERE primary_website = %s AND id != %s",
        (website, new_seller_id),
    ).fetchall()
    for (other_id,) in matches:
        a, b = sorted((new_seller_id, other_id))
        conn.execute(
            "INSERT INTO seller_link_candidates "
            "(seller_id_a, seller_id_b, matched_on, matched_value, detected_at, status) "
            "VALUES (%s, %s, %s, %s, %s, 'pending') "
            "ON CONFLICT (seller_id_a, seller_id_b) DO NOTHING",
            (a, b, "primary_website", website, now),
        )


def rolled_up_seller_id(seller_identity_id: int) -> int | None:
    """The sellers.id a specific identity currently rolls up under —
    changes if/when that identity's seller gets merged via confirm_link,
    without needing to touch any historical row that referenced this
    seller_identity_id. This indirection is the entire point of storing
    seller_identity_id (immutable) rather than seller_id (mutable via
    merges) on reported_outcomes.
    """
    with connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT seller_id FROM seller_identities WHERE id = %s", (seller_identity_id,)
        ).fetchone()
    return row[0] if row else None


def sibling_identity_ids(seller_id: int) -> list[int]:
    """Every identity (possibly across multiple ecosystems) currently
    rolled up under this seller — what a rolled-up trust query should
    actually search over, not just the one identity a caller happened
    to use this time.
    """
    with connect() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT id FROM seller_identities WHERE seller_id = %s", (seller_id,)
        ).fetchall()
    return [r[0] for r in rows]


def list_pending_link_candidates() -> list[dict]:
    with connect() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT c.id, c.seller_id_a, sa.canonical_name, c.seller_id_b, sb.canonical_name, "
            "       c.matched_on, c.matched_value, c.detected_at "
            "FROM seller_link_candidates c "
            "JOIN sellers sa ON sa.id = c.seller_id_a "
            "JOIN sellers sb ON sb.id = c.seller_id_b "
            "WHERE c.status = 'pending' ORDER BY c.detected_at ASC"
        ).fetchall()
    return [
        {
            "candidate_id": r[0],
            "seller_id_a": r[1], "seller_a_name": r[2],
            "seller_id_b": r[3], "seller_b_name": r[4],
            "matched_on": r[5], "matched_value": r[6],
            "detected_at": r[7].isoformat(),
        }
        for r in rows
    ]


def confirm_link(candidate_id: int) -> None:
    """Performs the actual merge: every identity under the higher
    (newer) seller_id is re-pointed onto the lower (older, 'surviving')
    one. The losing sellers row is left in place, not deleted — it's
    still a valid FK target for seller_identities history and for
    seller_link_candidates' own audit trail.
    """
    with connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT seller_id_a, seller_id_b FROM seller_link_candidates WHERE id = %s AND status = 'pending'",
            (candidate_id,),
        ).fetchone()
        if not row:
            return
        survivor, merged_away = row
        conn.execute(
            "UPDATE seller_identities SET seller_id = %s WHERE seller_id = %s", (survivor, merged_away)
        )
        conn.execute(
            "UPDATE seller_link_candidates SET status = 'confirmed', reviewed_at = %s WHERE id = %s",
            (datetime.now(timezone.utc), candidate_id),
        )


def reject_link(candidate_id: int) -> None:
    with connect() as conn:
        _ensure_schema(conn)
        conn.execute(
            "UPDATE seller_link_candidates SET status = 'rejected', reviewed_at = %s WHERE id = %s AND status = 'pending'",
            (datetime.now(timezone.utc), candidate_id),
        )
