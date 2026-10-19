"""Administration: users, roles and access (RBAC), scope grants, HR role rules, the log viewer and
background jobs."""
import csv
import io
import json
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import audit, db
from ..auth import passwords, sessions
from ..iam import roles as R
from ..iam.context import Ctx
from ..sync import hr as hr_sync
from ..web import envelope, require

router = APIRouter(prefix="/api/v1/admin")
IST = timedelta(hours=5, minutes=30)


def _user(cur, uid):
    cur.execute("select * from iam.users where id = %s", (uid,))
    u = cur.fetchone()
    if not u:
        raise HTTPException(404, "User not found.")
    return u


def _mask(u: dict, ctx: Ctx) -> dict:
    out = {k: (str(v) if k in ("id", "home_node_id", "dept_node_id") and v else v) for k, v in u.items()
           if k not in ("prefs",)}
    if "PERSONAL" not in ctx.data_classes and not ctx.can("admin.users.manage"):
        out["mobile"] = ("******" + u["mobile"][-4:]) if u.get("mobile") else None
    return out


# ---------------------------------------------------------------- users
@router.get("/users")
def users(q: str = "", status: str = "active", cursor: str = "", limit: int = 50,
          ctx: Ctx = Depends(require("admin.users.view"))):
    limit = max(1, min(limit, 200))
    off = int(cursor) if cursor.isdigit() else 0
    like = "%" + q.strip().lower() + "%"
    with db.tx() as cur:
        cur.execute(
            "select u.*, array(select role_code from iam.user_role r where r.user_id = u.id"
            "  and (r.valid_to is null or r.valid_to > now()) order by 1) roles from iam.users u"
            " where (%s = '' or u.status = %s) and (%s = '%%%%' or lower(u.emp_code) like %s or lower(u.display_name) like %s"
            "  or lower(coalesce(u.email,'')) like %s or lower(coalesce(u.location_code,'')) like %s)"
            " order by u.display_name offset %s limit %s",
            (status, status, like, like, like, like, like, off, limit + 1))
        rows = cur.fetchall()
    nxt = str(off + limit) if len(rows) > limit else None
    keep = ("id", "emp_code", "display_name", "email", "designation", "hr_department", "location_code", "location_name",
            "status", "roles", "last_login_at", "source")
    return envelope([{k: (str(r[k]) if k == "id" else r[k]) for k in keep} for r in rows[:limit]], next_cursor=nxt)


@router.get("/users/{uid}")
def user_detail(uid: str, ctx: Ctx = Depends(require("admin.users.view"))):
    with db.tx() as cur:
        u = _user(cur, uid)
        cur.execute("select ur.role_code, ur.source, ur.valid_from, ur.valid_to, ur.reason, g.emp_code granted_by"
                    " from iam.user_role ur left join iam.users g on g.id = ur.granted_by where ur.user_id = %s order by 1", (uid,))
        roles = cur.fetchall()
        cur.execute("select sg.id, sg.source, sg.valid_to, sg.reason, n.id node_id, n.node_type, n.code, n.name"
                    " from iam.scope_grant sg join iam.org_node n on n.id = sg.node_id where sg.user_id = %s", (uid,))
        scopes = [{"id": str(r["id"]), "source": r["source"], "valid_to": r["valid_to"], "reason": r["reason"],
                   "node": {"id": str(r["node_id"]), "type": r["node_type"], "code": r["code"], "name": r["name"]}}
                  for r in cur.fetchall()]
        cur.execute("select id, provider, created_at, last_seen_at, expires_at, ip, revoked_at from iam.session"
                    " where user_id = %s order by created_at desc limit 10", (uid,))
        sess = [{**r, "id": str(r["id"])} for r in cur.fetchall()]
        cur.execute("select 1 from iam.local_credential where user_id = %s", (uid,))
        has_pw = cur.fetchone() is not None
        from ..iam.resolve import load_ctx
        eff = load_ctx(cur, uid) if u["status"] == "active" else None
    detail = {**_mask(u, ctx), "role_grants": roles, "scope_grants": scopes, "sessions": sess,
              "has_local_password": has_pw,
              "effective": {"roles": eff.roles, "permissions": sorted(eff.permissions), "scope_label": eff.scope_label,
                            "departments": eff.depts} if eff else None}
    # The user page reads these fields from the top level of the reply (it does not unwrap "data"), and crashed on
    # role_grants.length with a blank screen. Send them at the top level too; "data" stays for other callers.
    return {**detail, "data": detail}


