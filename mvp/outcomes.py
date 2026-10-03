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
separately, labeled unverified, regardless of caller identity.

Generalized 2026-10-03 (docs/seller-normalization-spec.md) from a bare
ERC-8004 agent_id to any (ecosystem, external_id) pair, resolved via
mvp/sellers.py. `agent_id` is kept as a legacy, nullable column —
populated only for ecosystem="erc8004" calls. summarize() now rolls up
across every identity currently linked to the same seller, not just the
one (ecosystem, external_id) pair asked about — a self-report made
through a ChatGPT-ecosystem identity shows up when later looking up a
confirmed-linked ERC-8004 identity for the same real seller, and vice
versa.
"""

from contextlib import contextmanager
from datetime import datetime, timezone

from mvp import sellers, trust_lookups
from mvp.db import DatabaseUnavailable, connect as _db_connect

# Preserve the existing public name — callers (mvp/lookup.py, tests)
# import OutcomesStoreUnavailable specifically.
OutcomesStoreUnavailable = DatabaseUnavailable

_VALID_OUTCOMES = {"completed", "disputed", "no_response"}

# How many non-"completed" reports' full detail to surface in
# summarize() — unweighted counts alone ("2 disputed") tell a reader
# nothing about what actually went wrong; the raw detail text does.
# Capped, not all of them, so a seller with a long history doesn't blow
# up check_agent_trust's response size.
_RECENT_ISSUES_LIMIT = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reported_outcomes (
    id SERIAL PRIMARY KEY,
    agent_id BIGINT,
    outcome TEXT NOT NULL,
    evidence_ref TEXT,
    reported_at TIMESTAMPTZ NOT NULL,
    verified_subject TEXT,
    task_id TEXT,
    task_description TEXT,
    detail TEXT
)
"""

# agent_id was NOT NULL in the original (ERC-8004-only) version of this
# table — dropped since a non-ERC-8004 identity has no numeric agent_id
# at all. seller_identity_id references mvp/sellers.py's normalization
# layer (docs/seller-normalization-spec.md) and is the real identity
# going forward; agent_id survives only as a legacy convenience column.
_MIGRATE = """
ALTER TABLE reported_outcomes ALTER COLUMN agent_id DROP NOT NULL;
ALTER TABLE reported_outcomes ADD COLUMN IF NOT EXISTS verified_subject TEXT;
ALTER TABLE reported_outcomes ADD COLUMN IF NOT EXISTS task_id TEXT;
ALTER TABLE reported_outcomes ADD COLUMN IF NOT EXISTS task_description TEXT;
ALTER TABLE reported_outcomes ADD COLUMN IF NOT EXISTS detail TEXT;
ALTER TABLE reported_outcomes ADD COLUMN IF NOT EXISTS seller_identity_id INT REFERENCES seller_identities(id)
"""


@contextmanager
def _connect():
    with _db_connect() as conn:
        sellers._ensure_schema(conn)  # seller_identities must exist first — this table's FK depends on it
        conn.execute(_SCHEMA)
        conn.execute(_MIGRATE)
        yield conn


