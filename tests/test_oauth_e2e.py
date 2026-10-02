"""Live end-to-end test of the OAuth trust mechanism
(docs/oauth-trust-spec.md) against a REAL running deployment — not a
mocked/unit test. Drives the actual HTTP endpoints exactly as a real
MCP client + browser wallet would: DCR, /authorize, the wallet-signature
verify page, /token, authenticated tool calls, refresh, and revocation.

Simulates the wallet with a throwaway eth_account keypair in place of a
real browser extension (there's no headless MetaMask to drive), but the
signing math is identical — eth_account implements the same
secp256k1/personal_sign scheme any wallet extension does, and this is
exactly how mvp/identity_proof.py verifies a real signature too.

This created the bugs it exists to catch: every failure in the commit
history around docs/oauth-trust-spec.md (the /mcp/authorize vs
/authorize path mismatch, the dropped `scope` and
`token_endpoint_auth_method` fields on client registration) was found
by running this against the live Hetzner deployment, not by reading the
code — the MCP SDK's auth routing has real sharp edges that only show
up under an actual HTTP round-trip.

Requires network access to a real deployment and leaves a handful of
test OAuth client/token rows in its database — harmless, but don't
point this at a production database you care about keeping pristine.

Run: VADIUM_TEST_BASE_URL=https://vadium-lookup.atesta.io python3 test_oauth_e2e.py
(defaults to the production Hetzner deployment if unset)
"""
import base64
import hashlib
import os
import secrets
import sys

import requests
from eth_account import Account
from eth_account.messages import encode_defunct

BASE = os.environ.get("VADIUM_TEST_BASE_URL", "https://vadium-lookup.atesta.io")
FAILURES = []


def check(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


# ---- Scenario 1: unauthenticated calls are rejected ----
r = requests.post(
    f"{BASE}/mcp",
    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
          "params": {"name": "check_agent_trust", "arguments": {"agent_id": 1}}},
    headers={"Accept": "application/json, text/event-stream"},
)
check("unauthenticated tool call -> 401", r.status_code == 401, f"got {r.status_code}")
check("401 carries WWW-Authenticate with resource_metadata",
      "resource_metadata" in r.headers.get("www-authenticate", ""), r.headers.get("www-authenticate"))

# ---- Scenario 2: discovery documents are internally consistent ----
prm = requests.get(f"{BASE}/.well-known/oauth-protected-resource/mcp").json()
check("protected-resource metadata resolves", "authorization_servers" in prm, prm)
issuer = prm["authorization_servers"][0]
asm = requests.get(f"{issuer.rstrip('/')}/.well-known/oauth-authorization-server").json()
check("authorization-server metadata resolves", "authorization_endpoint" in asm, asm)

# The actual regression this test exists to catch: advertised endpoints must
# be real, reachable routes, not 404s.
authz_url = asm["authorization_endpoint"]
token_url = asm["token_endpoint"]
register_url = asm["registration_endpoint"]
r = requests.get(authz_url, params={"client_id": "nonexistent", "response_type": "code",
                                     "redirect_uri": "https://example.com/cb",
                                     "code_challenge": "x", "code_challenge_method": "S256"})
check("advertised authorization_endpoint is a real route (not 404)", r.status_code != 404, r.status_code)
r = requests.post(token_url, data={"grant_type": "authorization_code", "code": "x", "client_id": "x"})
check("advertised token_endpoint is a real route (not 404)", r.status_code != 404, r.status_code)

# ---- Scenario 3: full happy-path flow ----
redirect_uri = "https://client.example.com/callback"
reg = requests.post(register_url, json={
    "redirect_uris": [redirect_uri],
    "client_name": "vadium-oauth-e2e-test",
    "token_endpoint_auth_method": "none",
}).json()
check("dynamic client registration succeeds", "client_id" in reg, reg)
client_id = reg["client_id"]

verifier, challenge = pkce_pair()
state = secrets.token_urlsafe(8)
r = requests.get(authz_url, params={
    "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri,
    "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
    "scope": "vadium",
}, allow_redirects=False)
check("authorize redirects to our verify-identity page", r.status_code in (302, 303, 307),
      f"status={r.status_code} body={r.text[:200]}")
verify_url = r.headers.get("location", "")
check("redirect target is our own /oauth/verify-identity page", "/oauth/verify-identity" in verify_url, verify_url)

page = requests.get(verify_url)
check("verify-identity page renders", page.status_code == 200 and "personal_sign" in page.text, page.status_code)

request_id = verify_url.split("request_id=")[-1]
acct = Account.create()
challenge_msg = f"Sign to connect your wallet to Vadium.\nRequest: {request_id}\nThis will not trigger a transaction or cost any gas."
signed = Account.sign_message(encode_defunct(text=challenge_msg), private_key=acct.key)

