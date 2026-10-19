"""Me, Home (My Day), the store directory and the datasets page."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import audit, db
from ..data.contracts import freshness
from ..iam.context import Ctx
from ..web import current, envelope, require
from . import portals as portals_mod

router = APIRouter(prefix="/api/v1")

NAV = [  # key, label, path, icon, any-of permissions, sidebar group
    ("home", "Home", "/", "home", ["home.view"], "Workspace"),
    ("portals", "Portals", "/portals", "grid", ["portals.view"], "Workspace"),
    ("reports", "Reports", "/reports", "folder", ["reports.view"], "Libraries"),
    ("masters", "Masters", "/masters", "layers", ["masters.view"], "Libraries"),
    # /analytics is the Analytics landing page (pulse/analytics), served outside the web app.
    ("analytics", "Analytics", "/analytics", "chart", ["analytics.view"], "Libraries"),
    # /mis and /dumps are plain pages (pulse/mis_ui, pulse/dumps_ui) served outside the web app, like Analytics.
    # The 19 DFS MIS tabs (same names and order groups as dfs.v2retail.net) - one MIS workspace page, pulse/mis_ui.
    ("mis", "Reports", "/mis", "table", ["mis.view"], "MIS"),
    ("mis-day-trend-reports", "Day Wise Trend Reports", "/mis/day-trend-reports", "calendar", ["mis.view"], "MIS"),
    ("mis-inv-reports", "Inventory Reports", "/mis/inv-reports", "layers", ["mis.view"], "MIS"),
    ("mis-sale-trend", "Sale Trend", "/mis/sale-trend", "chart", ["mis.view"], "MIS"),
    ("mis-stock-trend", "Stock Trend", "/mis/stock-trend", "chart", ["mis.view"], "MIS"),
    ("mis-day-trend", "Day Wise Trend", "/mis/day-trend", "calendar", ["mis.view"], "MIS"),
    ("mis-vert-inv", "Vertical Inventory (Article)", "/mis/vert-inv", "layers", ["mis.view"], "MIS"),
    ("mis-inv-dashboard", "Inventory Dashboard", "/mis/inv-dashboard", "chart", ["mis.view"], "MIS"),
    ("mis-bin-wise", "Bin Wise", "/mis/bin-wise", "database", ["mis.view"], "MIS"),
    ("mis-variant-data", "Variant Article Data", "/mis/variant-data", "table", ["mis.view"], "MIS"),
    ("mis-allocation", "Allocation", "/mis/allocation", "grid", ["mis.view"], "MIS"),
    ("mis-alloc-gaps", "Allocation Gaps", "/mis/alloc-gaps", "grid", ["mis.view"], "MIS"),
    ("mis-plan-360", "Plan Live 360", "/mis/plan-360", "activity", ["mis.view"], "MIS"),
    ("mis-tna", "TNA Report", "/mis/tna", "clock", ["mis.view"], "MIS"),
    ("mis-scm", "SCM Report", "/mis/scm", "store", ["mis.view"], "MIS"),
    ("mis-product-master", "Product Master", "/mis/product-master", "layers", ["mis.view"], "MIS"),
    ("mis-store-master", "Store Master", "/mis/store-master", "store", ["mis.view"], "MIS"),
    ("mis-monitor", "Monitor", "/mis/monitor", "activity", ["mis.view"], "MIS"),
    ("mis-lineage", "Dependencies", "/mis/lineage", "database", ["mis.view"], "MIS"),
    ("dumps", "Dump Scheduler", "/mis/dumps", "clock", ["dumps.view"], "MIS"),
    ("scheduled-reports", "Scheduled Reports", "/reports?tree=custom:scheduledreports", "clock", ["reports.view"], "Libraries"),
    # /simulation is the Simulation page (pulse/simulation), served outside the web app, like Analytics.
    ("simulation", "Simulation", "/simulation", "bolt", ["simulation.view"], "Libraries"),
    ("stores", "Stores", "/stores", "store", ["org.stores.view"], "Operations"),
    ("control-tower", "Control Tower", "/control-tower", "activity", ["ct.view", "ct.view_all"], "Operations"),
    ("data", "Data", "/data", "database", ["data.view"], "Operations"),
    ("admin", "Admin", "/admin", "settings", ["admin.users.view", "admin.audit.view", "admin.jobs.view", "portals.manage",
                                              "reports.manage", "reports.design", "masters.manage", "admin.roles.view",
                                              "admin.announcements.manage"],
     "Administration"),
]


@router.get("/me")
def me(ctx: Ctx = Depends(current)):
    u = ctx.user
    return {"user": {"id": ctx.user_id, "emp_code": ctx.emp_code, "display_name": ctx.display_name, "email": u.get("email"),
                     "designation": u.get("designation"), "department": u.get("hr_department"),
                     "location": {"code": u.get("location_code"), "name": u.get("location_name")}},
            "roles": ctx.roles, "permissions": sorted(ctx.permissions), "departments": ctx.depts,
            "scope": {"all": ctx.scope.all, "label": ctx.scope_label, "store_count": ctx.store_count, "nodes": ctx.scope_nodes},
            "nav": [{"key": k, "label": lb, "path": p, "icon": ic, "group": g} for k, lb, p, ic, perms, g in NAV
                    if any(ctx.can(x) for x in perms)],
            "lang": (u.get("prefs") or {}).get("lang", "en"), "provider": ctx.provider}


class Prefs(BaseModel):
    lang: str | None = None


@router.put("/me/prefs")
def prefs(body: Prefs, request: Request, ctx: Ctx = Depends(current)):
    if body.lang and body.lang not in ("en", "hi"):
        raise HTTPException(422, "Language must be en or hi.")
    with db.tx() as cur:
        cur.execute("update iam.users set prefs = prefs || jsonb_strip_nulls(jsonb_build_object('lang', %s::text)) where id = %s",
                    (body.lang, ctx.user_id))
        audit.action(cur, ctx, "me.prefs", "user", ctx.emp_code, body.model_dump(exclude_none=True), req=request)
    return {"ok": True}


@router.get("/me/activity")
def my_activity(cursor: str = "", ctx: Ctx = Depends(current)):
    """The signed-in user's own actions and page views, newest first (cursor = ISO timestamp)."""
    with db.tx() as cur:
        cur.execute("""
            select * from (
              select 'action' as type, ts, action as what, outcome, object_type, object_id, null::text as path, ip
                from audit.event where actor_id = %(u)s and (%(c)s = '' or ts < %(c)s::timestamptz)
              union all
              select 'view', ts, 'page.view', null, null, null, path, ip
                from audit.access where actor_id = %(u)s and kind = 'page' and (%(c)s = '' or ts < %(c)s::timestamptz)
            ) x order by ts desc limit 101""", {"u": ctx.user_id, "c": cursor})
        rows = cur.fetchall()
    return envelope(rows[:100], next_cursor=rows[99]["ts"].isoformat() if len(rows) > 100 else None)


