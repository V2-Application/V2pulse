"""Workspace: universal search, favourites, recently opened, announcements, and usage analytics.

Search returns only what the caller may open, using the same visibility rules as each library. People
search shows work details only (no mobile numbers) and needs people.view. Usage analytics read the audit
tables V2Pulse already keeps and need admin.audit.view.
"""
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import audit, db
from ..iam.context import Ctx
from ..web import current, require
from . import masters, portals as portals_mod
from .reports import _roles_ok, tree_name

router = APIRouter(prefix="/api/v1")
IST = "interval '330 minutes'"   # the database has no tz data for Asia/Kolkata; IST has no DST


# ---------------------------------------------------------------- universal search
def _rank(q: str, *fields) -> int:
    """0 = exact / prefix match on the first field, 1 = word prefix, 2 = contains anywhere."""
    f0 = (fields[0] or "").lower()
    if f0 == q or f0.startswith(q):
        return 0
    if any(re.search(r"\b" + re.escape(q), (f or "").lower()) for f in fields):
        return 1
    return 2


@router.get("/search")
def search(q: str, ctx: Ctx = Depends(current)):
    q = q.strip().lower()
    if len(q) < 2:
        return {"data": []}
    like = "%" + q.replace("%", "").replace("_", "\\_") + "%"
    out = []
    with db.tx(ctx.scope) as cur:
        if ctx.can("portals.view"):
            for p in portals_mod.list_for(cur, ctx):
                hay = [p["name"], p["description"], p["category"], p["code"], *(p["tags"] or [])]
                if any(q in (h or "").lower() for h in hay):
                    out.append({"type": "portal", "id": p["id"], "title": p["name"], "subtitle": p["category"],
                                "url": p["url"], "status": p["status"], "rank": _rank(q, *hay)})
        if ctx.can("reports.view"):
            cur.execute("select code, name, description, kind, owner_dept, audience_roles, status from rpt.report"
                        " where status <> 'deprecated' and (name ilike %s or code ilike %s or description ilike %s) limit 60",
                        (like, like, like))
            for r in cur.fetchall():
                if (r["status"] == "draft" and not ctx.can("reports.manage")) or not _roles_ok(r["audience_roles"], ctx):
                    continue
                out.append({"type": "report", "id": r["code"], "title": r["name"], "subtitle": "Report · %s" % (r["owner_dept"] or r["kind"]),
                            "path": "/reports/view/%s" % r["code"], "rank": _rank(q, r["name"], r["code"], r["description"])})
            cur.execute("select r.id, r.name, r.category, r.description, r.visibility, r.owner_id, r.status, r.audience_roles,"
                        " d.audience_roles d_aud, d.status d_status from bi.report r join bi.dataset d on d.code = r.dataset_code"
                        " where (r.name ilike %s or r.description ilike %s or r.category ilike %s) limit 60", (like, like, like))
            for r in cur.fetchall():
                if r["visibility"] == "personal":
                    if str(r["owner_id"]) != str(ctx.user_id):
                        continue
                elif not ctx.can("reports.design") and (
                        r["status"] != "published" or r["d_status"] != "live"
                        or (r["d_aud"] and not set(r["d_aud"]) & set(ctx.roles))
                        or (r["audience_roles"] and not set(r["audience_roles"]) & set(ctx.roles))):
                    continue
                out.append({"type": "dynamic", "id": str(r["id"]), "title": r["name"],
                            "subtitle": "Dynamic report · %s" % ("My reports" if r["visibility"] == "personal" else r["category"]),
                            "path": "/reports/dynamic/%s" % r["id"], "rank": _rank(q, r["name"], r["category"], r["description"])})
        if ctx.can("masters.view"):
            central = tree_name(cur, "masters")
            cur.execute("select m.*, coalesce((select array_agg(distinct f.tree) from nav.item i join nav.folder f on f.id = i.folder_id"
                        " where i.master_id = m.id and not f.is_archived), '{}') _trees from mst.master m"
                        " where m.name ilike %s or m.code ilike %s or m.description ilike %s or array_to_string(m.tags, ' ') ilike %s"
                        " limit 60", (like, like, like, like))
            for m in cur.fetchall():
                if not masters._visible(m, ctx):
                    continue
                out.append({"type": "master", "id": m["code"], "title": m["name"],
                            "subtitle": "Master · %s" % (central if m["layer"] == "central" else m["owner_dept"]),
                            "path": "/masters/view/%s" % m["code"], "rank": _rank(q, m["name"], m["code"], " ".join(m["tags"]))})
        if ctx.can("people.view"):
            cur.execute("select emp_code, display_name, designation, location_code, location_name, hr_department, email"
                        " from iam.users where status = 'active' and (emp_code ilike %s or display_name ilike %s)"
                        " order by display_name limit 8", (like, like))
            for u in cur.fetchall():
                out.append({"type": "person", "id": u["emp_code"], "title": u["display_name"],
                            "subtitle": " · ".join(x for x in (u["emp_code"], u["designation"], u["location_name"] or u["location_code"],
                                                                 u["hr_department"]) if x),
                            "email": u["email"], "rank": _rank(q, u["display_name"], u["emp_code"])})
    order = {"portal": 0, "dynamic": 1, "report": 2, "master": 3, "person": 4}
    out.sort(key=lambda x: (x["rank"], order[x["type"]], x["title"].lower()))
    for x in out:
        x.pop("rank")
    return {"data": out[:40]}