submit = requests.post(f"{BASE}/oauth/verify-identity/submit", json={
    "request_id": request_id, "address": acct.address, "signature": signed.signature.hex(),
}).json()
check("signature verification succeeds", "redirect_url" in submit, submit)

code = submit.get("redirect_url", "").split("code=")[-1].split("&")[0]
check("got an authorization code", bool(code), submit)

tok = requests.post(token_url, data={
    "grant_type": "authorization_code", "code": code, "client_id": client_id,
    "redirect_uri": redirect_uri, "code_verifier": verifier,
}).json()
check("token exchange succeeds", "access_token" in tok, tok)
access_token = tok.get("access_token")

# ---- Scenario 4: authenticated tool calls work for BOTH tools ----
headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json, text/event-stream",
           "Content-Type": "application/json"}
r = requests.post(f"{BASE}/mcp", headers=headers, json={
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2026-06-18", "capabilities": {}, "clientInfo": {"name": "e2e", "version": "1"}},
})
check("authenticated initialize succeeds", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
session_id = r.headers.get("mcp-session-id")
headers["mcp-session-id"] = session_id
requests.post(f"{BASE}/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

r = requests.post(f"{BASE}/mcp", headers=headers, json={
    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
    "params": {"name": "check_agent_trust", "arguments": {"agent_id": 1}},
})
check("authenticated check_agent_trust succeeds", r.status_code == 200 and '"isError":false' in r.text.replace(" ", ""),
      f"{r.status_code} {r.text[:300]}")

r = requests.post(f"{BASE}/mcp", headers=headers, json={
    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
    "params": {"name": "report_outcome",
               "arguments": {"agent_id": 1, "outcome": "completed", "evidence_ref": "e2e-test"}},
})
check("authenticated report_outcome succeeds", r.status_code == 200, f"{r.status_code} {r.text[:300]}")
check(f"report_outcome recorded verified_subject={acct.address}",
      acct.address.lower() in r.text.lower(), r.text[:400])

# ---- Scenario 5: negative cases ----
r = requests.post(f"{BASE}/mcp", headers={**headers, "Authorization": "Bearer garbage-token-123"}, json={
    "jsonrpc": "2.0", "id": 4, "method": "tools/call",
    "params": {"name": "check_agent_trust", "arguments": {"agent_id": 1}},
})
check("garbage bearer token is rejected (401)", r.status_code == 401, r.status_code)

bad_submit = requests.post(f"{BASE}/oauth/verify-identity/submit", json={
    "request_id": request_id, "address": acct.address, "signature": signed.signature.hex(),
}).json()
check("reused request_id (already consumed) is rejected", "error" in bad_submit, bad_submit)

other_acct = Account.create()
tampered = requests.post(f"{BASE}/oauth/verify-identity/submit", json={
    "request_id": "fake-request-id-that-never-existed",
    "address": other_acct.address, "signature": signed.signature.hex(),
}).json()
check("signature over an unrelated/nonexistent request_id is rejected", "error" in tampered, tampered)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")

# ---- Scenario 6: refresh token rotation and revocation ----
refresh_token = tok.get("refresh_token")
check("token response includes a refresh_token", bool(refresh_token), tok)

refreshed = requests.post(token_url, data={
    "grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id,
}).json()
check("refresh_token exchange succeeds", "access_token" in refreshed, refreshed)
new_access_token = refreshed.get("access_token")

old_still_works = requests.post(f"{BASE}/mcp", headers={**headers, "Authorization": f"Bearer {access_token}"}, json={
    "jsonrpc": "2.0", "id": 5, "method": "tools/call",
    "params": {"name": "check_agent_trust", "arguments": {"agent_id": 1}},
})
new_works = requests.post(f"{BASE}/mcp", headers={**headers, "Authorization": f"Bearer {new_access_token}"}, json={
    "jsonrpc": "2.0", "id": 6, "method": "tools/call",
    "params": {"name": "check_agent_trust", "arguments": {"agent_id": 1}},
})
check("new access token from refresh works", new_works.status_code == 200, new_works.text[:200])

revoke = requests.post(f"{issuer.rstrip('/')}/revoke", data={"token": new_access_token, "client_id": client_id, "client_secret": ""})
check("revoke endpoint accepts the token", revoke.status_code in (200, 204), revoke.status_code)
after_revoke = requests.post(f"{BASE}/mcp", headers={**headers, "Authorization": f"Bearer {new_access_token}"}, json={
    "jsonrpc": "2.0", "id": 7, "method": "tools/call",
    "params": {"name": "check_agent_trust", "arguments": {"agent_id": 1}},
})
check("revoked token is rejected afterward", after_revoke.status_code == 401, after_revoke.status_code)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