@router.get("/home")
def home(ctx: Ctx = Depends(require("home.view"))):
    alerts = []
    with db.tx(ctx.scope) as cur:
        allp = portals_mod.list_for(cur, ctx)
        cur.execute("select portal_id, max(ts) t from portal.usage where user_id = %s group by 1 order by 2 desc limit 6",
                    (ctx.user_id,))
        recent_ids = [str(r["portal_id"]) for r in cur.fetchall()]
        cur.execute("select c.code, c.name, c.cadence_minutes, s.as_of from data.contract c"
                    " left join data.dataset_state s on s.dataset = c.code where c.enabled order by c.name")
        ds = [{"code": r["code"], "name": r["name"], "as_of": r["as_of"].isoformat() if r["as_of"] else None,
               "freshness": freshness(r["as_of"], r["cadence_minutes"])} for r in cur.fetchall()]
    for p in allp:
        if p["pinned"] and p["status"] == "down":
            alerts.append({"severity": "red", "title": "%s is not responding" % p["name"], "detail": p["status_detail"],
                           "link": "/portals"})
    if ctx.can("data.view"):
        for d in ds:
            if d["freshness"] in ("stale", "late"):
                alerts.append({"severity": "red" if d["freshness"] == "late" else "amber",
                               "title": "%s data is %s" % (d["name"], d["freshness"]), "detail": "As of %s" % d["as_of"],
                               "link": "/data"})
    ct = None
    if ctx.can("ct.view") or ctx.can("ct.view_all"):
        from . import control_tower
        p, t = control_tower.latest()
        if p:
            reps = control_tower._visible_reports(p, ctx)
            ct = {"counts": control_tower._counts(reps), "refreshed_at": t.isoformat()}
            bad = [r for r in reps if r["status"] in ("FAILED", "STALE")]
            if bad:
                alerts.append({"severity": "red" if any(r["status"] == "FAILED" for r in bad) else "amber",
                               "title": "%d report%s need attention" % (len(bad), "" if len(bad) == 1 else "s"),
                               "detail": ", ".join(r["name"] for r in bad[:4]) + ("…" if len(bad) > 4 else ""),
                               "link": "/control-tower"})
    byid = {p["id"]: p for p in allp}
    return {"greeting_name": ctx.display_name.split(" ")[0].title(), "alerts": alerts,
            "pinned_portals": [p for p in allp if p["pinned"]],
            "recent_portals": [byid[i] for i in recent_ids if i in byid],
            "datasets": ds if ctx.can("data.view") else [], "ct_summary": ct,
            "my_scope": {"label": ctx.scope_label, "store_count": ctx.store_count}}


