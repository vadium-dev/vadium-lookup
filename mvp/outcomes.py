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

from mvp import trust_lookups
from mvp.db import DatabaseUnavailable, connect as _db_connect

# Preserve the existing public name — callers (mvp/lookup.py, tests)
# import OutcomesStoreUnavailable specifically.
OutcomesStoreUnavailable = DatabaseUnavailable

_VALID_OUTCOMES = {"completed", "disputed", "no_response"}

# How many non-"completed" reports' full detail to surface in
# summarize() — unweighted counts alone ("2 disputed") tell a reader
# nothing about what actually went wrong; the raw detail text does.
# Capped, not all of them, so an agent with a long history doesn't blow
# up check_agent_trust's response size.
_RECENT_ISSUES_LIMIT = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reported_outcomes (
    id SERIAL PRIMARY KEY,
    agent_id BIGINT NOT NULL,
    outcome TEXT NOT NULL,
    evidence_ref TEXT,
    reported_at TIMESTAMPTZ NOT NULL,
    verified_subject TEXT,
    task_id TEXT,
    task_description TEXT,
    detail TEXT
)
"""

# Added 2026-10-02 alongside the OAuth trust mechanism
# (docs/oauth-trust-spec.md) and the task-correlation + detail-text
# columns — none of which existed when the table was first created.
# ALTER ... ADD COLUMN IF NOT EXISTS runs alongside CREATE TABLE IF NOT
# EXISTS so an already-deployed table picks them up without a separate
# migration step.
_MIGRATE = """
ALTER TABLE reported_outcomes ADD COLUMN IF NOT EXISTS verified_subject TEXT;
ALTER TABLE reported_outcomes ADD COLUMN IF NOT EXISTS task_id TEXT;
ALTER TABLE reported_outcomes ADD COLUMN IF NOT EXISTS task_description TEXT;
ALTER TABLE reported_outcomes ADD COLUMN IF NOT EXISTS detail TEXT
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
    task_id: str | None = None,
    task_description: str | None = None,
    detail: str | None = None,
) -> dict:
    """Store one self-reported outcome. Returns what was stored, not a
    trust score — this never computes or returns anything that looks
    like a verdict, since nothing here has been independently checked.

    verified_subject: the wallet address or verified email from the
    caller's OAuth token (docs/oauth-trust-spec.md), or None for calls
    made before the OAuth mechanism shipped or through a transport that
    doesn't carry one.

    task_id / task_description: optional, and the same values the
    caller may have passed to check_agent_trust for this same task — see
    mvp/trust_lookups.py for how a prior lookup gets correlated.

    detail: optional free text describing what actually happened —
    distinct from evidence_ref, which points AT supporting evidence (a
    transcript URI, a hash) rather than containing an explanation
    itself. Most useful when outcome isn't "completed": an unweighted
    count ("2 disputed") says nothing about what went wrong; this does.
    """
    if outcome not in _VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(_VALID_OUTCOMES)}, got {outcome!r}")

    reported_at = datetime.now(timezone.utc)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO reported_outcomes "
            "(agent_id, outcome, evidence_ref, reported_at, verified_subject, task_id, task_description, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (agent_id, outcome, evidence_ref, reported_at, verified_subject, task_id, task_description, detail),
        )

    preceded_by_lookup = trust_lookups.find_correlated_lookup(
        agent_id, task_id, verified_subject, before=reported_at
    )

    return {
        "agent_id": agent_id,
        "outcome": outcome,
        "evidence_ref": evidence_ref,
        "reported_at": reported_at.isoformat(),
        "verified_subject": verified_subject,
        "task_id": task_id,
        "task_description": task_description,
        "detail": detail,
        "preceded_by_lookup": preceded_by_lookup,
        "stored": True,
    }


def summarize(agent_id: int) -> dict:
    """Unweighted counts, plus the raw detail text of recent non-
    "completed" reports — explicitly labeled self-reported and
    unverified either way. Never call this a score; it isn't one.

    The counts alone ("2 disputed") don't tell a reader what actually
    went wrong; recent_issues (capped at _RECENT_ISSUES_LIMIT, most
    recent first) does, which is the whole point of collecting detail
    text in the first place rather than just an enum.

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
            issue_rows = conn.execute(
                "SELECT outcome, detail, evidence_ref, task_description, reported_at "
                "FROM reported_outcomes WHERE agent_id = %s AND outcome != 'completed' "
                "ORDER BY reported_at DESC LIMIT %s",
                (agent_id, _RECENT_ISSUES_LIMIT),
            ).fetchall()
    except OutcomesStoreUnavailable:
        return {
            "agent_id": agent_id,
            "has_reports": False,
            "counts": {k: 0 for k in sorted(_VALID_OUTCOMES)},
            "recent_issues": [],
            "verified": False,
            "note": "self-reported outcomes store is not configured (DATABASE_URL unset)",
        }

    if not rows:
        return _empty(agent_id)
    counts = {outcome: n for outcome, n in rows}
    recent_issues = [
        {
            "outcome": outcome,
            "detail": detail,
            "evidence_ref": evidence_ref,
            "task_description": task_description,
            "reported_at": reported_at.isoformat(),
        }
        for outcome, detail, evidence_ref, task_description, reported_at in issue_rows
    ]
    return {
        "agent_id": agent_id,
        "has_reports": True,
        "counts": {k: counts.get(k, 0) for k in sorted(_VALID_OUTCOMES)},
        "recent_issues": recent_issues,
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
        "recent_issues": [],
        "verified": False,
        "note": "no self-reported outcomes for this agent yet",
    }
