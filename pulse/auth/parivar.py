"""V2 Parivar (HRMS) sign-in: employees sign in to V2Pulse with their existing Parivar employee code and
password. V2Pulse posts the credentials to the Parivar login API at sign-in and reads only whether they
are valid; the password is never stored or logged. The employee must already exist and be active in V2Pulse
(loaded by the hourly HR sync), so this grants no access on its own — it only proves who the person is.
"""
import httpx

from ..config import settings


def enabled() -> bool:
    return bool(settings().parivar_login_url)


class ParivarUnavailable(Exception):
    """Parivar could not be reached or gave an unexpected response (not a wrong password)."""


def verify(emp_code: str, password: str) -> bool:
    """True if Parivar accepts this employee code and password. Raises ParivarUnavailable if Parivar itself
    is unreachable or misbehaving, so the caller can tell 'wrong password' from 'service down'."""
    s = settings()
    payload = {s.parivar_user_field: emp_code, s.parivar_pass_field: password}
    try:
        r = httpx.post(s.parivar_login_url, json=payload, timeout=s.parivar_timeout_s, verify=s.parivar_verify_tls,
                       headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        raise ParivarUnavailable(str(e))
    if r.status_code in (400, 401, 403):
        return False                                  # Parivar rejected the credentials
    if r.status_code >= 500:
        raise ParivarUnavailable("Parivar returned %d" % r.status_code)
    if r.status_code // 100 != 2:
        return False
    if not s.parivar_success_field:
        return True                                   # any 2xx means accepted
    try:
        data = r.json()
    except ValueError:
        raise ParivarUnavailable("Parivar response was not JSON")
    val = data.get(s.parivar_success_field) if isinstance(data, dict) else None
    return bool(val) and str(val).lower() not in ("false", "0", "fail", "failed")