class RoleGrant(BaseModel):
    role_code: str
    valid_to: datetime | None = None
    reason: str = Field(min_length=3, max_length=300)


@router.post("/users/{uid}/roles")
def grant_role(uid: str, body: RoleGrant, request: Request, ctx: Ctx = Depends(require("admin.users.manage"))):
    with db.tx() as cur:
        u = _user(cur, uid)
        cur.execute("select 1 from iam.role where code = %s", (body.role_code,))
        if not cur.fetchone():
            raise HTTPException(422, "Unknown role.")
        if body.role_code == "PORTAL_ADMIN" and "PORTAL_ADMIN" not in ctx.roles:
            raise HTTPException(403, "Only a portal administrator can make another.")
        cur.execute("insert into iam.user_role (user_id, role_code, source, valid_to, granted_by, reason)"
                    " values (%s,%s,'manual',%s,%s,%s) on conflict (user_id, role_code, source) do update"
                    " set valid_to = excluded.valid_to, granted_by = excluded.granted_by, reason = excluded.reason",
                    (uid, body.role_code, body.valid_to, ctx.user_id, body.reason))
        audit.action(cur, ctx, "admin.role.grant", "user", u["emp_code"], body.model_dump(), req=request)
    return {"ok": True}


@router.delete("/users/{uid}/roles/{role_code}")
def revoke_role(uid: str, role_code: str, request: Request, ctx: Ctx = Depends(require("admin.users.manage"))):
    with db.tx() as cur:
        u = _user(cur, uid)
        if uid == ctx.user_id and role_code == "PORTAL_ADMIN":
            raise HTTPException(409, "You cannot remove your own administrator role.")
        cur.execute("delete from iam.user_role where user_id = %s and role_code = %s and source = 'manual'", (uid, role_code))
        if not cur.rowcount:
            raise HTTPException(409, "No manual grant of that role (rule-based roles follow HR data).")
        audit.action(cur, ctx, "admin.role.revoke", "user", u["emp_code"], {"role_code": role_code}, req=request)
    return {"ok": True}


class ScopeGrant(BaseModel):
    node_id: str
    valid_to: datetime | None = None
    reason: str = Field(min_length=3, max_length=300)


@router.post("/users/{uid}/scopes")
def grant_scope(uid: str, body: ScopeGrant, request: Request, ctx: Ctx = Depends(require("admin.users.manage"))):
    with db.tx() as cur:
        u = _user(cur, uid)
        cur.execute("select node_type, code, name from iam.org_node where id = %s", (body.node_id,))
        n = cur.fetchone()
        if not n:
            raise HTTPException(422, "Unknown org node.")
        if n["node_type"] == "company" and not ctx.scope.all:
            raise HTTPException(403, "Only someone with company-wide scope can grant it.")
        cur.execute("insert into iam.scope_grant (user_id, node_id, source, valid_to, granted_by, reason)"
                    " values (%s,%s,'manual',%s,%s,%s) on conflict (user_id, node_id, source) do update"
                    " set valid_to = excluded.valid_to, granted_by = excluded.granted_by, reason = excluded.reason",
                    (uid, body.node_id, body.valid_to, ctx.user_id, body.reason))
        audit.action(cur, ctx, "admin.scope.grant", "user", u["emp_code"], {**body.model_dump(), "node": dict(n)}, req=request)
    return {"ok": True}


@router.delete("/users/{uid}/scopes/{gid}")
def revoke_scope(uid: str, gid: str, request: Request, ctx: Ctx = Depends(require("admin.users.manage"))):
    with db.tx() as cur:
        u = _user(cur, uid)
        cur.execute("delete from iam.scope_grant where id = %s and user_id = %s and source = 'manual' returning node_id", (gid, uid))
        row = cur.fetchone()
        if not row:
            raise HTTPException(409, "No manual scope grant with that id (rule-based scope follows the store hierarchy).")
        audit.action(cur, ctx, "admin.scope.revoke", "user", u["emp_code"], {"grant_id": gid, "node_id": row["node_id"]},
                     req=request)
    return {"ok": True}


class PasswordIn(BaseModel):
    password: str


