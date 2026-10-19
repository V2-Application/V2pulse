"""Request plumbing shared by every module: request id, client IP, LAN allowlist, CSRF,
the signed-in caller, permission checks, problem responses and access logging."""
import ipaddress
import time
import uuid
from functools import lru_cache

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from . import db
from .audit import access_log
from .auth import sessions
from .config import settings
from .iam.context import Ctx
from .iam.resolve import load_ctx

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_EXEMPT = {"/auth/local/login", "/auth/dev/login", "/api/v1/log/view"}   # no session yet / beacon carries token in body
LOGGED_PREFIXES = ("/api/", "/auth/")


@lru_cache
def _nets(spec: str):
    return [ipaddress.ip_network(n.strip(), strict=False) for n in spec.split(",") if n.strip()]


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "0.0.0.0"
    s = settings()
    try:
        if s.trusted_proxies and any(ipaddress.ip_address(peer) in n for n in _nets(s.trusted_proxies)):
            xff = request.headers.get("x-forwarded-for", "")
            if xff:
                return _strip_port(xff.split(",")[0].strip())
    except ValueError:
        pass
    return peer


def _strip_port(addr: str) -> str:
    """IIS ARR forwards 'ip:port' ('[v6]:port' for IPv6); keep the address only."""
    if addr.startswith("[") and "]" in addr:
        return addr[1:addr.index("]")]
    if addr.count(":") == 1:
        return addr.split(":")[0]
    return addr


def ip_allowed(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in _nets(settings().allowed_nets))


def problem(status: int, title: str, detail: str = "", request: Request | None = None, **extra):
    body = {"type": "about:blank", "title": title, "status": status, "detail": detail,
            "trace_id": getattr(getattr(request, "state", None), "request_id", None)}
    body.update(extra)
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


async def middleware(request: Request, call_next):
    request.state.request_id = uuid.uuid4().hex[:16]
    request.state.client_ip = client_ip(request)
    request.state.ctx = None
    t0 = time.perf_counter()
    path = request.url.path
    if not ip_allowed(request.state.client_ip):
        resp = problem(403, "Not on the company network", "V2Pulse is available on the V2 Retail network only.", request)
    else:
        resp = await call_next(request)
    resp.headers["X-Request-Id"] = request.state.request_id
    _security_headers(resp, path)
    if path.startswith(LOGGED_PREFIXES) and path != "/api/v1/log/view":
        ctx = request.state.ctx
        access_log.record(kind="api", method=request.method, path=path, query=str(request.url.query or ""),
                          status=resp.status_code, ms=int((time.perf_counter() - t0) * 1000),
                          request_id=request.state.request_id, actor_id=ctx.user_id if ctx else None,
                          actor_emp_code=ctx.emp_code if ctx else getattr(request.state, "actor_emp_code", None),
                          ip=request.state.client_ip, user_agent=request.headers.get("user-agent"),
                          session_id=ctx.session_id if ctx else None)
    return resp


def _security_headers(resp, path):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Content-Security-Policy",
                            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
    if settings().cookie_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if path.startswith(("/api/", "/auth/")):
        resp.headers.setdefault("Cache-Control", "no-store")


def current(request: Request) -> Ctx:
    """The signed-in caller, or 401. Also enforces CSRF on mutating requests."""
    if request.state.ctx is not None:
        return request.state.ctx
    token = request.cookies.get(sessions.COOKIE)
    with db.tx() as cur:
        sess = sessions.lookup(cur, token)
        ctx = load_ctx(cur, sess["user_id"], str(sess["id"]), sess["provider"]) if sess else None
    if ctx is None:
        raise HTTPException(401, "Please sign in.")
    if request.method in MUTATING and request.url.path not in CSRF_EXEMPT:
        if not sessions.csrf_ok(request.cookies.get(sessions.CSRF_COOKIE), request.headers.get("x-csrf-token")):
            raise HTTPException(403, "Security token missing or expired. Reload the page and try again.")
    request.state.ctx = ctx
    return ctx


def require(*perms: str):
    """Dependency: the caller must hold at least one of `perms`."""
    def dep(request: Request, ctx: Ctx = Depends(current)) -> Ctx:
        if not any(ctx.can(p) for p in perms):
            from .audit import action_now
            action_now(ctx, "access.denied", "permission", "|".join(perms),
                       {"path": request.url.path, "method": request.method}, outcome="denied", req=request)
            raise HTTPException(403, "You do not have access to this. Ask your V2Pulse administrator.")
        return ctx
    return dep


def envelope(data, *, as_of=None, freshness=None, dataset=None, scope=None, next_cursor=None, **meta):
    m = {k: v for k, v in {"as_of": as_of, "freshness": freshness, "dataset": dataset, "scope": scope, **meta}.items()
         if v is not None}
    return {"data": data, "meta": m, "next_cursor": next_cursor}
