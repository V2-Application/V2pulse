"""Sign-in, sign-out and the page-view beacon. Every attempt, success or failure, is logged."""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from .. import audit, db
from ..audit import access_log
from ..config import settings
from ..iam.resolve import load_ctx
from ..web import problem
from . import entra, parivar, passwords, sessions

router = APIRouter()


def _start_session(cur, request: Request, user_id: str, provider: str):
    ctx = load_ctx(cur, user_id, None, provider)
    token = sessions.create(cur, user_id, provider, request.state.client_ip, request.headers.get("user-agent"),
                            elevated=ctx.elevated)
    audit.action(cur, ctx, "auth.login", "user", user_id, {"provider": provider}, req=request)
    return token


@router.get("/auth/providers")
def providers():
    s = settings()
    out = []
    if entra.enabled():
        out.append({"id": "entra", "label": "Sign in with Microsoft", "url": "/auth/entra/login"})
    if parivar.enabled():
        out.append({"id": "parivar", "label": "Sign in with V2 Parivar"})
    if s.local_login_enabled:
        out.append({"id": "local", "label": "Employee code and password"})
    if s.is_dev:
        out.append({"id": "dev", "label": "Developer sign-in (local only)"})
    return {"providers": out}


class LocalLogin(BaseModel):
    emp_code: str
    password: str


@router.post("/auth/local/login")
def local_login(body: LocalLogin, request: Request):
    s = settings()
    if not s.local_login_enabled:
        raise HTTPException(404, "Not available.")
    code = body.emp_code.strip().upper()
    request.state.actor_emp_code = code
    with db.tx() as cur:
        cur.execute("select count(*) n from audit.event where action = 'auth.login' and outcome = 'failed'"
                    " and ip = %s and ts > now() - interval '15 minutes'", (request.state.client_ip,))
        if cur.fetchone()["n"] >= 20:
            return problem(429, "Too many attempts", "Too many failed sign-ins from this computer. Wait 15 minutes.", request)
        cur.execute("select u.id, u.status, c.password_hash, c.failed_count, c.locked_until from iam.users u"
                    " left join iam.local_credential c on c.user_id = u.id where u.emp_code = %s", (code,))
        row = cur.fetchone()
    now = datetime.now(timezone.utc)
    if row and row["locked_until"] and row["locked_until"] > now:
        audit.action_now(None, "auth.login", "user", row["id"], {"provider": "local", "reason": "locked"},
                         outcome="failed", req=request, actor_emp_code=code)
        return problem(429, "Account locked", "Too many wrong passwords. Try again after %s IST." %
                       (row["locked_until"] + timedelta(hours=5, minutes=30)).strftime("%H:%M"), request)
    ok = bool(row and row["status"] == "active" and row["password_hash"]
              and passwords.verify_password(body.password, row["password_hash"]))
    if not ok:
        with db.tx() as cur:
            if row and row["password_hash"]:
                cur.execute("update iam.local_credential set failed_count = failed_count + 1,"
                            " locked_until = case when failed_count + 1 >= %s then now() + make_interval(mins => %s) end"
                            " where user_id = %s", (s.local_max_failures, s.local_lock_minutes, row["id"]))
        audit.action_now(None, "auth.login", "user", row["id"] if row else None,
                         {"provider": "local", "reason": "bad credentials" if row else "unknown employee code"},
                         outcome="failed", req=request, actor_emp_code=code)
        return problem(401, "Sign-in failed", "Employee code or password is wrong.", request)
    with db.tx() as cur:
        cur.execute("update iam.local_credential set failed_count = 0, locked_until = null where user_id = %s", (row["id"],))
        token = _start_session(cur, request, str(row["id"]), "local")
    resp = JSONResponse({"ok": True})
    sessions.set_cookies(resp, token)
    return resp


class ParivarLogin(BaseModel):
    emp_code: str
    password: str


@router.post("/auth/parivar/login")
def parivar_login(body: ParivarLogin, request: Request):
    if not parivar.enabled():
        raise HTTPException(404, "Not available.")
    code = body.emp_code.strip().upper()
    request.state.actor_emp_code = code
    with db.tx() as cur:
        cur.execute("select count(*) n from audit.event where action = 'auth.login' and outcome = 'failed'"
                    " and ip = %s and ts > now() - interval '15 minutes'", (request.state.client_ip,))
        if cur.fetchone()["n"] >= 20:
            return problem(429, "Too many attempts", "Too many failed sign-ins from this computer. Wait 15 minutes.", request)
        cur.execute("select id, status from iam.users where emp_code = %s", (code,))
        row = cur.fetchone()
    # Must be a known, active V2Pulse employee first: Parivar may have accounts we do not (left staff, service IDs).
    if not row or row["status"] != "active":
        audit.action_now(None, "auth.login", "user", row["id"] if row else None,
                         {"provider": "parivar", "reason": "not an active V2Pulse employee"},
                         outcome="failed", req=request, actor_emp_code=code)
        return problem(401, "Sign-in failed", "Employee code or password is wrong.", request)
    try:
        ok = parivar.verify(code, body.password)
    except parivar.ParivarUnavailable as e:
        audit.action_now(None, "auth.login", "user", row["id"], {"provider": "parivar", "reason": "parivar unavailable", "detail": str(e)[:200]},
                         outcome="failed", req=request, actor_emp_code=code)
        return problem(503, "V2 Parivar is not responding", "Could not reach V2 Parivar to check your password. Try again shortly.", request)
    if not ok:
        audit.action_now(None, "auth.login", "user", row["id"], {"provider": "parivar", "reason": "bad credentials"},
                         outcome="failed", req=request, actor_emp_code=code)
        return problem(401, "Sign-in failed", "Employee code or password is wrong.", request)
    with db.tx() as cur:
        token = _start_session(cur, request, str(row["id"]), "parivar")
    resp = JSONResponse({"ok": True})
    sessions.set_cookies(resp, token)
    return resp