# ---------------------------------------------------------------- favourites and recent
class FavIn(BaseModel):
    kind: str = Field(pattern=r"^(report|master|bi_report|portal)$")
    ref: str = Field(min_length=1, max_length=80)


def _resolve(cur, kind: str, refs: list[str]) -> dict:
    """Names and links for favourites / recent items; anything gone or no longer visible is dropped by the caller."""
    if not refs:
        return {}
    if kind == "report":
        cur.execute("select code ref, name, kind sub from rpt.report where code = any(%s)", (refs,))
        return {r["ref"]: {"title": r["name"], "subtitle": "Report", "path": "/reports/view/%s" % r["ref"]} for r in cur.fetchall()}
    if kind == "master":
        central = tree_name(cur, "masters")
        cur.execute("select code ref, name, layer, owner_dept from mst.master where code = any(%s) and status <> 'deprecated'", (refs,))
        return {r["ref"]: {"title": r["name"], "subtitle": "Master · %s" % (central if r["layer"] == "central" else r["owner_dept"]),
                           "path": "/masters/view/%s" % r["ref"]} for r in cur.fetchall()}
    if kind == "bi_report":
        ids = [x for x in refs if re.fullmatch(r"[0-9a-f-]{36}", x)]
        cur.execute("select id::text ref, name, category, visibility from bi.report where id = any(%s::uuid[])", (ids,))
        return {r["ref"]: {"title": r["name"], "subtitle": "Dynamic report · %s" % ("My reports" if r["visibility"] == "personal" else r["category"]),
                           "path": "/reports/dynamic/%s" % r["ref"]} for r in cur.fetchall()}
    if kind == "portal":
        cur.execute("select id::text ref, name, category from portal.portal where id::text = any(%s)", (refs,))
        return {r["ref"]: {"title": r["name"], "subtitle": "Portal · %s" % r["category"], "path": "/portals"} for r in cur.fetchall()}
    return {}


@router.get("/me/favorites")
def favorites(ctx: Ctx = Depends(current)):
    with db.tx(ctx.scope) as cur:
        cur.execute("select kind, ref, created_at from core.favorite where user_id = %s order by created_at desc", (ctx.user_id,))
        favs = cur.fetchall()
        names = {k: _resolve(cur, k, [f["ref"] for f in favs if f["kind"] == k]) for k in ("report", "master", "bi_report", "portal")}
    return {"data": [{"kind": f["kind"], "ref": f["ref"], **names[f["kind"]][f["ref"]]} for f in favs if f["ref"] in names[f["kind"]]]}


@router.post("/me/favorites")
def add_favorite(body: FavIn, request: Request, ctx: Ctx = Depends(current)):
    with db.tx() as cur:
        if not _resolve(cur, body.kind, [body.ref]):
            raise HTTPException(404, "Nothing to add.")
        cur.execute("insert into core.favorite (user_id, kind, ref) values (%s,%s,%s) on conflict do nothing",
                    (ctx.user_id, body.kind, body.ref))
        audit.action(cur, ctx, "me.favorite.add", body.kind, body.ref, req=request)
    return {"ok": True}