def record_outcome(
    ecosystem: str,
    external_id: str,
    outcome: str,
    evidence_ref: str | None = None,
    verified_subject: str | None = None,
    task_id: str | None = None,
    task_description: str | None = None,
    detail: str | None = None,
    seller_name: str | None = None,
    seller_website: str | None = None,
) -> dict:
    """Store one self-reported outcome. Returns what was stored, not a
    trust score — this never computes or returns anything that looks
    like a verdict, since nothing here has been independently checked.

    ecosystem / external_id: which ecosystem this seller belongs to
    (mvp/ecosystems.py) and that ecosystem's own native ID for it — an
    ERC-8004 agentId (as a string), a ChatGPT "plugins_<hash>", etc.
    Resolved to our own normalized seller record via mvp/sellers.py;
    the caller never needs to know anything about that normalization.

    seller_name / seller_website: optional, used only the first time
    this (ecosystem, external_id) is seen, to seed the seller record —
    primary_website in particular is what enables cross-ecosystem link
    detection (docs/seller-normalization-spec.md). Safe to omit; later
    calls after the first don't need them.

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

    identity_id = sellers.resolve_or_create_identity(
        ecosystem, external_id, canonical_name=seller_name, primary_website=seller_website
    )
    legacy_agent_id = int(external_id) if ecosystem == "erc8004" else None

    reported_at = datetime.now(timezone.utc)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO reported_outcomes "
            "(agent_id, seller_identity_id, outcome, evidence_ref, reported_at, verified_subject, "
            " task_id, task_description, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                legacy_agent_id, identity_id, outcome, evidence_ref, reported_at, verified_subject,
                task_id, task_description, detail,
            ),
        )

    preceded_by_lookup = trust_lookups.find_correlated_lookup(
        ecosystem, external_id, task_id, verified_subject, before=reported_at
    )

    return {
        "ecosystem": ecosystem,
        "external_id": external_id,
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


def summarize(ecosystem: str, external_id: str) -> dict:
    """Unweighted counts, plus the raw detail text of recent non-
    "completed" reports — explicitly labeled self-reported and
    unverified either way. Never call this a score; it isn't one.

    Rolls up across every identity currently linked to the same
    normalized seller (mvp/sellers.py), not just this exact
    (ecosystem, external_id) pair — a confirmed cross-ecosystem link
    immediately makes this reflect reports made through either identity.

    Degrades to an explicit "unavailable" note rather than raising, so a
    missing DATABASE_URL doesn't take down check_agent_trust's read path
    entirely — the reputation lookup should still work even if outcome
    reporting is misconfigured.
    """
    try:
        identity_id = sellers.resolve_or_create_identity(ecosystem, external_id)
        seller_id = sellers.rolled_up_seller_id(identity_id)
        sibling_ids = sellers.sibling_identity_ids(seller_id) if seller_id else [identity_id]

        with _connect() as conn:
            rows = conn.execute(
                "SELECT outcome, COUNT(*) as n FROM reported_outcomes "
                "WHERE seller_identity_id = ANY(%s) GROUP BY outcome",
                (sibling_ids,),
            ).fetchall()
            issue_rows = conn.execute(
                "SELECT outcome, detail, evidence_ref, task_description, reported_at "
                "FROM reported_outcomes WHERE seller_identity_id = ANY(%s) AND outcome != 'completed' "
                "ORDER BY reported_at DESC LIMIT %s",
                (sibling_ids, _RECENT_ISSUES_LIMIT),
            ).fetchall()
    except OutcomesStoreUnavailable:
        return {
            "ecosystem": ecosystem,
            "external_id": external_id,
            "has_reports": False,
            "counts": {k: 0 for k in sorted(_VALID_OUTCOMES)},
            "recent_issues": [],
            "verified": False,
            "note": "self-reported outcomes store is not configured (DATABASE_URL unset)",
        }

    if not rows:
        return _empty(ecosystem, external_id)
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
        "ecosystem": ecosystem,
        "external_id": external_id,
        "has_reports": True,
        "counts": {k: counts.get(k, 0) for k in sorted(_VALID_OUTCOMES)},
        "recent_issues": recent_issues,
        "verified": False,
        "note": (
            "self-reported by integrators via the report_outcome MCP tool, "
            "not independently verified — never blended into the raw or "
            "Sybil-adjusted ERC-8004 numbers above — rolled up across every "
            "ecosystem identity linked to this seller, not just this one"
        ),
    }


def _empty(ecosystem: str, external_id: str) -> dict:
    return {
        "ecosystem": ecosystem,
        "external_id": external_id,
        "has_reports": False,
        "counts": {k: 0 for k in sorted(_VALID_OUTCOMES)},
        "recent_issues": [],
        "verified": False,
        "note": "no self-reported outcomes for this seller yet",
    }