@router.get("/auth/entra/login")
def entra_login(next: str = "/"):
    if not entra.enabled():
        raise HTTPException(404, "Microsoft sign-in is not configured yet.")
    with db.tx() as cur:
        url = entra.begin(cur, next)
    return RedirectResponse(url, status_code=302)


@router.get("/auth/entra/callback")
def entra_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        audit.action_now(None, "auth.login", details={"provider": "entra", "reason": error, "detail": error_description[:300]},
                         outcome="failed", req=request)
        return RedirectResponse("/login?error=" + quote(error), status_code=302)
    try:
        with db.tx() as cur:
            claims, next_url = entra.finish(cur, code, state)
    except Exception as e:  # noqa: BLE001
        audit.action_now(None, "auth.login", details={"provider": "entra", "reason": str(e)[:300]}, outcome="failed", req=request)
        return RedirectResponse("/login?error=" + quote(str(e)[:200]), status_code=302)
    emails = entra.claim_emails(claims)
    with db.tx() as cur:
        cur.execute("select id, emp_code from iam.users where status = 'active' and lower(email) = any(%s) limit 2", (emails,))
        found = cur.fetchall()
        if len(found) != 1:
            audit.action(cur, None, "auth.login", "user", None,
                         {"provider": "entra", "emails": emails, "reason": "no matching active employee" if not found else "email matches two employees"},
                         outcome="denied", req=request)
            return RedirectResponse("/login?error=not_linked&email=" + quote(emails[0] if emails else ""), status_code=302)
        request.state.actor_emp_code = found[0]["emp_code"]
        token = _start_session(cur, request, str(found[0]["id"]), "entra")
    resp = RedirectResponse(next_url, status_code=302)
    sessions.set_cookies(resp, token)
    return resp


@router.get("/auth/dev/users")
def dev_users(request: Request):
    _dev_only(request)
    with db.tx() as cur:
        cur.execute("select u.emp_code, u.display_name, u.location_code as location,"
                    " array(select role_code from iam.user_role r where r.user_id = u.id order by 1) roles"
                    " from iam.users u where u.status = 'active'"
                    " order by (select count(*) from iam.user_role r where r.user_id = u.id) desc, u.emp_code limit 60")
        return {"users": cur.fetchall()}


class DevLogin(BaseModel):
    emp_code: str


@router.post("/auth/dev/login")
def dev_login(body: DevLogin, request: Request):
    _dev_only(request)
    with db.tx() as cur:
        cur.execute("select id from iam.users where emp_code = %s and status = 'active'", (body.emp_code.strip().upper(),))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "No such active user.")
        token = _start_session(cur, request, str(row["id"]), "dev")
    resp = JSONResponse({"ok": True})
    sessions.set_cookies(resp, token)
    return resp


def _dev_only(request: Request):
    if not settings().is_dev or request.state.client_ip not in ("127.0.0.1", "::1"):
        raise HTTPException(404, "Not found.")


@router.post("/auth/logout")
def logout(request: Request):
    token = request.cookies.get(sessions.COOKIE)
    with db.tx() as cur:
        sess = sessions.lookup(cur, token)
        if sess:
            ctx = load_ctx(cur, sess["user_id"], str(sess["id"]), sess["provider"])
            request.state.ctx = ctx
            sessions.revoke(cur, token)
            audit.action(cur, ctx, "auth.logout", "user", sess["user_id"], req=request)
    resp = JSONResponse({"ok": True})
    sessions.clear_cookies(resp)
    return resp


class PageView(BaseModel):
    path: str
    title: str | None = None
    csrf: str | None = None


@router.post("/api/v1/log/view")
def page_view(body: PageView, request: Request):
    """SPA page-view beacon. sendBeacon cannot set headers, so the CSRF token rides in the body."""
    from ..web import current
    header = request.headers.get("x-csrf-token") or body.csrf
    if not sessions.csrf_ok(request.cookies.get(sessions.CSRF_COOKIE), header):
        raise HTTPException(403, "Security token missing.")
    ctx = current(request)
    access_log.record(kind="page", method="VIEW", path=body.path[:500], query=(body.title or "")[:200] or None,
                      status=200, ms=None, request_id=request.state.request_id, actor_id=ctx.user_id,
                      actor_emp_code=ctx.emp_code, ip=request.state.client_ip,
                      user_agent=request.headers.get("user-agent"), session_id=ctx.session_id)
    return {"ok": True}