@router.delete("/me/favorites/{kind}/{ref}")
def remove_favorite(kind: str, ref: str, request: Request, ctx: Ctx = Depends(current)):
    with db.tx() as cur:
        cur.execute("delete from core.favorite where user_id = %s and kind = %s and ref = %s", (ctx.user_id, kind, ref))
        audit.action(cur, ctx, "me.favorite.remove", kind, ref, req=request)
    return {"ok": True}


RECENT = [(re.compile(r"^/masters/view/([^/?#]+)"), "master"), (re.compile(r"^/reports/dynamic/([0-9a-f-]{36})(?:$|[?#])"), "bi_report"),
          (re.compile(r"^/reports/view/([^/?#]+)"), "report")]


@router.get("/me/recent")
def recent(ctx: Ctx = Depends(current)):
    """Masters and reports the user opened lately, newest first (from their own page views)."""
    with db.tx(ctx.scope) as cur:
        cur.execute("select path, max(ts) ts from audit.access where actor_id = %s and kind = 'page'"
                    " and ts > now() - interval '60 days' and (path like '/masters/view/%%' or path like '/reports/dynamic/%%'"
                    " or path like '/reports/view/%%') group by path order by 2 desc limit 60", (ctx.user_id,))
        seen, items = set(), []
        for r in cur.fetchall():
            for rx, kind in RECENT:
                m = rx.match(r["path"])
                if m and (kind, m.group(1)) not in seen:
                    seen.add((kind, m.group(1)))
                    items.append((kind, m.group(1), r["ts"]))
                    break
        names = {k: _resolve(cur, k, [i[1] for i in items if i[0] == k]) for k in ("report", "master", "bi_report")}
    out = [{"kind": k, "ref": ref, "opened_at": ts.isoformat(), **names[k][ref]} for k, ref, ts in items if ref in names[k]]
    return {"data": out[:12]}


# ---------------------------------------------------------------- announcements
def _ann_visible(a: dict, ctx: Ctx) -> bool:
    return (not a["audience_roles"] or bool(set(a["audience_roles"]) & set(ctx.roles))) and \
           (not a["audience_depts"] or bool(set(a["audience_depts"]) & set(ctx.depts)))


def _shape_ann(a: dict, read: bool | None = None) -> dict:
    out = {k: a[k] for k in ("title", "body", "severity", "link_url", "link_label", "audience_roles", "audience_depts", "pinned")}
    out.update({"id": str(a["id"]), "starts_at": a["starts_at"].isoformat(), "ends_at": a["ends_at"].isoformat() if a["ends_at"] else None,
                "created_at": a["created_at"].isoformat(), "author": a.get("author")})
    if read is not None:
        out["read"] = read
    return out


@router.get("/announcements")
def announcements(ctx: Ctx = Depends(current)):
    with db.tx() as cur:
        cur.execute("""select a.*, u.display_name author, (r.user_id is not null) is_read from core.announcement a
                       left join iam.users u on u.id = a.created_by
                       left join core.announcement_read r on r.announcement_id = a.id and r.user_id = %s
                       where a.starts_at <= now() and (a.ends_at is null or a.ends_at > now())
                       order by a.pinned desc, a.starts_at desc limit 50""", (ctx.user_id,))
        rows = [a for a in cur.fetchall() if _ann_visible(a, ctx)]
    data = [_shape_ann(a, a["is_read"]) for a in rows]
    return {"data": data, "unread": sum(1 for a in data if not a["read"])}


@router.post("/announcements/read")
def mark_read(body: dict, ctx: Ctx = Depends(current)):
    ids = [i for i in (body.get("ids") or []) if isinstance(i, str) and re.fullmatch(r"[0-9a-f-]{36}", i)][:100]
    with db.tx() as cur:
        for i in ids:
            cur.execute("insert into core.announcement_read (user_id, announcement_id) select %s, id from core.announcement"
                        " where id = %s on conflict do nothing", (ctx.user_id, i))
    return {"ok": True}


