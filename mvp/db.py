"""Shared Postgres connection helper for mvp/outcomes.py and
mvp/oauth_store.py. Both need the same "fail loudly if DATABASE_URL is
unset" behavior — a local-file fallback would silently reintroduce the
exact ephemeral-storage bug the Postgres migration exists to fix.
"""

import os
from contextlib import contextmanager

import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL")


class DatabaseUnavailable(RuntimeError):
    """Raised when DATABASE_URL isn't configured."""


@contextmanager
def connect():
    if not DATABASE_URL:
        raise DatabaseUnavailable(
            "DATABASE_URL is not set — no persistent store to use. This is "
            "a deployment misconfiguration, not a degraded-but-working state."
        )
    conn = psycopg.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