@router.post("/users/{uid}/local-password")
def set_password(uid: str, body: PasswordIn, request: Request, ctx: Ctx = Depends(require("admin.users.manage"))):
    msg = passwords.problem(body.password)
    if msg:
        raise HTTPException(422, msg)
    with db.tx() as cur:
        u = _user(cur, uid)
        cur.execute("insert into iam.local_credential (user_id, password_hash, set_by) values (%s,%s,%s)"
                    " on conflict (user_id) do update set password_hash = excluded.password_hash, failed_count = 0,"
                    " locked_until = null, set_by = excluded.set_by, updated_at = now()",
                    (uid, passwords.hash_password(body.password), ctx.user_id))
        sessions.revoke_user(cur, uid)
        audit.action(cur, ctx, "admin.password.set", "user", u["emp_code"], req=request)
    return {"ok": True}


class EmailIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.put("/users/{uid}/email")
def link_email(uid: str, body: EmailIn, request: Request, ctx: Ctx = Depends(require("admin.users.manage"))):
    em = body.email.strip().lower()
    with db.tx() as cur:
        u = _user(cur, uid)
        cur.execute("select emp_code from iam.users where lower(email) = %s and status = 'active' and id <> %s", (em, uid))
        other = cur.fetchone()
        if other:
            raise HTTPException(409, "That email already belongs to %s." % other["emp_code"])
        cur.execute("update iam.users set email = %s, email_locked = true, updated_at = now() where id = %s", (em, uid))
        audit.action(cur, ctx, "admin.email.link", "user", u["emp_code"], {"from": u["email"], "to": em}, req=request)
    return {"ok": True}


@router.post("/users/{uid}/sessions/revoke")
def revoke_sessions(uid: str, request: Request, ctx: Ctx = Depends(require("admin.users.manage"))):
    with db.tx() as cur:
        u = _user(cur, uid)
        sessions.revoke_user(cur, uid)
        audit.action(cur, ctx, "admin.sessions.revoke", "user", u["emp_code"], req=request)
    return {"ok": True}


@router.get("/org/nodes")
def org_nodes(type: str = "", q: str = "", ctx: Ctx = Depends(require("admin.users.view"))):
    with db.tx() as cur:
        cur.execute("select id, node_type, code, name from iam.org_node where is_active and (%s = '' or node_type = %s)"
                    " and (%s = '' or lower(code) like %s or lower(name) like %s) order by node_type, code limit 200",
                    (type, type, q, "%" + q.lower() + "%", "%" + q.lower() + "%"))
        return {"data": [{**r, "id": str(r["id"]), "type": r["node_type"]} for r in cur.fetchall()]}


@router.get("/roles")
def roles(ctx: Ctx = Depends(require("admin.roles.view", "admin.users.view"))):
    with db.tx() as cur:
        cur.execute("select r.*, (select count(distinct user_id) from iam.user_role ur where ur.role_code = r.code"
                    " and (ur.valid_to is null or ur.valid_to > now())) user_count from iam.role r order by r.code")
        return {"data": cur.fetchall()}


# ---------------------------------------------------------------- logs
def _range(frm: str, to: str):
    """Dates are IST calendar days; default = today IST."""
    today = (datetime.now(timezone.utc) + IST).date()
    d0 = date.fromisoformat(frm) if frm else today
    d1 = date.fromisoformat(to) if to else d0
    start = datetime.combine(d0, datetime.min.time(), tzinfo=timezone.utc) - IST
    end = datetime.combine(d1 + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc) - IST
    if (end - start).days > 400:
        raise HTTPException(422, "Pick a range of at most 400 days.")
    return start, end


