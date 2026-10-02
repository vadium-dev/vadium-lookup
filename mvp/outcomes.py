"""Self-reported transaction outcomes from MCP integrators.

Moved off local SQLite to Postgres 2026-10-02 — confirmed by directly
testing against the live production service that Render's local disk
does NOT survive a redeploy (it survives a plain restart, which is what
made the SQLite version look safe at first; a real redeploy wiped it
completely). Self-reported outcomes are exactly the data this whole
effort depends on accumulating over time, so ephemeral storage was a
real, not theoretical, problem.

Still deliberately NOT part of own_ledger.py's schema/read path — that
module is documented as a read-only consumer of the separately-anchored
Phase 2 evidence ledger, and this must not blur that boundary. This is
its own table, in its own connection, that this repo fully owns.

Never merged into check_agent_trust's raw/Sybil-adjusted ERC-8004
numbers (see docs/mcp-server-spec.md's "Anti-gaming" section) — shown
separately, labeled unverified, until there's a real caller-identity
signal (the planned OAuth work) to weight it by.
"""

from contextlib import contextmanager
from datetime import datetime, timezone

from mvp.db import DatabaseUnavailable, connect as _db_connect

# Preserve the existing public name — callers (mvp/lookup.py, tests)
# import OutcomesStoreUnavailable specifically.
OutcomesStoreUnavailable = DatabaseUnavailable

_VALID_OUTCOMES = {"completed", "disputed", "no_response"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reported_outcomes (
    id SERIAL PRIMARY KEY,
    agent_id BIGINT NOT NULL,
    outcome TEXT NOT NULL,
    evidence_ref TEXT,
    reported_at TIMESTAMPTZ NOT NULL,
    verified_subject TEXT
)
"""

# Added 2026-10-02 alongside the OAuth trust mechanism
# (docs/oauth-trust-spec.md) — a column that didn't exist when the table
# was first created. ALTER ... ADD COLUMN IF NOT EXISTS runs alongside
# CREATE TABLE IF NOT EXISTS so existing deployments pick it up without
# a separate migration step.
_MIGRATE = """
ALTER TABLE reported_outcomes ADD COLUMN IF NOT EXISTS verified_subject TEXT
"""


@contextmanager
def _connect():
    with _db_connect() as conn:
        conn.execute(_SCHEMA)
        conn.execute(_MIGRATE)
        yield conn


def record_outcome(
    agent_id: int,
    outcome: str,
    evidence_ref: str | None = None,
    verified_subject: str | None = None,
) -> dict:
    """Store one self-reported outcome. Returns what was stored, not a
    trust score — this never computes or returns anything that looks
    like a verdict, since nothing here has been independently checked.

    verified_subject: the wallet address from the caller's OAuth token
    (docs/oauth-trust-spec.md), or None for calls made before the OAuth
    mechanism shipped or through a transport that doesn't carry one.
    """
    if outcome not in _VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(_VALID_OUTCOMES)}, got {outcome!r}")

    reported_at = datetime.now(timezone.utc)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO reported_outcomes (agent_id, outcome, evidence_ref, reported_at, verified_subject) "
            "VALUES (%s, %s, %s, %s, %s)",
            (agent_id, outcome, evidence_ref, reported_at, verified_subject),
        )

    return {
        "agent_id": agent_id,
        "outcome": outcome,
        "evidence_ref": evidence_ref,
        "reported_at": reported_at.isoformat(),
        "verified_subject": verified_subject,
        "stored": True,
    }


def summarize(agent_id: int) -> dict:
    """Unweighted counts only — explicitly labeled self-reported and
    unverified. Never call this a score; it isn't one.

    Degrades to an explicit "unavailable" note rather than raising, so a
    missing DATABASE_URL doesn't take down check_agent_trust's read path
    entirely — the reputation lookup should still work even if outcome
    reporting is misconfigured.
    """
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT outcome, COUNT(*) as n FROM reported_outcomes "
                "WHERE agent_id = %s GROUP BY outcome",
                (agent_id,),
            ).fetchall()
    except OutcomesStoreUnavailable:
        return {
            "agent_id": agent_id,
            "has_reports": False,
            "counts": {k: 0 for k in sorted(_VALID_OUTCOMES)},
            "verified": False,
            "note": "self-reported outcomes store is not configured (DATABASE_URL unset)",
        }

    if not rows:
        return _empty(agent_id)
    counts = {outcome: n for outcome, n in rows}
    return {
        "agent_id": agent_id,
        "has_reports": True,
        "counts": {k: counts.get(k, 0) for k in sorted(_VALID_OUTCOMES)},
        "verified": False,
        "note": (
            "self-reported by integrators via the report_outcome MCP tool, "
            "not independently verified — never blended into the raw or "
            "Sybil-adjusted ERC-8004 numbers above"
        ),
    }


def _empty(agent_id: int) -> dict:
    return {
        "agent_id": agent_id,
        "has_reports": False,
        "counts": {k: 0 for k in sorted(_VALID_OUTCOMES)},
        "verified": False,
        "note": "no self-reported outcomes for this agent yet",
    }