@router.get("/org/stores")
def stores(q: str = "", zone: str = "", region: str = "", cluster: str = "", cursor: str = "", limit: int = 50,
           ctx: Ctx = Depends(require("org.stores.view"))):
    limit = max(1, min(limit, 500))
    off = int(cursor) if cursor.isdigit() else 0
    like = "%" + q.lower() + "%"
    with db.tx(ctx.scope) as cur:   # RLS on snap.store_directory limits rows to the caller's scope
        cur.execute("select * from snap.store_directory where (%s = '' or lower(store_code) like %s or lower(store_name) like %s)"
                    " and (%s = '' or zone_code = %s) and (%s = '' or region_code = %s) and (%s = '' or cluster_code = %s)"
                    " order by store_code offset %s limit %s",
                    (q, like, like, zone, zone, region, region, cluster, cluster, off, limit + 1))
        rows = cur.fetchall()
        cur.execute("select s.as_of, c.cadence_minutes from data.contract c left join data.dataset_state s on s.dataset = c.code"
                    " where c.code = 'store_directory'")
        st = cur.fetchone() or {}
    keep = ("store_code", "store_name", "state", "zone_code", "region_code", "cluster_code", "cluster_head_ecode",
            "cluster_head_name")
    as_of = st.get("as_of")
    return envelope([{k: r[k] for k in keep} for r in rows[:limit]], as_of=as_of.isoformat() if as_of else None,
                    freshness=freshness(as_of, st.get("cadence_minutes") or 1440), dataset="store_directory",
                    scope=ctx.scope_label, next_cursor=str(off + limit) if len(rows) > limit else None)


@router.get("/data/datasets")
def datasets(ctx: Ctx = Depends(require("data.view"))):
    with db.tx() as cur:
        cur.execute("""select c.*, s.as_of, s.rows, r.started_at r_started, r.finished_at r_finished, r.verdict r_verdict,
                              r.rows r_rows, r.reason r_reason
                       from data.contract c left join data.dataset_state s on s.dataset = c.code
                       left join lateral (select * from data.refresh x where x.dataset = c.code order by id desc limit 1) r on true
                       order by c.name""")
        out = [{"code": r["code"], "name": r["name"], "description": r["description"], "owner_dept": r["owner_dept"],
                "source_object": r["source_object"], "cadence_minutes": r["cadence_minutes"], "rows": r["rows"],
                "freshness": freshness(r["as_of"], r["cadence_minutes"]),
                "as_of": r["as_of"].isoformat() if r["as_of"] else None,
                "last_refresh": {"started_at": r["r_started"], "finished_at": r["r_finished"], "verdict": r["r_verdict"],
                                 "rows": r["r_rows"], "reason": r["r_reason"]} if r["r_verdict"] else None}
               for r in cur.fetchall()]
    return envelope(out)


@router.get("/data/datasets/{code}/refreshes")
def refreshes(code: str, ctx: Ctx = Depends(require("data.view"))):
    with db.tx() as cur:
        cur.execute("select * from data.refresh where dataset = %s order by id desc limit 100", (code,))
        return envelope(cur.fetchall(), dataset=code)


@router.post("/data/datasets/{code}/refresh", status_code=202)
def refresh_now(code: str, request: Request, ctx: Ctx = Depends(require("data.refresh"))):
    job = "refresh:" + code
    with db.tx() as cur:
        cur.execute("select 1 from ops.job where code = %s", (job,))
        if not cur.fetchone():
            raise HTTPException(404, "No such dataset.")
        cur.execute("select 1 from ops.job_request where job_code = %s and claimed_at is null", (job,))
        if cur.fetchone():
            raise HTTPException(409, "A refresh is already queued.")
        cur.execute("insert into ops.job_request (job_code, requested_by) values (%s,%s) returning id", (job, ctx.user_id))
        rid = cur.fetchone()["id"]
        audit.action(cur, ctx, "data.refresh.request", "dataset", code, req=request)
    return {"job_run_id": rid}
