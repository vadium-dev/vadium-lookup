"""Postgres-backed storage for the OAuth 2.1 authorization server in
front of the MCP server's tools (docs/oauth-trust-spec.md). Same
database as mvp/outcomes.py, separate tables.
"""

import secrets
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from mvp.db import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    metadata JSONB NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_pending_authorizations (
    request_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
    redirect_uri TEXT NOT NULL,
    redirect_uri_provided_explicitly BOOLEAN NOT NULL,
    scopes TEXT[] NOT NULL,
    state TEXT,
    code_challenge TEXT NOT NULL,
    resource TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
    code TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
    redirect_uri TEXT NOT NULL,
    redirect_uri_provided_explicitly BOOLEAN NOT NULL,
    scopes TEXT[] NOT NULL,
    code_challenge TEXT NOT NULL,
    resource TEXT,
    subject TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_access_tokens (
    token TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
    scopes TEXT[] NOT NULL,
    resource TEXT,
    subject TEXT NOT NULL,
    expires_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
    token TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
    scopes TEXT[] NOT NULL,
    resource TEXT,
    subject TEXT NOT NULL,
    expires_at TIMESTAMPTZ
);
"""

_PENDING_TTL = timedelta(minutes=10)
_AUTH_CODE_TTL = timedelta(minutes=5)
_ACCESS_TOKEN_TTL = timedelta(hours=1)
_REFRESH_TOKEN_TTL = timedelta(days=90)


def _ensure_schema(conn) -> None:
    conn.execute(_SCHEMA)


# ---- clients (RFC 7591 dynamic client registration) ----
#
# Stored as the full serialized OAuthClientInformationFull, not one
# column per field — found by testing the real flow end-to-end: hand-
# picking fields (first just redirect_uris/client_name, then adding
# scope) kept silently dropping whatever field wasn't picked yet
# (scope, then token_endpoint_auth_method), each surfacing as a
# different cryptic rejection at /authorize or /token. Storing the
# whole object means every field this SDK's client model has — now or
# in a future version — round-trips correctly without needing another
# column added each time a new rejection shows up.

def get_client(client_id: str) -> dict | None:
    """Returns the full stored OAuthClientInformationFull as a dict
    (metadata already includes client_id — it was dumped from the full
    model at registration time), or None if not found.
    """
    with connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT metadata FROM oauth_clients WHERE client_id = %s",
            (client_id,),
        ).fetchone()
    return row[0] if row else None


def register_client(client_id: str, metadata: dict) -> None:
    with connect() as conn:
        _ensure_schema(conn)
        conn.execute(
            "INSERT INTO oauth_clients (client_id, metadata, registered_at) VALUES (%s, %s, %s)",
            (client_id, Jsonb(metadata), datetime.now(timezone.utc)),
        )


# ---- pending authorizations: the gap between /authorize and the user
# completing the wallet-signature step on our own verify page ----

def create_pending_authorization(
    client_id: str,
    redirect_uri: str,
    redirect_uri_provided_explicitly: bool,
    scopes: list[str],
    state: str | None,
    code_challenge: str,
    resource: str | None,
) -> str:
    request_id = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    with connect() as conn:
        _ensure_schema(conn)
        conn.execute(
            "INSERT INTO oauth_pending_authorizations "
            "(request_id, client_id, redirect_uri, redirect_uri_provided_explicitly, scopes, "
            " state, code_challenge, resource, created_at, expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                request_id, client_id, redirect_uri, redirect_uri_provided_explicitly, scopes,
                state, code_challenge, resource, now, now + _PENDING_TTL,
            ),
        )
    return request_id


def get_pending_authorization(request_id: str) -> dict | None:
    with connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT request_id, client_id, redirect_uri, redirect_uri_provided_explicitly, scopes, "
            "       state, code_challenge, resource, expires_at "
            "FROM oauth_pending_authorizations WHERE request_id = %s",
            (request_id,),
        ).fetchone()
    if not row:
        return None
    keys = [
        "request_id", "client_id", "redirect_uri", "redirect_uri_provided_explicitly", "scopes",
        "state", "code_challenge", "resource", "expires_at",
    ]
    data = dict(zip(keys, row))
    if data["expires_at"] < datetime.now(timezone.utc):
        return None
    return data


def delete_pending_authorization(request_id: str) -> None:
    with connect() as conn:
        _ensure_schema(conn)
        conn.execute("DELETE FROM oauth_pending_authorizations WHERE request_id = %s", (request_id,))


# ---- authorization codes ----

def create_authorization_code(
    client_id: str,
    redirect_uri: str,
    redirect_uri_provided_explicitly: bool,
    scopes: list[str],
    code_challenge: str,
    resource: str | None,
    subject: str,
) -> str:
    code = secrets.token_urlsafe(32)
    with connect() as conn:
        _ensure_schema(conn)
        conn.execute(
            "INSERT INTO oauth_authorization_codes "
            "(code, client_id, redirect_uri, redirect_uri_provided_explicitly, scopes, "
            " code_challenge, resource, subject, expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                code, client_id, redirect_uri, redirect_uri_provided_explicitly, scopes,
                code_challenge, resource, subject, datetime.now(timezone.utc) + _AUTH_CODE_TTL,
            ),
        )
    return code


def load_authorization_code(client_id: str, code: str) -> dict | None:
    with connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT code, client_id, redirect_uri, redirect_uri_provided_explicitly, scopes, "
            "       code_challenge, resource, subject, expires_at "
            "FROM oauth_authorization_codes WHERE code = %s AND client_id = %s",
            (code, client_id),
        ).fetchone()
    if not row:
        return None
    keys = [
        "code", "client_id", "redirect_uri", "redirect_uri_provided_explicitly", "scopes",
        "code_challenge", "resource", "subject", "expires_at",
    ]
    data = dict(zip(keys, row))
    if data["expires_at"] < datetime.now(timezone.utc):
        return None
    return data


def consume_authorization_code(code: str) -> None:
    """Delete-on-use — an authorization code is single-use per RFC 6749 §4.1.2."""
    with connect() as conn:
        _ensure_schema(conn)
        conn.execute("DELETE FROM oauth_authorization_codes WHERE code = %s", (code,))


# ---- access / refresh tokens ----

def create_tokens(client_id: str, scopes: list[str], resource: str | None, subject: str) -> dict:
    access_token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    access_expires = now + _ACCESS_TOKEN_TTL
    with connect() as conn:
        _ensure_schema(conn)
        conn.execute(
            "INSERT INTO oauth_access_tokens (token, client_id, scopes, resource, subject, expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (access_token, client_id, scopes, resource, subject, access_expires),
        )
        conn.execute(
            "INSERT INTO oauth_refresh_tokens (token, client_id, scopes, resource, subject, expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (refresh_token, client_id, scopes, resource, subject, now + _REFRESH_TOKEN_TTL),
        )
    return {"access_token": access_token, "refresh_token": refresh_token, "expires_at": access_expires}


def load_access_token(token: str) -> dict | None:
    with connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT token, client_id, scopes, resource, subject, expires_at "
            "FROM oauth_access_tokens WHERE token = %s",
            (token,),
        ).fetchone()
    if not row:
        return None
    keys = ["token", "client_id", "scopes", "resource", "subject", "expires_at"]
    data = dict(zip(keys, row))
    if data["expires_at"] and data["expires_at"] < datetime.now(timezone.utc):
        return None
    return data


def load_refresh_token(client_id: str, token: str) -> dict | None:
    with connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT token, client_id, scopes, resource, subject, expires_at "
            "FROM oauth_refresh_tokens WHERE token = %s AND client_id = %s",
            (token, client_id),
        ).fetchone()
    if not row:
        return None
    keys = ["token", "client_id", "scopes", "resource", "subject", "expires_at"]
    data = dict(zip(keys, row))
    if data["expires_at"] and data["expires_at"] < datetime.now(timezone.utc):
        return None
    return data


def delete_refresh_token(token: str) -> None:
    with connect() as conn:
        _ensure_schema(conn)
        conn.execute("DELETE FROM oauth_refresh_tokens WHERE token = %s", (token,))


def revoke_token(token: str) -> None:
    """Deletes from both tables regardless of which kind `token` is —
    matches OAuthAuthorizationServerProvider.revoke_token's contract
    (revoke the pair, whichever one was handed in).
    """
    with connect() as conn:
        _ensure_schema(conn)
        conn.execute("DELETE FROM oauth_access_tokens WHERE token = %s", (token,))
        conn.execute("DELETE FROM oauth_refresh_tokens WHERE token = %s", (token,))
