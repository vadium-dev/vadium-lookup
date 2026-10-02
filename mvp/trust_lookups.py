"""Persists every check_agent_trust call — previously a pure stateless
read with zero record of who looked up what, when, or why. Lets
report_outcome correlate a later report back to the lookup that
preceded it: either exactly, via an explicit task_id the caller
supplies and reuses across both calls, or — when that's absent or
doesn't match anything — heuristically, on (verified_subject, agent_id,
most-recent-prior-timestamp).

Logging a lookup must never break check_agent_trust itself — this is
the free, frictionless tool; a persistence hiccup here is not a reason
to fail a read. Failures here degrade silently, unlike
mvp/outcomes.py's report_outcome path, which is a write whose whole job
IS persistence and so fails loudly instead when DATABASE_URL is unset.
"""

from datetime import datetime, timezone

from mvp.db import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trust_lookups (
    id SERIAL PRIMARY KEY,
    agent_id BIGINT NOT NULL,
    task_id TEXT,
    task_description TEXT,
    looked_up_at TIMESTAMPTZ NOT NULL,
    verified_subject TEXT
)
"""


def record_lookup(
    agent_id: int,
    task_id: str | None,
    task_description: str | None,
    verified_subject: str | None,
) -> None:
    """Best-effort — swallows any failure (including DATABASE_URL being
    unset entirely, the normal case for local/stdio use per the README)
    rather than letting this logging side-effect break the actual
    lookup check_agent_trust exists to perform.
    """
    try:
        with connect() as conn:
            conn.execute(_SCHEMA)
            conn.execute(
                "INSERT INTO trust_lookups (agent_id, task_id, task_description, looked_up_at, verified_subject) "
                "VALUES (%s, %s, %s, %s, %s)",
                (agent_id, task_id, task_description, datetime.now(timezone.utc), verified_subject),
            )
    except Exception:  # noqa: BLE001 — logging must never break the read path
        pass


def find_correlated_lookup(
    agent_id: int,
    task_id: str | None,
    verified_subject: str | None,
    before: datetime,
) -> dict | None:
    """Finds the trust_lookups row a report_outcome call most likely
    follows up on.

    An explicit, matching task_id wins outright — it's a stronger
    signal than inferred identity, so it's trusted even across a
    changed verified_subject (e.g. a refreshed token mid-task).
    Otherwise falls back to the most recent prior lookup of the same
    agent by the same verified_subject. Returns None — not an error —
    when neither produces a match; "no prior lookup found" is a normal,
    common outcome (the caller may never have called check_agent_trust
    first, or may be reporting long after any record was correlatable),
    not a failure to surface as one.
    """
    try:
        with connect() as conn:
            conn.execute(_SCHEMA)
            if task_id:
                row = conn.execute(
                    "SELECT id, task_id, task_description, looked_up_at, verified_subject "
                    "FROM trust_lookups WHERE task_id = %s AND agent_id = %s "
                    "ORDER BY looked_up_at DESC LIMIT 1",
                    (task_id, agent_id),
                ).fetchone()
                if row:
                    return _row_to_dict(row, matched_by="explicit_task_id")

            if verified_subject:
                row = conn.execute(
                    "SELECT id, task_id, task_description, looked_up_at, verified_subject "
                    "FROM trust_lookups WHERE verified_subject = %s AND agent_id = %s AND looked_up_at <= %s "
                    "ORDER BY looked_up_at DESC LIMIT 1",
                    (verified_subject, agent_id, before),
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
