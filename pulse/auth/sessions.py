"""Server-side sessions. The browser holds only a random token (HTTP-only cookie); the
database stores its SHA-256, so a leaked database cannot be replayed as sessions.

Lifetimes: sliding idle timeout (10 h; 4 h for elevated roles) and an absolute cap (7 days).
CSRF: double-submit token. `pulse_csrf` cookie (readable by JS) must equal the
X-CSRF-Token header on every mutating request."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from ..config import settings

COOKIE = "pulse_sid"
CSRF_COOKIE = "pulse_csrf"


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def create(cur, user_id: str, provider: str, ip: str | None, ua: str | None, elevated: bool = False) -> str:
    s = settings()
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    idle = timedelta(hours=s.admin_session_hours if elevated else s.session_idle_hours)
    cur.execute(
        "insert into iam.session (token_hash, user_id, provider, expires_at, absolute_expires_at, ip, user_agent, idle_seconds)"
        " values (%s,%s,%s,%s,%s,%s,%s,%s)",
        (_hash(token), user_id, provider, now + idle, now + timedelta(days=s.session_absolute_days), ip, (ua or "")[:400],
         int(idle.total_seconds())))
    cur.execute("update iam.users set last_login_at = now() where id = %s", (user_id,))
    return token


def lookup(cur, token: str | None):
    """Return the live session row (and slide its expiry), or None."""
    if not token:
        return None
    cur.execute(
        "select s.*, u.status from iam.session s join iam.users u on u.id = s.user_id"
        " where s.token_hash = %s and s.revoked_at is null and s.expires_at > now() and s.absolute_expires_at > now()",
        (_hash(token),))
    row = cur.fetchone()
    if not row or row["status"] != "active":
        return None
    # Only write when the last touch is over a minute old: one session update per minute, not per request.
    if (datetime.now(timezone.utc) - row["last_seen_at"]).total_seconds() > 60:
        cur.execute("update iam.session set last_seen_at = now(),"
                    " expires_at = least(now() + make_interval(secs => idle_seconds), absolute_expires_at) where id = %s",
                    (row["id"],))
    return row


def revoke(cur, token: str | None):
    if token:
        cur.execute("update iam.session set revoked_at = now() where token_hash = %s and revoked_at is null", (_hash(token),))


def revoke_user(cur, user_id: str):
    cur.execute("update iam.session set revoked_at = now() where user_id = %s and revoked_at is null", (user_id,))


def set_cookies(resp, token: str):
    s = settings()
    max_age = int(s.session_absolute_days * 86400)
    resp.set_cookie(COOKIE, token, max_age=max_age, httponly=True, secure=s.cookie_secure, samesite="lax", path="/")
    resp.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(24), max_age=max_age, httponly=False, secure=s.cookie_secure,
                    samesite="lax", path="/")


def clear_cookies(resp):
    resp.delete_cookie(COOKIE, path="/")
    resp.delete_cookie(CSRF_COOKIE, path="/")


def csrf_ok(cookie_val: str | None, header_val: str | None) -> bool:
    return bool(cookie_val) and bool(header_val) and hmac.compare_digest(cookie_val, header_val)