class AnnIn(BaseModel):
    title: str = Field(min_length=3, max_length=140)
    body: str = Field("", max_length=4000)
    severity: str = Field("info", pattern=r"^(info|success|warning|critical)$")
    link_url: str | None = Field(None, max_length=500)
    link_label: str | None = Field(None, max_length=60)
    audience_roles: list[str] = []
    audience_depts: list[str] = []
    pinned: bool = False
    starts_at: datetime | None = None
    ends_at: datetime | None = None


def _check_ann(b: AnnIn):
    if b.link_url and not b.link_url.startswith(("http://", "https://", "/")):
        raise HTTPException(422, "Links must start with http://, https:// or /.")
    if b.ends_at and b.starts_at and b.ends_at <= b.starts_at:
        raise HTTPException(422, "The end must be after the start.")


@router.get("/admin/announcements")
def admin_announcements(ctx: Ctx = Depends(require("admin.announcements.manage"))):
    with db.tx() as cur:
        cur.execute("""select a.*, u.display_name author,
                              (select count(*) from core.announcement_read r where r.announcement_id = a.id) reads
                       from core.announcement a left join iam.users u on u.id = a.created_by order by a.created_at desc limit 200""")
        rows = cur.fetchall()
    return {"data": [{**_shape_ann(a), "reads": a["reads"]} for a in rows]}


@router.post("/admin/announcements")
def create_announcement(body: AnnIn, request: Request, ctx: Ctx = Depends(require("admin.announcements.manage"))):
    _check_ann(body)
    aid = uuid.uuid4()
    with db.tx() as cur:
        cur.execute("insert into core.announcement (id, title, body, severity, link_url, link_label, audience_roles, audience_depts,"
                    " pinned, starts_at, ends_at, created_by, updated_by) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (aid, body.title, body.body, body.severity, body.link_url, body.link_label, body.audience_roles,
                     body.audience_depts, body.pinned, body.starts_at or datetime.now(timezone.utc), body.ends_at,
                     ctx.user_id, ctx.user_id))
        audit.action(cur, ctx, "announcement.create", "announcement", aid, {"title": body.title, "severity": body.severity,
                                                                           "roles": body.audience_roles, "depts": body.audience_depts},
                     req=request)
    return {"data": {"id": str(aid)}}


@router.put("/admin/announcements/{aid}")
def update_announcement(aid: str, body: AnnIn, request: Request, ctx: Ctx = Depends(require("admin.announcements.manage"))):
    _check_ann(body)
    with db.tx() as cur:
        cur.execute("update core.announcement set title=%s, body=%s, severity=%s, link_url=%s, link_label=%s, audience_roles=%s,"
                    " audience_depts=%s, pinned=%s, starts_at=coalesce(%s, starts_at), ends_at=%s, updated_at=now(), updated_by=%s"
                    " where id = %s", (body.title, body.body, body.severity, body.link_url, body.link_label, body.audience_roles,
                                       body.audience_depts, body.pinned, body.starts_at, body.ends_at, ctx.user_id, aid))
        if not cur.rowcount:
            raise HTTPException(404, "Announcement not found.")
        audit.action(cur, ctx, "announcement.update", "announcement", aid, {"title": body.title}, req=request)
    return {"ok": True}


