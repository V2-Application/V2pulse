"""Microsoft Entra ID sign-in (OpenID Connect authorization code flow with PKCE).

The app is a confidential client: the code is exchanged server-side with the client secret,
the ID token is verified against Microsoft's published keys, and the signed-in account is
matched to an active V2Pulse user by email (HRMS email, or an admin-linked one).

Entra only accepts https redirect URIs for anything other than localhost, so production
needs V2Pulse served over HTTPS with a DNS name (see deploy/README.md)."""
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx
import jwt

from ..config import settings

_jwks_cache: dict = {"at": 0, "client": None}


def enabled() -> bool:
    s = settings()
    return bool(s.entra_tenant_id and s.entra_client_id and s.entra_client_secret)


def _authority() -> str:
    return "https://login.microsoftonline.com/%s/v2.0" % settings().entra_tenant_id


def redirect_uri() -> str:
    return settings().base_url.rstrip("/") + "/auth/entra/callback"


def begin(cur, next_url: str) -> str:
    """Store state + PKCE verifier, return the Microsoft authorize URL."""
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    cur.execute("delete from iam.oidc_state where created_at < now() - interval '15 minutes'")
    cur.execute("insert into iam.oidc_state (state, nonce, code_verifier, next_url) values (%s,%s,%s,%s)",
                (state, nonce, verifier, safe_next(next_url)))
    s = settings()
    q = {"client_id": s.entra_client_id, "response_type": "code", "redirect_uri": redirect_uri(),
         "response_mode": "query", "scope": "openid profile email", "state": state, "nonce": nonce,
         "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account"}
    return "https://login.microsoftonline.com/%s/oauth2/v2.0/authorize?%s" % (s.entra_tenant_id, urlencode(q))


def finish(cur, code: str, state: str) -> tuple[dict, str]:
    """Exchange the code, verify the ID token, return (claims, next_url). Raises ValueError."""
    cur.execute("delete from iam.oidc_state where state = %s and created_at > now() - interval '15 minutes' returning *",
                (state,))
    st = cur.fetchone()
    if not st:
        raise ValueError("Sign-in expired or was already used. Please try again.")
    s = settings()
    r = httpx.post("https://login.microsoftonline.com/%s/oauth2/v2.0/token" % s.entra_tenant_id, timeout=20, data={
        "client_id": s.entra_client_id, "client_secret": s.entra_client_secret, "grant_type": "authorization_code",
        "code": code, "redirect_uri": redirect_uri(), "code_verifier": st["code_verifier"]})
    if r.status_code != 200:
        raise ValueError("Microsoft did not accept the sign-in (%s)." % r.json().get("error", r.status_code))
    id_token = r.json()["id_token"]
    claims = verify_id_token(id_token)
    if claims.get("nonce") != st["nonce"]:
        raise ValueError("Sign-in response did not match this request.")
    return claims, st["next_url"]


def verify_id_token(id_token: str) -> dict:
    s = settings()
    if _jwks_cache["client"] is None or time.time() - _jwks_cache["at"] > 86400:
        _jwks_cache["client"] = jwt.PyJWKClient(
            "https://login.microsoftonline.com/%s/discovery/v2.0/keys" % s.entra_tenant_id)
        _jwks_cache["at"] = time.time()
    key = _jwks_cache["client"].get_signing_key_from_jwt(id_token)
    return jwt.decode(id_token, key.key, algorithms=["RS256"], audience=s.entra_client_id, issuer=_authority(),
                      leeway=300)  # servers here have drifted clocks (up to ~190 s seen); allow 5 min


def claim_emails(claims: dict) -> list[str]:
    out = []
    for k in ("email", "preferred_username", "upn"):
        v = (claims.get(k) or "").strip().lower()
        if "@" in v and v not in out:
            out.append(v)
    return out


def safe_next(url: str | None) -> str:
    """Only same-site relative paths, so the login cannot be used as an open redirect."""
    if not url or not url.startswith("/") or url.startswith("//") or "\\" in url:
        return "/"
    return url