def _csv(name, cols, rows):
    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c) for c in cols])
            if buf.tell() > 65536:
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()
        yield buf.getvalue()
    return StreamingResponse(gen(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="%s"' % name})


@router.get("/logs/actions")
def log_actions(request: Request, actor: str = "", action: str = "", outcome: str = "", object_type: str = "",
                object_id: str = "", request_id: str = "", q: str = "", frm: str = "", to: str = "", cursor: str = "",
                limit: int = 100, format: str = "", ctx: Ctx = Depends(require("admin.audit.view"))):
    frm = frm or request.query_params.get("from", "")
    start, end = _range(frm, to)
    cap = 100_000 if format == "csv" else max(1, min(limit, 500))
    w = ["e.ts >= %s", "e.ts < %s"]
    p: list = [start, end]
    for col, val, op in (("e.actor_emp_code", actor.upper(), "="), ("e.outcome", outcome, "="),
                         ("e.object_type", object_type, "="), ("e.object_id", object_id, "="),
                         ("e.request_id", request_id, "=")):
        if val:
            w.append("%s %s %%s" % (col, op))
            p.append(val)
    if action:
        w.append("e.action like %s")
        p.append(action.replace("*", "%") + ("%" if "*" not in action and "." not in action else ""))
    if q:
        w.append("e.details::text ilike %s")
        p.append("%" + q + "%")
    if cursor.isdigit():
        w.append("e.id < %s")
        p.append(int(cursor))
    with db.tx() as cur:
        cur.execute("select e.*, u.display_name actor_name from audit.event e left join iam.users u on u.id = e.actor_id"
                    " where " + " and ".join(w) + " order by e.id desc limit %s", p + [cap + 1])
        rows = cur.fetchall()
    more = len(rows) > cap
    rows = rows[:cap]
    if format == "csv":
        audit.action_now(ctx, "admin.logs.export", "log", "actions", {"rows": len(rows), "filters": dict(request.query_params)},
                         req=request)
        return _csv("v2pulse-actions.csv", ["id", "ts", "actor_emp_code", "actor_name", "action", "outcome", "object_type",
                                            "object_id", "ip", "request_id", "details"], rows)
    return envelope([{**r, "actor_id": str(r["actor_id"]) if r["actor_id"] else None} for r in rows],
                    next_cursor=str(rows[-1]["id"]) if more and rows else None)


@router.get("/logs/access")
def log_access(request: Request, actor: str = "", kind: str = "", path: str = "", status: str = "", request_id: str = "",
               frm: str = "", to: str = "", cursor: str = "", limit: int = 100, format: str = "",
               ctx: Ctx = Depends(require("admin.audit.view"))):
    frm = frm or request.query_params.get("from", "")
    start, end = _range(frm, to)
    cap = 100_000 if format == "csv" else max(1, min(limit, 500))
    w = ["a.ts >= %s", "a.ts < %s"]
    p: list = [start, end]
    for col, val in (("a.actor_emp_code", actor.upper()), ("a.kind", kind), ("a.request_id", request_id)):
        if val:
            w.append("%s = %%s" % col)
            p.append(val)
    if path:
        w.append("a.path ilike %s")
        p.append("%" + path + "%")
    if status:
        if status.endswith("xx") and status[0].isdigit():
            w.append("a.status between %s and %s")
            p += [int(status[0]) * 100, int(status[0]) * 100 + 99]
        elif status.isdigit():
            w.append("a.status = %s")
            p.append(int(status))
    if cursor.isdigit():
        w.append("a.id < %s")
        p.append(int(cursor))
    with db.tx() as cur:
        cur.execute("select a.*, u.display_name actor_name from audit.access a left join iam.users u on u.id = a.actor_id"
                    " where " + " and ".join(w) + " order by a.id desc limit %s", p + [cap + 1])
        rows = cur.fetchall()
    more = len(rows) > cap
    rows = rows[:cap]
    if format == "csv":
        audit.action_now(ctx, "admin.logs.export", "log", "access", {"rows": len(rows), "filters": dict(request.query_params)},
                         req=request)
        return _csv("v2pulse-access.csv", ["id", "ts", "actor_emp_code", "actor_name", "kind", "method", "path", "query",
                                           "status", "ms", "ip", "user_agent", "request_id"], rows)
    return envelope([{**r, "actor_id": str(r["actor_id"]) if r["actor_id"] else None,
                      "session_id": str(r["session_id"]) if r["session_id"] else None} for r in rows],
                    next_cursor=str(rows[-1]["id"]) if more and rows else None)


@router.get("/logs/summary")
def log_summary(request: Request, frm: str = "", to: str = "", ctx: Ctx = Depends(require("admin.audit.view"))):
    start, end = _range(frm or request.query_params.get("from", ""), to)
    with db.tx() as cur:
        cur.execute("select count(*) actions, count(*) filter (where outcome = 'denied') denied,"
                    " count(*) filter (where outcome = 'failed') failed from audit.event where ts >= %s and ts < %s", (start, end))
        a = cur.fetchone()
        cur.execute("select count(*) filter (where kind = 'page') page_views, count(*) filter (where kind = 'api') api_calls,"
                    " count(distinct actor_id) active_users from audit.access where ts >= %s and ts < %s", (start, end))
        v = cur.fetchone()
        cur.execute("select path, count(*) n from audit.access where kind = 'page' and ts >= %s and ts < %s"
                    " group by 1 order by 2 desc limit 10", (start, end))
        paths = cur.fetchall()
        cur.execute("select a.actor_emp_code emp_code, max(u.display_name) name, count(*) n from audit.access a"
                    " left join iam.users u on u.id = a.actor_id where a.ts >= %s and a.ts < %s and a.actor_emp_code is not null"
                    " group by 1 order by 3 desc limit 10", (start, end))
        actors = cur.fetchall()
    return {"data": {**a, **v, "top_paths": paths, "top_actors": actors}}


# ---------------------------------------------------------------- jobs
@router.get("/jobs")
def jobs(ctx: Ctx = Depends(require("admin.jobs.view"))):
    with db.tx() as cur:
        cur.execute("""select j.*, r.status r_status, r.started_at r_started, r.finished_at r_finished, r.message r_message,
                              r.triggered_by r_by
                       from ops.job j left join lateral (select * from ops.job_run x where x.job_code = j.code
                                                          order by x.id desc limit 1) r on true order by j.code""")
        out = []
        for j in cur.fetchall():
            out.append({"code": j["code"], "name": j["name"], "schedule": j["schedule"], "enabled": j["enabled"],
                        "next_run_at": j["next_run_at"],
                        "last_run": {"status": j["r_status"], "started_at": j["r_started"], "finished_at": j["r_finished"],
                                     "message": j["r_message"], "triggered_by": j["r_by"]} if j["r_status"] else None})
    return {"data": out}


@router.get("/jobs/{code}/runs")
def job_runs(code: str, ctx: Ctx = Depends(require("admin.jobs.view"))):
    with db.tx() as cur:
        cur.execute("select * from ops.job_run where job_code = %s order by id desc limit 50", (code,))
        return {"data": cur.fetchall()}


@router.post("/jobs/{code}/run", status_code=202)
def run_job(code: str, request: Request, ctx: Ctx = Depends(require("admin.jobs.run"))):
    with db.tx() as cur:
        cur.execute("select 1 from ops.job where code = %s", (code,))
        if not cur.fetchone():
            raise HTTPException(404, "No such job.")
        cur.execute("select 1 from ops.job_request where job_code = %s and claimed_at is null", (code,))
        if cur.fetchone():
            raise HTTPException(409, "This job is already queued to run.")
        cur.execute("insert into ops.job_request (job_code, requested_by) values (%s,%s) returning id", (code, ctx.user_id))
        rid = cur.fetchone()["id"]
        audit.action(cur, ctx, "admin.job.run", "job", code, {"request_id": rid}, req=request)
    return {"queued": rid}


# ---------------------------------------------------------------- department mapping and role rules
@router.get("/departments")
def departments(ctx: Ctx = Depends(require("admin.users.view"))):
    """HRMS departments and the functional department each belongs to (drives Control Tower and report trees)."""
    with db.tx() as cur:
        cur.execute("select m.hr_department_id, m.hr_department_name, m.portal_dept_code,"
                    " (select count(*) from iam.users u where u.hr_department_id = m.hr_department_id and u.status = 'active') employees"
                    " from iam.dept_map m order by m.portal_dept_code nulls first, employees desc")
        rows = cur.fetchall()
        cur.execute("select code, name from iam.org_node where node_type = 'dept' and is_active order by name")
        funcs = cur.fetchall()
    return {"data": rows, "functional_departments": funcs}


class DeptMapIn(BaseModel):
    portal_dept_code: str | None = None


@router.put("/departments/{hr_department_id}")
def map_department(hr_department_id: int, body: DeptMapIn, request: Request,
                   ctx: Ctx = Depends(require("admin.users.manage"))):
    from ..sync.hr import _fix_paths
    code = body.portal_dept_code or "UNASSIGNED"
    with db.tx() as cur:
        cur.execute("select * from iam.dept_map where hr_department_id = %s for update", (hr_department_id,))
        m = cur.fetchone()
        if not m:
            raise HTTPException(404, "Unknown HRMS department.")
        cur.execute("select id from iam.org_node where node_type = 'dept' and code = %s", (code,))
        parent = cur.fetchone()
        if not parent:
            raise HTTPException(422, "Unknown functional department.")
        cur.execute("update iam.dept_map set portal_dept_code = %s where hr_department_id = %s",
                    (body.portal_dept_code, hr_department_id))
        cur.execute("update iam.org_node set parent_id = %s, updated_at = now() where node_type = 'hr_dept' and code = %s",
                    (parent["id"], str(hr_department_id)))
        _fix_paths(cur)
        audit.action(cur, ctx, "admin.department.map", "hr_department", hr_department_id,
                     {"name": m["hr_department_name"], "from": m["portal_dept_code"], "to": body.portal_dept_code}, req=request)
    return {"ok": True}


@router.get("/role-rules")
def role_rules(ctx: Ctx = Depends(require("admin.users.view"))):
    with db.tx() as cur:
        cur.execute("select r.*, (select count(*) from iam.user_role ur where ur.role_code = r.role_code and ur.source = 'rule') holders"
                    " from iam.role_rule r order by priority")
        return {"data": cur.fetchall()}


# ---------------------------------------------------------------- roles & access (RBAC)
# Admins manage roles here: edit what a role may do, create custom roles, assign people, and maintain
# the HR rules that assign roles automatically. Nobody can hand out a permission they do not hold
# themselves, and the administrator role cannot be edited, so nobody can lock everyone out.
@router.get("/permissions")
def permissions(ctx: Ctx = Depends(require("admin.roles.view", "admin.users.view"))):
    return {"data": [{"code": p, "description": R.PERMISSIONS[p], "module": R.module_of(p)} for p in R.ALL],
            "modules": [name for _, name in R.MODULES]}


def _role(cur, code, lock=False):
    cur.execute("select * from iam.role where code = %s" + (" for update" if lock else ""), (code,))
    r = cur.fetchone()
    if not r:
        raise HTTPException(404, "Role not found.")
    return r


def _check_perms(perms: list[str], ctx: Ctx):
    unknown = sorted(set(perms) - set(R.ALL))
    if unknown:
        raise HTTPException(422, "Unknown permission(s): %s." % ", ".join(unknown))
    beyond = sorted(set(perms) - ctx.permissions)
    if beyond and "PORTAL_ADMIN" not in ctx.roles:
        raise HTTPException(403, "You can only give permissions you hold yourself: %s." % ", ".join(beyond))


class RoleIn(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,39}$")
    name: str = Field(min_length=2, max_length=80)
    description: str = Field("", max_length=300)
    permissions: list[str] = []


class RoleUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str = Field("", max_length=300)
    permissions: list[str]


@router.post("/roles")
def create_role(body: RoleIn, request: Request, ctx: Ctx = Depends(require("admin.roles.manage"))):
    _check_perms(body.permissions, ctx)
    with db.tx() as cur:
        cur.execute("select 1 from iam.role where code = %s", (body.code,))
        if cur.fetchone():
            raise HTTPException(409, "A role with code %s already exists." % body.code)
        cur.execute("insert into iam.role (code, name, description, permissions, is_system, updated_by)"
                    " values (%s,%s,%s,%s,false,%s)",
                    (body.code, body.name, body.description, sorted(set(body.permissions)), ctx.user_id))
        audit.action(cur, ctx, "admin.role.create", "role", body.code, body.model_dump(), req=request)
    return {"ok": True}


@router.put("/roles/{code}")
def update_role(code: str, body: RoleUpdate, request: Request, ctx: Ctx = Depends(require("admin.roles.manage"))):
    if code in R.LOCKED:
        raise HTTPException(409, "The administrator role always holds every permission and cannot be edited.")
    _check_perms(body.permissions, ctx)
    perms = sorted(set(body.permissions))
    with db.tx() as cur:
        before = _role(cur, code, lock=True)
        removed = sorted(set(before["permissions"]) - set(perms))
        _check_perms(removed, ctx)   # taking a permission away needs the same standing as giving it
        customized = False
        if before["is_system"] and code in R.ROLES:
            name, desc, default = R.ROLES[code][0], R.ROLES[code][1], sorted(set(R.ROLES[code][2]))
            customized = (perms, body.name, body.description) != (default, name, desc)
        cur.execute("update iam.role set name = %s, description = %s, permissions = %s, is_customized = %s,"
                    " updated_at = now(), updated_by = %s where code = %s",
                    (body.name, body.description, perms, customized, ctx.user_id, code))
        audit.action(cur, ctx, "admin.role.update", "role", code,
                     {"added": sorted(set(perms) - set(before["permissions"])), "removed": removed,
                      "name": {"from": before["name"], "to": body.name} if before["name"] != body.name else None,
                      "customized": customized}, req=request)
    return {"ok": True, "customized": customized}


@router.post("/roles/{code}/reset")
def reset_role(code: str, request: Request, ctx: Ctx = Depends(require("admin.roles.manage"))):
    if code not in R.ROLES or code in R.LOCKED:
        raise HTTPException(409, "Only system roles can be reset to their defaults.")
    name, desc, perms = R.ROLES[code]
    with db.tx() as cur:
        before = _role(cur, code, lock=True)
        cur.execute("update iam.role set name = %s, description = %s, permissions = %s, is_customized = false,"
                    " updated_at = now(), updated_by = %s where code = %s",
                    (name, desc, sorted(set(perms)), ctx.user_id, code))
        audit.action(cur, ctx, "admin.role.reset", "role", code, {"permissions_before": before["permissions"]}, req=request)
    return {"ok": True}


@router.delete("/roles/{code}")
def delete_role(code: str, request: Request, ctx: Ctx = Depends(require("admin.roles.manage"))):
    with db.tx() as cur:
        r = _role(cur, code, lock=True)
        if r["is_system"]:
            raise HTTPException(409, "System roles cannot be deleted; reset or edit them instead.")
        cur.execute("select count(*) n from iam.role_rule where role_code = %s", (code,))
        if cur.fetchone()["n"]:
            raise HTTPException(409, "Delete the HR rules that assign this role first.")
        cur.execute("delete from iam.user_role where role_code = %s", (code,))
        holders = cur.rowcount
        cur.execute("delete from iam.role where code = %s", (code,))
        audit.action(cur, ctx, "admin.role.delete", "role", code,
                     {"name": r["name"], "permissions": r["permissions"], "grants_removed": holders}, req=request)
    return {"ok": True, "grants_removed": holders}


@router.get("/roles/{code}/members")
def role_members(code: str, q: str = "", ctx: Ctx = Depends(require("admin.roles.view", "admin.users.view"))):
    with db.tx() as cur:
        _role(cur, code)
        cur.execute("""select u.id, u.emp_code, u.display_name, u.designation, u.location_code, u.location_name,
                              ur.source, ur.valid_to, ur.reason, ur.created_at, g.emp_code granted_by
                       from iam.user_role ur join iam.users u on u.id = ur.user_id
                       left join iam.users g on g.id = ur.granted_by
                       where ur.role_code = %(c)s and (ur.valid_to is null or ur.valid_to > now()) and u.status = 'active'
                         and (%(q)s = '' or u.emp_code ilike %(p)s or u.display_name ilike %(p)s)
                       order by ur.source, u.display_name limit 501""",
                    {"c": code, "q": q.strip(), "p": "%" + q.strip() + "%"})
        rows = cur.fetchall()
        cur.execute("select count(distinct ur.user_id) filter (where ur.source = 'manual') manual,"
                    " count(distinct ur.user_id) filter (where ur.source = 'rule') rule from iam.user_role ur"
                    " join iam.users u on u.id = ur.user_id where ur.role_code = %s and u.status = 'active'"
                    " and (ur.valid_to is null or ur.valid_to > now())", (code,))
        counts = cur.fetchone()
    return envelope([{**r, "id": str(r["id"])} for r in rows[:500]], truncated=len(rows) > 500,
                    manual=counts["manual"], rule=counts["rule"])


class MembersIn(BaseModel):
    emp_codes: list[str] = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=3, max_length=300)
    valid_to: datetime | None = None


@router.post("/roles/{code}/members")
def add_members(code: str, body: MembersIn, request: Request, ctx: Ctx = Depends(require("admin.users.manage"))):
    with db.tx() as cur:
        r = _role(cur, code)
        if code == "PORTAL_ADMIN" and "PORTAL_ADMIN" not in ctx.roles:
            raise HTTPException(403, "Only a portal administrator can make another.")
        _check_perms(r["permissions"], ctx)
        codes = sorted({c.strip().upper() for c in body.emp_codes if c.strip()})
        cur.execute("select id, emp_code from iam.users where emp_code = any(%s) and status = 'active'", (codes,))
        found = {u["emp_code"]: u["id"] for u in cur.fetchall()}
        for uid in found.values():
            cur.execute("insert into iam.user_role (user_id, role_code, source, valid_to, granted_by, reason)"
                        " values (%s,%s,'manual',%s,%s,%s) on conflict (user_id, role_code, source) do update"
                        " set valid_to = excluded.valid_to, granted_by = excluded.granted_by, reason = excluded.reason",
                        (uid, code, body.valid_to, ctx.user_id, body.reason))
        unknown = [c for c in codes if c not in found]
        audit.action(cur, ctx, "admin.role.members.add", "role", code,
                     {"added": sorted(found), "unknown": unknown, "reason": body.reason, "valid_to": body.valid_to},
                     req=request)
    return {"ok": True, "added": len(found), "unknown": unknown}


@router.delete("/roles/{code}/members/{uid}")
def remove_member(code: str, uid: str, request: Request, ctx: Ctx = Depends(require("admin.users.manage"))):
    with db.tx() as cur:
        u = _user(cur, uid)
        if uid == ctx.user_id and code == "PORTAL_ADMIN":
            raise HTTPException(409, "You cannot remove your own administrator role.")
        cur.execute("delete from iam.user_role where user_id = %s and role_code = %s and source = 'manual'", (uid, code))
        if not cur.rowcount:
            raise HTTPException(409, "No manual grant of that role (rule-based roles follow HR data; change the rule).")
        audit.action(cur, ctx, "admin.role.revoke", "user", u["emp_code"], {"role_code": code}, req=request)
    return {"ok": True}


# ---------------------------------------------------------------- HR auto-assignment rules
class RuleIn(BaseModel):
    priority: int = Field(100, ge=1, le=10000)
    role_code: str
    match: dict
    note: str = Field("", max_length=200)
    is_active: bool = True


def _check_rule(cur, body: RuleIn):
    keys = set(body.match)
    if not keys or keys - hr_sync.RULE_KEYS:
        raise HTTPException(422, "A rule needs at least one condition from: %s." % ", ".join(sorted(hr_sync.RULE_KEYS)))
    m = body.match
    for k in ("location_codes", "designation_in"):
        if k in m:
            if not isinstance(m[k], list) or not m[k] or len(m[k]) > 500:
                raise HTTPException(422, "%s must be a list of 1 to 500 values." % k)
            m[k] = sorted({str(x).strip().upper() for x in m[k] if str(x).strip()})
    if "location_kind" in m and m["location_kind"] not in ("store", "site"):
        raise HTTPException(422, "location_kind must be store or site.")
    if "hierarchy_head" in m and m["hierarchy_head"] not in ("zone", "region", "cluster"):
        raise HTTPException(422, "hierarchy_head must be zone, region or cluster.")
    for k in ("location_name_like", "designation_like", "functional_dept"):
        if k in m and (not isinstance(m[k], str) or not m[k].strip() or len(m[k]) > 100):
            raise HTTPException(422, "%s needs a value." % k)
    return _role(cur, body.role_code)


@router.post("/role-rules/preview")
def preview_rule(body: RuleIn, ctx: Ctx = Depends(require("admin.roles.view", "admin.users.view"))):
    with db.tx() as cur:
        _check_rule(cur, body)
        return {"data": hr_sync.rule_matches(cur, body.match)}


@router.post("/role-rules")
def create_rule(body: RuleIn, request: Request, ctx: Ctx = Depends(require("admin.roles.manage"))):
    with db.tx() as cur:
        _check_perms(_check_rule(cur, body)["permissions"], ctx)
        cur.execute("insert into iam.role_rule (priority, role_code, match, note, is_active, updated_by)"
                    " values (%s,%s,%s,%s,%s,%s) returning id",
                    (body.priority, body.role_code, json.dumps(body.match), body.note, body.is_active, ctx.user_id))
        rid = cur.fetchone()["id"]
        audit.action(cur, ctx, "admin.rule.create", "role_rule", rid, body.model_dump(), req=request)
    return {"ok": True, "id": rid}


@router.put("/role-rules/{rid}")
def update_rule(rid: int, body: RuleIn, request: Request, ctx: Ctx = Depends(require("admin.roles.manage"))):
    with db.tx() as cur:
        cur.execute("select * from iam.role_rule where id = %s for update", (rid,))
        before = cur.fetchone()
        if not before:
            raise HTTPException(404, "Rule not found.")
        _check_perms(_check_rule(cur, body)["permissions"], ctx)
        cur.execute("update iam.role_rule set priority = %s, role_code = %s, match = %s, note = %s, is_active = %s,"
                    " updated_at = now(), updated_by = %s where id = %s",
                    (body.priority, body.role_code, json.dumps(body.match), body.note, body.is_active, ctx.user_id, rid))
        audit.action(cur, ctx, "admin.rule.update", "role_rule", rid,
                     {"from": {k: before[k] for k in ("priority", "role_code", "match", "note", "is_active")},
                      "to": body.model_dump()}, req=request)
    return {"ok": True}


@router.delete("/role-rules/{rid}")
def delete_rule(rid: int, request: Request, ctx: Ctx = Depends(require("admin.roles.manage"))):
    with db.tx() as cur:
        cur.execute("delete from iam.role_rule where id = %s returning *", (rid,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Rule not found.")
        audit.action(cur, ctx, "admin.rule.delete", "role_rule", rid,
                     {k: r[k] for k in ("priority", "role_code", "match", "note")}, req=request)
    return {"ok": True}
