"""Read-only summary of our own Phase 2 ledger for one agent identity.

Kept deliberately separate from `ledger/` (which is the write/ingest
path) — this module only ever does SELECTs, never touches ingest,
anchoring, or signing, so the MVP endpoint can't accidentally become a
write path into the evidence ledger.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "ledger.db"


def summarize(agent_identity: str) -> dict:
    """Everything we ourselves know about this ERC-8004 agentId.

    `agent_identity` matches `agent_versions.agent_identity` — see
    schema.sql: "ERC-8004 agentId once registered, else NULL". Returns a
    zeroed summary (never an error) when we simply have no history yet —
    that's an expected, common case for an MVP with near-zero volume, not
    a failure.
    """
    if not DB_PATH.exists():
        return _empty(agent_identity, note="no local ledger database found")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.row_factory = sqlite3.Row
        versions = conn.execute(
            "SELECT version_id, model_id, resolution, created_at "
            "FROM agent_versions WHERE agent_identity = ? ORDER BY created_at ASC",
            (agent_identity,),
        ).fetchall()
        if not versions:
            return _empty(agent_identity)

        version_ids = [v["version_id"] for v in versions]
        placeholders = ",".join("?" * len(version_ids))
        op_rows = conn.execute(
            f"SELECT operation_type, status, started_at FROM operations "
            f"WHERE version_id IN ({placeholders})",
            version_ids,
        ).fetchall()

        anchored = conn.execute(
            f"""SELECT COUNT(DISTINCT e.epoch_id) FROM epochs e
                JOIN epoch_members m ON m.epoch_id = e.epoch_id
                JOIN operations o ON o.op_id = m.op_id
                WHERE o.version_id IN ({placeholders}) AND e.anchor_tx_hash IS NOT NULL""",
            version_ids,
        ).fetchone()[0]

        op_types = sorted({r["operation_type"] for r in op_rows})
        error_count = sum(1 for r in op_rows if r["status"] == "error")

        return {
            "agent_identity": agent_identity,
            "has_history": True,
            "operation_count": len(op_rows),
            "distinct_operation_types": op_types,
            "error_count": error_count,
            "first_seen": versions[0]["created_at"],
            "anchored_epochs_touched": anchored,
            "source": "vadium-native (Phase 2 evidence ledger)",
        }
    finally:
        conn.close()


def _empty(agent_identity: str, note: str | None = None) -> dict:
    return {
        "agent_identity": agent_identity,
        "has_history": False,
        "operation_count": 0,
        "distinct_operation_types": [],
        "error_count": 0,
        "first_seen": None,
        "anchored_epochs_touched": 0,
        "source": "vadium-native (Phase 2 evidence ledger)",
        "note": note or "no operations recorded for this identity yet",
    }
