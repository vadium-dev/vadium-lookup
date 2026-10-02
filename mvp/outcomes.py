"""Self-reported transaction outcomes from MCP integrators.

Deliberately NOT part of ledger.db's schema or own_ledger.py's read path
— own_ledger.py is documented as a read-only consumer of the Phase 2
evidence ledger (populated by a separate, anchored ingest process this
repo doesn't own), and this module must not blur that boundary. This is
a new, small, separate store this repo fully owns, for a channel with no
on-chain identity to verify against.

Never merged into check_agent_trust's raw/Sybil-adjusted ERC-8004
numbers (see docs/mcp-server-spec.md's "Anti-gaming" section) — a
self-reported outcome is shown separately and labeled as unverified,
because there is currently no caller-identity signal to weight it by,
not because the distinction doesn't matter.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "mcp_outcomes.db"

_VALID_OUTCOMES = {"completed", "disputed", "no_response"}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS reported_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            evidence_ref TEXT,
            reported_at TEXT NOT NULL
        )"""
    )
    return conn


def record_outcome(agent_id: int, outcome: str, evidence_ref: str | None = None) -> dict:
    """Store one self-reported outcome. Returns what was stored, not a
    trust score — this never computes or returns anything that looks
    like a verdict, since nothing here has been independently checked.
    """
    if outcome not in _VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(_VALID_OUTCOMES)}, got {outcome!r}")

    reported_at = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO reported_outcomes (agent_id, outcome, evidence_ref, reported_at) "
            "VALUES (?, ?, ?, ?)",
            (agent_id, outcome, evidence_ref, reported_at),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "agent_id": agent_id,
        "outcome": outcome,
        "evidence_ref": evidence_ref,
        "reported_at": reported_at,
        "stored": True,
    }


def summarize(agent_id: int) -> dict:
    """Unweighted counts only — explicitly labeled self-reported and
    unverified. Never call this a score; it isn't one.
    """
    if not DB_PATH.exists():
        return _empty(agent_id)

    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT outcome, COUNT(*) as n FROM reported_outcomes "
            "WHERE agent_id = ? GROUP BY outcome",
            (agent_id,),
        ).fetchall()
        if not rows:
            return _empty(agent_id)
        counts = {r["outcome"]: r["n"] for r in rows}
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
    finally:
        conn.close()


def _empty(agent_id: int) -> dict:
    return {
        "agent_id": agent_id,
        "has_reports": False,
        "counts": {k: 0 for k in sorted(_VALID_OUTCOMES)},
        "verified": False,
        "note": "no self-reported outcomes for this agent yet",
    }
