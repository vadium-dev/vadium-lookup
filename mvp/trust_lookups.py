"""Persists every check_agent_trust call — previously a pure stateless
read with zero record of who looked up what, when, or why. Lets
report_outcome correlate a later report back to the lookup that
preceded it: either exactly, via an explicit task_id the caller
supplies and reuses across both calls, or — when that's absent or
doesn't match anything — heuristically, on (verified_subject,
most-recent-prior-timestamp).

Generalized 2026-10-03 (docs/seller-normalization-spec.md) from a bare
ERC-8004 agent_id to any (ecosystem, external_id) pair, resolved via
mvp/sellers.py. `agent_id` is kept as a legacy, nullable column —
populated only for ecosystem="erc8004" calls — in case anything still
reads it directly; the real identity going forward is
seller_identity_id. Correlation searches across every identity
currently rolled up under the same seller (sellers.sibling_identity_ids),
not just the exact (ecosystem, external_id) pair used this time, so a
later-confirmed cross-ecosystem link immediately makes correlation work
across both without touching any historical row.

Logging a lookup must never break check_agent_trust itself — this is
the free, frictionless tool; a persistence hiccup here is not a reason
to fail a read. Failures here degrade silently, unlike
mvp/outcomes.py's report_outcome path, which is a write whose whole job
IS persistence and so fails loudly instead when DATABASE_URL is unset.
"""

from datetime import datetime, timezone

from mvp import sellers
from mvp.db import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trust_lookups (
    id SERIAL PRIMARY KEY,
    agent_id BIGINT,
    task_id TEXT,
    task_description TEXT,
    looked_up_at TIMESTAMPTZ NOT NULL,
    verified_subject TEXT
)
"""

# agent_id was NOT NULL in the original (ERC-8004-only) version of this
# table — dropped here since a non-ERC-8004 identity (e.g. a ChatGPT
# "plugins_<hash>" string) has no numeric agent_id at all.
_MIGRATE = """
ALTER TABLE trust_lookups ALTER COLUMN agent_id DROP NOT NULL;
ALTER TABLE trust_lookups ADD COLUMN IF NOT EXISTS seller_identity_id INT REFERENCES seller_identities(id)
"""


def _ensure_schema(conn) -> None:
    sellers._ensure_schema(conn)  # seller_identities must exist first — this table's FK depends on it
    conn.execute(_SCHEMA)
    conn.execute(_MIGRATE)


def record_lookup(
    ecosystem: str,
    external_id: str,
    task_id: str | None,
    task_description: str | None,
    verified_subject: str | None,
    seller_name: str | None = None,
    seller_website: str | None = None,
) -> None:
    """Best-effort — swallows any failure (including DATABASE_URL being
    unset entirely, the normal case for local/stdio use per the README)
    rather than letting this logging side-effect break the actual
    lookup check_agent_trust exists to perform.
    """
    try:
        identity_id = sellers.resolve_or_create_identity(
            ecosystem, external_id, canonical_name=seller_name, primary_website=seller_website
        )
        legacy_agent_id = int(external_id) if ecosystem == "erc8004" else None
        with connect() as conn:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO trust_lookups "
                "(agent_id, seller_identity_id, task_id, task_description, looked_up_at, verified_subject) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (legacy_agent_id, identity_id, task_id, task_description, datetime.now(timezone.utc), verified_subject),
            )
    except Exception:  # noqa: BLE001 — logging must never break the read path
        pass


def find_correlated_lookup(
    ecosystem: str,
    external_id: str,
    task_id: str | None,
    verified_subject: str | None,
    before: datetime,
) -> dict | None:
    """Finds the trust_lookups row a report_outcome call most likely
    follows up on, searching across every identity currently rolled up
    under the same seller — not just the exact (ecosystem, external_id)
    pair used this time.

    An explicit, matching task_id wins outright — it's a stronger
    signal than inferred identity, so it's trusted even across a
    changed verified_subject (e.g. a refreshed token mid-task).
    Otherwise falls back to the most recent prior lookup under the same
    seller by the same verified_subject. Returns None — not an error —
    when neither produces a match; "no prior lookup found" is a normal,
    common outcome, not a failure to surface as one.
    """
    try:
        identity_id = sellers.resolve_or_create_identity(ecosystem, external_id)
        seller_id = sellers.rolled_up_seller_id(identity_id)
        sibling_ids = sellers.sibling_identity_ids(seller_id) if seller_id else [identity_id]

        with connect() as conn:
            _ensure_schema(conn)
            if task_id:
                row = conn.execute(
                    "SELECT id, task_id, task_description, looked_up_at, verified_subject "
                    "FROM trust_lookups WHERE task_id = %s AND seller_identity_id = ANY(%s) "
                    "ORDER BY looked_up_at DESC LIMIT 1",
                    (task_id, sibling_ids),
                ).fetchone()
                if row:
                    return _row_to_dict(row, matched_by="explicit_task_id")

            if verified_subject:
                row = conn.execute(
                    "SELECT id, task_id, task_description, looked_up_at, verified_subject "
                    "FROM trust_lookups WHERE verified_subject = %s AND seller_identity_id = ANY(%s) AND looked_up_at <= %s "
                    "ORDER BY looked_up_at DESC LIMIT 1",
                    (verified_subject, sibling_ids, before),
                ).fetchone()
                if row:
                    return _row_to_dict(row, matched_by="heuristic")
    except Exception:  # noqa: BLE001 — correlation is enrichment, never load-bearing for the write itself
        return None
    return None


def _row_to_dict(row, matched_by: str) -> dict:
    return {
        "lookup_id": row[0],
        "task_id": row[1],
        "task_description": row[2],
        "looked_up_at": row[3].isoformat(),
        "verified_subject": row[4],
        "matched_by": matched_by,
    }