@router.delete("/admin/announcements/{aid}")
def delete_announcement(aid: str, request: Request, ctx: Ctx = Depends(require("admin.announcements.manage"))):
    with db.tx() as cur:
        cur.execute("delete from core.announcement where id = %s returning title", (aid,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Announcement not found.")
        audit.action(cur, ctx, "announcement.delete", "announcement", aid, {"title": r["title"]}, req=request)
    return {"ok": True}


# ---------------------------------------------------------------- usage analytics
SECTION = "case when path = '/' then 'Home' when path like '/portals%%' then 'Portals' when path like '/reports/dynamic%%' then 'Dynamic reports'" \
          " when path like '/reports%%' then 'Reports' when path like '/masters%%' then 'Masters' when path like '/stores%%' then 'Stores'" \
          " when path like '/control-tower%%' then 'Control Tower' when path like '/data%%' then 'Data' when path like '/admin%%' then 'Admin'" \
          " else 'Other' end"


@router.get("/admin/usage")
def usage(days: int = 30, ctx: Ctx = Depends(require("admin.audit.view"))):
    days = max(7, min(days, 180))
    since = "now() - interval '%d days'" % days
    with db.tx() as cur:
        cur.execute("select count(*) n from iam.users where status = 'active' and source = 'hrms'")
        employees = cur.fetchone()["n"]
        cur.execute(f"""select
              count(distinct actor_id) filter (where ts > date_trunc('day', now() + {IST}) - {IST}) today,
              count(distinct actor_id) filter (where ts > now() - interval '7 days') d7,
              count(distinct actor_id) filter (where ts > {since}) dn,
              count(*) filter (where ts > {since}) views
            from audit.access where kind = 'page' and actor_id is not null""")
        k = cur.fetchone()
        cur.execute(f"select count(*) n from audit.event where action = 'auth.login' and ts > {since}")
        logins = cur.fetchone()["n"]
        cur.execute(f"""select (ts + {IST})::date d, count(distinct actor_id) users, count(*) views from audit.access
                        where kind = 'page' and actor_id is not null and ts > {since} group by 1 order by 1""")
        daily = [{"date": r["d"].isoformat(), "users": r["users"], "views": r["views"]} for r in cur.fetchall()]
        cur.execute(f"""select {SECTION} section, count(*) views, count(distinct actor_id) users from audit.access
                        where kind = 'page' and actor_id is not null and ts > {since} group by 1 order by 2 desc""")
        sections = cur.fetchall()
        cur.execute(f"""select extract(hour from ts + {IST})::int h, count(*) views from audit.access
                        where kind = 'page' and actor_id is not null and ts > {since} group by 1 order by 1""")
        hours = {r["h"]: r["views"] for r in cur.fetchall()}

        def top(prefix, kind, rx):
            cur.execute(f"""select substring(path from %s) ref, count(*) opens, count(distinct actor_id) users from audit.access
                            where kind = 'page' and path like %s and ts > {since} group by 1 order by 2 desc limit 10""",
                        (rx, prefix + "%"))
            rows = [r for r in cur.fetchall() if r["ref"]]
            names = _resolve(cur, kind, [r["ref"] for r in rows])
            return [{"title": names.get(r["ref"], {}).get("title", r["ref"]), "path": names.get(r["ref"], {}).get("path"),
                     "opens": r["opens"], "users": r["users"]} for r in rows]
        top_masters = top("/masters/view/", "master", "^/masters/view/([^/?#]+)")
        top_dynamic = top("/reports/dynamic/", "bi_report", "^/reports/dynamic/([0-9a-f-]{36})$")
        top_reports = top("/reports/view/", "report", "^/reports/view/([^/?#]+)")
        cur.execute(f"""select p.name title, count(*) opens, count(distinct e.actor_id) users from audit.event e
                        join portal.portal p on p.code = e.object_id where e.action = 'portal.open' and e.ts > {since}
                        group by 1 order by 2 desc limit 10""")
        top_portals = cur.fetchall()
        cur.execute(f"""select coalesce(n.name, 'Unassigned') dept, count(distinct u.id) employees,
                              count(distinct a.actor_id) active
                        from iam.users u left join iam.dept_map m on m.hr_department_id = u.hr_department_id
                        left join iam.org_node n on n.node_type = 'dept' and n.code = m.portal_dept_code
                        left join audit.access a on a.actor_id = u.id and a.kind = 'page' and a.ts > {since}
                        where u.status = 'active' and u.source = 'hrms' group by 1 order by 3 desc, 2 desc""")
        depts = cur.fetchall()
        cur.execute(f"""select u.emp_code, u.display_name, u.designation, count(*) views, max(a.ts) last_seen
                        from audit.access a join iam.users u on u.id = a.actor_id
                        where a.kind = 'page' and a.ts > {since} group by 1, 2, 3 order by 4 desc limit 10""")
        users = [{**r, "last_seen": r["last_seen"].isoformat()} for r in cur.fetchall()]
    return {"data": {"days": days, "kpis": {"employees": employees, "active_today": k["today"], "active_7d": k["d7"],
                                            "active_period": k["dn"], "page_views": k["views"], "sign_ins": logins},
                     "daily": daily, "sections": sections, "hours": [hours.get(h, 0) for h in range(24)],
                     "top_masters": top_masters, "top_dynamic": top_dynamic, "top_reports": top_reports,
                     "top_portals": top_portals, "departments": depts, "top_users": users}}
