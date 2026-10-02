"""OAuthAuthorizationServerProvider for vadium-lookup (docs/oauth-trust-spec.md).

At /authorize, instead of redirecting to a real third-party IdP (the
SDK's documented pattern for this hook — see
mcp.server.auth.provider.OAuthAuthorizationServerProvider.authorize's
docstring), we redirect to our own wallet-signature verification page.
The resulting token's `subject` is the wallet address that signed —
see mvp/identity_proof.py for why there's deliberately no on-chain
ownership check behind that.
"""

import json
from datetime import datetime, timezone

from pydantic import AnyUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from mvp import identity_proof, oauth_store


class VadiumOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self, verify_page_url: str):
        self.verify_page_url = verify_page_url.rstrip("/")

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        row = oauth_store.get_client(client_id)
        if not row:
            return None
        return OAuthClientInformationFull.model_validate(row)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.redirect_uris:
            raise RegistrationError(
                error="invalid_redirect_uri",
                error_description="at least one redirect_uri is required",
            )
        # The full model, not hand-picked fields — see the module-level
        # comment on oauth_store's client functions for why.
        oauth_store.register_client(
            client_id=client_info.client_id,
            metadata=client_info.model_dump(mode="json"),
        )

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        request_id = oauth_store.create_pending_authorization(
            client_id=client.client_id,
            redirect_uri=str(params.redirect_uri),
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            scopes=params.scopes or [],
            state=params.state,
            code_challenge=params.code_challenge,
            resource=params.resource,
        )
        return f"{self.verify_page_url}?request_id={request_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        row = oauth_store.load_authorization_code(client.client_id, authorization_code)
        if not row:
            return None
        return AuthorizationCode(
            code=row["code"],
            scopes=row["scopes"],
            expires_at=row["expires_at"].timestamp(),
            client_id=row["client_id"],
            code_challenge=row["code_challenge"],
            redirect_uri=AnyUrl(row["redirect_uri"]),
            redirect_uri_provided_explicitly=row["redirect_uri_provided_explicitly"],
            resource=row["resource"],
            subject=row["subject"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        oauth_store.consume_authorization_code(authorization_code.code)
        tokens = oauth_store.create_tokens(
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            resource=authorization_code.resource,
            subject=authorization_code.subject,
        )
        expires_in = int((tokens["expires_at"] - datetime.now(timezone.utc)).total_seconds())
        return OAuthToken(
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            expires_in=expires_in,
            scope=" ".join(authorization_code.scopes),
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        row = oauth_store.load_refresh_token(client.client_id, refresh_token)
        if not row:
            return None
        return RefreshToken(
            token=row["token"],
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=int(row["expires_at"].timestamp()) if row["expires_at"] else None,
            resource=row["resource"],
            subject=row["subject"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        oauth_store.delete_refresh_token(refresh_token.token)
        granted_scopes = scopes or refresh_token.scopes
        tokens = oauth_store.create_tokens(
            client_id=client.client_id,
            scopes=granted_scopes,
            resource=refresh_token.resource,
            subject=refresh_token.subject,
        )
        return OAuthToken(
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            scope=" ".join(granted_scopes),
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = oauth_store.load_access_token(token)
        if not row:
            return None
        return AccessToken(
            token=row["token"],
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=int(row["expires_at"].timestamp()) if row["expires_at"] else None,
            resource=row["resource"],
            subject=row["subject"],
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        oauth_store.revoke_token(token.token)


# ============================================================
# The human-facing half of /authorize: a page asking the caller to sign
# a challenge with a wallet, and the endpoint that verifies it. These
# are plain Starlette routes wired into mvp/app.py — not part of the
# OAuthAuthorizationServerProvider protocol itself, which only hands
# back a URL to redirect to (see authorize() above).
# ============================================================

def _verify_page_html(request_id: str, message: str) -> str:
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Connect your wallet — Vadium</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: 560px; margin: 60px auto; padding: 0 20px; color: #111; line-height: 1.5; }}
  button {{ font-size: 16px; padding: 10px 22px; cursor: pointer; border-radius: 6px;
            border: 1px solid #222; background: #111; color: #fff; }}
  button:disabled {{ opacity: 0.5; cursor: default; }}
  #status {{ margin-top: 18px; color: #555; min-height: 1.5em; }}
  code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 4px; font-size: 13px; }}
</style>
</head>
<body>
  <h2>Connect your wallet</h2>
  <p>Vadium ties each self-reported outcome to a wallet address so
  reports aren't anonymous. Signing is free — it does not submit a
  transaction or cost any gas.</p>
  <button id="connect">Connect wallet &amp; sign</button>
  <div id="status"></div>
  <script>
    const requestId = {json.dumps(request_id)};
    const message = {json.dumps(message)};
    const btn = document.getElementById('connect');
    const statusEl = document.getElementById('status');
    btn.onclick = async () => {{
      if (!window.ethereum) {{
        statusEl.textContent = 'No wallet extension found. Install MetaMask or another injected wallet, then reload this page.';
        return;
      }}
      btn.disabled = true;
      try {{
        statusEl.textContent = 'Requesting account access…';
        const accounts = await window.ethereum.request({{ method: 'eth_requestAccounts' }});
        const address = accounts[0];
        statusEl.textContent = 'Requesting signature…';
        const signature = await window.ethereum.request({{
          method: 'personal_sign',
          params: [message, address],
        }});
        statusEl.textContent = 'Verifying…';
        const resp = await fetch('/oauth/verify-identity/submit', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ request_id: requestId, address, signature }}),
        }});
        const data = await resp.json();
        if (data.redirect_url) {{
          statusEl.textContent = 'Verified — redirecting…';
          window.location.href = data.redirect_url;
        }} else {{
          statusEl.textContent = data.error || 'Verification failed.';
          btn.disabled = false;
        }}
      }} catch (err) {{
        statusEl.textContent = 'Error: ' + (err && err.message ? err.message : String(err));
        btn.disabled = false;
      }}
    }};
  </script>
</body>
</html>"""


async def verify_identity_page(request: Request) -> HTMLResponse:
    request_id = request.query_params.get("request_id", "")
    pending = oauth_store.get_pending_authorization(request_id)
    if not pending:
        return HTMLResponse(
            "<p>This verification link has expired or is invalid. "
            "Please restart the connection from the app you were connecting.</p>",
            status_code=400,
        )
    message = identity_proof.challenge_message(request_id)
    return HTMLResponse(_verify_page_html(request_id, message))


async def verify_identity_submit(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Malformed request."}, status_code=400)

    request_id = body.get("request_id", "")
    address = body.get("address", "")
    signature = body.get("signature", "")
    if not (request_id and address and signature):
        return JSONResponse({"error": "Missing request_id, address, or signature."}, status_code=400)

    pending = oauth_store.get_pending_authorization(request_id)
    if not pending:
        return JSONResponse(
            {"error": "This verification link has expired. Please restart the connection."},
            status_code=400,
        )

    try:
        recovered = identity_proof.recover_signer(request_id, signature)
    except Exception:
        return JSONResponse({"error": "Could not verify that signature."}, status_code=400)

    if recovered.lower() != address.lower():
        return JSONResponse({"error": "Signature does not match the provided address."}, status_code=400)

    code = oauth_store.create_authorization_code(
        client_id=pending["client_id"],
        redirect_uri=pending["redirect_uri"],
        redirect_uri_provided_explicitly=pending["redirect_uri_provided_explicitly"],
        scopes=pending["scopes"],
        code_challenge=pending["code_challenge"],
        resource=pending["resource"],
        subject=recovered,
    )
    oauth_store.delete_pending_authorization(request_id)

    redirect_url = construct_redirect_uri(pending["redirect_uri"], code=code, state=pending["state"])
    return JSONResponse({"redirect_url": redirect_url})
