"""PBKDF2-SHA256 password hashing (stdlib only), the same scheme the Report Portal uses."""
import hashlib
import hmac
import secrets

ITERATIONS = 310_000


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITERATIONS)
    return "pbkdf2_sha256$%d$%s$%s" % (ITERATIONS, salt.hex(), dk.hex())


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, it, salt, dk = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(got.hex(), dk)
    except Exception:  # noqa: BLE001
        return False


def problem(pw: str) -> str | None:
    if len(pw or "") < 10:
        return "Password must be at least 10 characters."
    if pw.isdigit() or pw.isalpha() or pw.lower() == pw and not any(c.isdigit() for c in pw):
        return "Use letters and numbers together."
    return None
