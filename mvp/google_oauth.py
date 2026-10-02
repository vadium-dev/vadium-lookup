"""Google OAuth/OIDC as the identity-proofing step at /authorize
(docs/oauth-trust-spec.md, "Google Sign-In" revision). This is the real
instance of the pattern mcp.server.auth.provider.OAuthAuthorizationServerProvider
.authorize()'s own docstring describes — redirect to a genuine third-party
provider, not a workaround — so Google verifies the email for us instead
of us running our own verification code/magic-link system.

Requires GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET /
GOOGLE_OAUTH_REDIRECT_URI to be set — see docs/oauth-trust-spec.md for
where those come from. Deliberately uses only `requests` against
Google's own tokeninfo endpoint for verification rather than adding a
JWT/JWKS-verification dependency: Google documents tokeninfo as a
supported way for a server-side app to validate an id_token it just
received directly from Google over TLS (not one a client handed it
secondhand, which is the case tokeninfo's docs warn needs JWKS
verification instead) — no cryptographic parsing needed on our side.
"""

import os
from urllib.parse import urlencode

import requests

GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
# `or` rather than os.environ.get's own default param — found by testing
# the real deployment: docker-compose's `${GOOGLE_OAUTH_REDIRECT_URI}`
# (no `:-default` in the compose file) sets this to an empty string,
# not absent, when .env doesn't define it. os.environ.get(key, default)
# only falls back on an ABSENT key, so an empty string silently won
# over the intended default and broke the redirect_uri sent to Google.
GOOGLE_OAUTH_REDIRECT_URI = (
    os.environ.get("GOOGLE_OAUTH_REDIRECT_URI") or "https://vadium-lookup.atesta.io/oauth/google/callback"
)

_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


class GoogleOAuthNotConfigured(RuntimeError):
    """GOOGLE_OAUTH_CLIENT_ID/SECRET aren't set."""


class GoogleVerificationFailed(RuntimeError):
    """Google's token exchange or tokeninfo check didn't produce a
    verified email — distinct from "not configured" so callers can
    show a different message for each.
    """


def _require_configured() -> None:
    if not (GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET):
        raise GoogleOAuthNotConfigured(
            "GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET are not set — "
            "see docs/oauth-trust-spec.md for how to obtain them."
        )


def build_authorize_url(state: str) -> str:
    """`state` carries our own request_id through Google's redirect —
    Google echoes it back verbatim on the callback, which is how we
    correlate the callback to the right pending_authorization row.
    """
    _require_configured()
    params = {
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        # Google would otherwise show an account picker + consent every
        # time; "select_account" (not "consent") keeps re-connecting
        # quick for a returning user without forcing a fresh consent
        # grant each time, while still letting them switch accounts.
        "prompt": "select_account",
    }
    return f"{_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_email(code: str) -> str:
    """Exchanges Google's authorization code for a verified email
    address. Raises GoogleVerificationFailed on anything short of a
    fully verified email — never returns an unverified one.
    """
    _require_configured()
    token_resp = requests.post(
        _TOKEN_URL,
        data={
            "code": code,
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    if token_resp.status_code != 200:
        raise GoogleVerificationFailed(f"Google token exchange failed: {token_resp.text[:300]}")
    id_token = token_resp.json().get("id_token")
    if not id_token:
        raise GoogleVerificationFailed("Google's token response had no id_token")

    info_resp = requests.get(_TOKENINFO_URL, params={"id_token": id_token}, timeout=10)
    if info_resp.status_code != 200:
        raise GoogleVerificationFailed(f"Google tokeninfo check failed: {info_resp.text[:300]}")
    claims = info_resp.json()

    if claims.get("aud") != GOOGLE_OAUTH_CLIENT_ID:
        # tokeninfo validates the token's signature/issuer/expiry for us, but
        # NOT that it was issued to *this* client — that's our own check to
        # make, same as any OIDC relying party must do.
        raise GoogleVerificationFailed("id_token audience does not match our client_id")
    if claims.get("email_verified") != "true":
        raise GoogleVerificationFailed("Google account email is not verified")
    email = claims.get("email")
    if not email:
        raise GoogleVerificationFailed("Google did not return an email claim")
    return email
