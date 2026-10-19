"""Portal directory: every company portal in one place, each with owner, audience and a live
up/down check. Opening a portal goes through V2Pulse so every open is logged."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import audit, db
from ..config import settings
from ..iam.context import Ctx
from ..web import envelope, require

router = APIRouter(prefix="/api/v1")

CATEGORIES = ["Data & Reports", "Stores & Operations", "Planning & Merchandising", "Sourcing & Vendors", "Supply Chain",
              "Expansion & Marketing", "Finance", "HR & People", "Customer & CRM", "Systems & APIs"]

DC = "http://DataCenter.v2rd.com"
# code, name, description, category, url, owner_dept, login, network, icon, audience, placeholder, how_to_get_access
SEED = [
    ("report-portal", "Report Portal", "Status, freshness and re-runs for every scheduled Snowflake report and SAP/HRMS load.",
     "Data & Reports", DC + ":8874", "PLATFORM", "own", "lan", "chart", [], False,
     "Ask the data platform team for a Report Portal login."),
    ("loyalty-dashboard", "Loyalty card dashboard", "Loyalty card sales and card holders, refreshed hourly.",
     "Customer & CRM", DC + ":8873", "CRM", "none", "lan", "card", ["EXEC", "PORTAL_ADMIN", "PLATFORM", "MIS_CENTRAL"], False, ""),
    ("cctv-dashboard", "CCTV analytics", "Footfall and camera analytics from store CCTV.",
     "Stores & Operations", DC + ":8501", "IT_ADMIN", "none", "lan", "camera", ["EXEC", "PORTAL_ADMIN", "PLATFORM", "IT_ADMIN"], False, ""),
    ("closing-stock-api", "Closing-stock API", "Store x article closing stock (POST API; this link opens its documentation).",
     "Systems & APIs", DC + ":8872/docs", "PLATFORM", "own", "lan", "api", ["PLATFORM", "PORTAL_ADMIN"], False, ""),
    ("face-recognition-api", "Face-recognition attendance API", "CCTV face-recognition attendance service.",
     "Systems & APIs", DC + ":8871/docs", "PLATFORM", "own", "lan", "api", ["PLATFORM", "PORTAL_ADMIN"], False, ""),
    ("frams", "FRAMS attendance", "Face-recognition attendance management system.",
     "HR & People", "https://192.168.155.124:19051", "HR", "own", "lan", "users", ["HR", "PLATFORM", "PORTAL_ADMIN"], False, ""),
    ("snowsight", "Snowflake (Snowsight)", "The data warehouse console.",
     "Data & Reports", "https://app.snowflake.com/iafphkw/hh80816", "PLATFORM", "own", "internet", "database",
     ["PLATFORM", "PORTAL_ADMIN", "MIS_CENTRAL"], False, "Snowflake accounts are created by the data platform team."),
    ("srm", "SRM vendor portal", "Vendors, PPT and PO routing, T&A, ASN.", "Sourcing & Vendors", "https://srm.v2retail.com",
     "SOURCING", "own", "internet", "truck", [], False, "Ask the sourcing team for SRM access."),
    ("karma", "Karma", "Store issues, tasks and delegations.", "Stores & Operations", "https://karma.v2kart.com", "RETAIL_OPS",
     "own", "internet", "check", [], False, ""),
    ("v2-parivar", "V2 Parivar (HRMS)", "Employee self-service: attendance, leave, payslips.", "HR & People",
     "https://v2parivar.v2retail.com:9988/", "HR", "own", "internet", "users", [], False, "Sign in with your employee code."),
    ("dfs", "DFS planning", "Sale plans, MIS and inventory reports.", "Planning & Merchandising", "#", "MERCH", "own",
     "internet", "chart", [], True, ""),
    ("retail-hub", "Retail Hub", "Store checklists and hierarchy.", "Stores & Operations", "#", "RETAIL_OPS", "own",
     "internet", "store", [], True, ""),
    ("sap", "SAP S/4HANA", "SAP Fiori launchpad.", "Systems & APIs", "#", "IT_ADMIN", "own", "lan", "box", [], True, ""),
    ("sap-sync-monitor", "SAP SYNC monitor", "Streamlit-in-Snowflake monitor of SAP and HRMS loads.", "Data & Reports", "#",
     "PLATFORM", "own", "internet", "refresh", ["PLATFORM", "PORTAL_ADMIN"], True, ""),
    # From the V2 PORTAL LIST (Oct 2026). Portals still in development are visible to admins only.
    ("po-wardrobe", "PO Wardrobe", "Buying app for purchase orders and range buys (B&M).", "Planning & Merchandising",
     "https://buying.v2retail.net", "MERCH", "own", "internet", "box", [], False, ""),
    ("article-creation", "Article creation (MDM)", "Create and maintain articles in the master data workflow (B&M / MDM).",
     "Planning & Merchandising", "https://articlecreation.v2retail.net", "MERCH", "own", "internet", "box", [], False, ""),
    ("tech-pack", "Tech pack creation", "Product development tech packs (in development).", "Planning & Merchandising",
     "https://craftpack-studio.lovable.app", "SOURCING", "own", "internet", "box", ["PORTAL_ADMIN", "PLATFORM"], False, ""),
    ("retail-app", "Retail app & LMS", "Store operations, loss prevention and the learning management system (LMS).",
     "Stores & Operations", "https://retail.v2kart.in", "RETAIL_OPS", "own", "internet", "store", [], False, ""),
    ("dc-ops", "DC operations portal", "Distribution centre operations (in development).", "Supply Chain",
     "https://dc-flow-master.lovable.app", "WAREHOUSE", "own", "internet", "truck", ["PORTAL_ADMIN", "PLATFORM"], False, ""),
    ("logistics", "Logistics portal", "Transport and logistics management.", "Supply Chain", "https://transport.v2retail.net/",
     "WAREHOUSE", "own", "internet", "truck", [], False, ""),
    ("hub-ops", "Hub operations portal", "Hub operations for supply chain.", "Supply Chain", "https://hubs.v2retail.net",
     "WAREHOUSE", "own", "internet", "truck", [], False, ""),
    ("nso-ops", "NSO, Marketing & BD ops", "Shared portal for Marketing ops (live) and the BD, NSO, Project and NSO-IT modules "
     "(rolling out).", "Expansion & Marketing", "https://nso.v2retail.net/", "CRM", "own", "internet", "chart", [], False, ""),
    ("finance-hub", "Finance Hub", "Finance operations portal.", "Finance", "https://finance.v2retail.net", "FINANCE", "own",
     "internet", "card", [], False, ""),
    # V2Pulse's own page for renaming the Reports / Masters trees (pulse/manage/trees.html); shown to tree editors.
    ("library-trees", "Library tree names", "Rename the top-level trees of the Reports and Masters libraries (Central and "
     "each department).", "Data & Reports", "/manage/trees", "PLATFORM", "sso", "lan", "folder",
     ["PORTAL_ADMIN", "PLATFORM", "MIS_CENTRAL", "DEPT_MIS_OWNER"], False,
     "Needs edit rights on a library tree: Central MIS, department MIS owner, data platform or portal admin."),
]


def seed(cur):
    for (code, name, desc, cat, url, dept, login, net, icon, aud, ph, how) in SEED:
        cur.execute(
            "insert into portal.portal (code, name, description, category, url, owner_dept, login, network, icon,"
            " audience_roles, is_placeholder, how_to_get_access, sort_order) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " on conflict (code) do nothing",
            (code, name, desc, cat, url, dept, login, net, icon, aud, ph, how, 100 + SEED.index(
                next(s for s in SEED if s[0] == code)) * 10))
    # A relative link keeps the viewer's own sign-in (IP or host name); the up/down check needs a full address.
    cur.execute("update portal.portal set health_url = %s where code = 'library-trees' and health_url is null",
                (settings().base_url.rstrip("/") + "/manage/trees",))


def visible(p: dict, ctx: Ctx) -> bool:
    return p["is_active"] and (not p["audience_roles"] or bool(set(p["audience_roles"]) & set(ctx.roles))
                               or ctx.can("portals.manage"))


def _shape(p: dict, pinned: set) -> dict:
    return {"id": str(p["id"]), "code": p["code"], "name": p["name"], "description": p["description"],
            "category": p["category"], "url": None if p["is_placeholder"] else p["url"], "owner_dept": p["owner_dept"],
            "owner_name": p["owner_name"], "login": p["login"], "network": p["network"], "icon": p["icon"],
            "tags": p["tags"], "pinned": p["id"] in pinned,
            "status": "unknown" if p["is_placeholder"] else p["status"],
            "status_checked_at": p["status_checked_at"].isoformat() if p["status_checked_at"] else None,
            "latency_ms": p["latency_ms"], "status_detail": "Link not confirmed yet" if p["is_placeholder"] else p["status_detail"],
            "how_to_get_access": p["how_to_get_access"], "is_placeholder": p["is_placeholder"]}


def list_for(cur, ctx: Ctx) -> list[dict]:
    cur.execute("select * from portal.portal order by sort_order, name")
    allp = cur.fetchall()
    cur.execute("select portal_id from portal.pin where user_id = %s", (ctx.user_id,))
    pins = {r["portal_id"] for r in cur.fetchall()}
    out = [_shape(p, pins) for p in allp if visible(p, ctx)]
    out.sort(key=lambda x: (not x["pinned"],))
    return out


@router.get("/portals")
def portals(ctx: Ctx = Depends(require("portals.view"))):
    with db.tx(ctx.scope) as cur:
        data = list_for(cur, ctx)
    cats = [c for c in CATEGORIES if any(p["category"] == c for p in data)]
    cats += sorted({p["category"] for p in data} - set(cats))
    return envelope(data, categories=cats)


def _get_visible(cur, pid, ctx):
    cur.execute("select * from portal.portal where id = %s", (pid,))
    p = cur.fetchone()
    if not p or not visible(p, ctx):
        raise HTTPException(404, "Portal not found.")
    return p


@router.post("/portals/{pid}/open")
def open_portal(pid: str, request: Request, ctx: Ctx = Depends(require("portals.view"))):
    with db.tx(ctx.scope) as cur:
        p = _get_visible(cur, pid, ctx)
        if p["is_placeholder"]:
            raise HTTPException(409, "This portal's link has not been confirmed yet.")
        cur.execute("insert into portal.usage (user_id, portal_id) values (%s,%s)", (ctx.user_id, pid))
        audit.action(cur, ctx, "portal.open", "portal", p["code"], {"url": p["url"]}, req=request)
    return {"url": p["url"]}


@router.put("/portals/{pid}/pin")
def pin(pid: str, request: Request, ctx: Ctx = Depends(require("portals.view"))):
    with db.tx(ctx.scope) as cur:
        p = _get_visible(cur, pid, ctx)
        cur.execute("insert into portal.pin (user_id, portal_id) values (%s,%s) on conflict do nothing", (ctx.user_id, pid))
        audit.action(cur, ctx, "portal.pin", "portal", p["code"], req=request)
    return {"ok": True}


@router.delete("/portals/{pid}/pin")
def unpin(pid: str, request: Request, ctx: Ctx = Depends(require("portals.view"))):
    with db.tx(ctx.scope) as cur:
        p = _get_visible(cur, pid, ctx)
        cur.execute("delete from portal.pin where user_id = %s and portal_id = %s", (ctx.user_id, pid))
        audit.action(cur, ctx, "portal.unpin", "portal", p["code"], req=request)
    return {"ok": True}


# ---------------------------------------------------------------- admin
class PortalIn(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,60}$")
    name: str = Field(min_length=2, max_length=120)
    description: str = ""
    category: str = Field(min_length=2, max_length=60)
    url: str = Field(min_length=1, max_length=500)
    health_url: str | None = None
    owner_dept: str | None = None
    owner_name: str | None = None
    login: str = Field("own", pattern=r"^(own|sso|none)$")
    network: str = Field("lan", pattern=r"^(lan|internet)$")
    icon: str = "app"
    tags: list[str] = []
    audience_roles: list[str] = []
    how_to_get_access: str = ""
    is_active: bool = True
    is_placeholder: bool = False
    sort_order: int = 100


FIELDS = list(PortalIn.model_fields)


def _check_url(body: PortalIn):
    if not body.is_placeholder and not body.url.startswith(("http://", "https://")):
        raise HTTPException(422, "The link must start with http:// or https:// (or mark the portal as a placeholder).")


@router.get("/admin/portals")
def admin_portals(ctx: Ctx = Depends(require("portals.manage"))):
    with db.tx() as cur:
        cur.execute("select p.*, (select count(*) from portal.usage u where u.portal_id = p.id and u.ts > now() - interval '30 days')"
                    " opens_30d from portal.portal p order by sort_order, name")
        rows = cur.fetchall()
    return envelope([{**{k: (str(v) if k in ("id", "updated_by") and v else v) for k, v in r.items()}} for r in rows])


@router.post("/admin/portals")
def create_portal(body: PortalIn, request: Request, ctx: Ctx = Depends(require("portals.manage"))):
    _check_url(body)
    with db.tx() as cur:
        cur.execute("select 1 from portal.portal where code = %s", (body.code,))
        if cur.fetchone():
            raise HTTPException(409, "A portal with code %s already exists." % body.code)
        cols = FIELDS + ["updated_by"]
        cur.execute("insert into portal.portal (%s) values (%s) returning id" % (", ".join(cols), ", ".join(["%s"] * len(cols))),
                    [getattr(body, f) for f in FIELDS] + [ctx.user_id])
        pid = cur.fetchone()["id"]
        audit.action(cur, ctx, "portal.create", "portal", body.code, body.model_dump(), req=request)
    return {"id": str(pid)}


@router.put("/admin/portals/{pid}")
def update_portal(pid: str, body: PortalIn, request: Request, ctx: Ctx = Depends(require("portals.manage"))):
    _check_url(body)
    with db.tx() as cur:
        cur.execute("select * from portal.portal where id = %s for update", (pid,))
        before = cur.fetchone()
        if not before:
            raise HTTPException(404, "Portal not found.")
        new = body.model_dump()
        changes = {k: {"from": before[k], "to": v} for k, v in new.items() if before.get(k) != v}
        cur.execute("update portal.portal set %s, updated_at = now(), updated_by = %%s where id = %%s"
                    % ", ".join("%s = %%s" % f for f in FIELDS), [new[f] for f in FIELDS] + [ctx.user_id, pid])
        audit.action(cur, ctx, "portal.update", "portal", body.code, {"changes": changes}, req=request)
    return {"ok": True}


@router.post("/admin/portals/{pid}/check")
def check_now(pid: str, request: Request, ctx: Ctx = Depends(require("portals.manage"))):
    with db.tx() as cur:
        cur.execute("select * from portal.portal where id = %s", (pid,))
        p = cur.fetchone()
    if not p:
        raise HTTPException(404, "Portal not found.")
    res = probe(p)
    _save([res])
    audit.action_now(ctx, "portal.check", "portal", p["code"], res, req=request)
    return res


# ---------------------------------------------------------------- health checks
def probe(p: dict) -> dict:
    """GET the portal (or its health_url). 401/403 still mean 'up': the portal answered, it wants a login."""
    if p["is_placeholder"] or not p["url"].startswith("http"):
        return {"id": p["id"], "status": "unknown", "latency_ms": None, "detail": "No link to check"}
    url = p["health_url"] or p["url"]
    t0 = time.perf_counter()
    try:
        r = httpx.get(url, timeout=settings().portal_check_timeout_s, follow_redirects=True, verify=False)
        ms = int((time.perf_counter() - t0) * 1000)
        if r.status_code >= 500:
            return {"id": p["id"], "status": "down", "latency_ms": ms, "detail": "HTTP %d" % r.status_code}
        status = "degraded" if ms > 3000 else "up"
        return {"id": p["id"], "status": status, "latency_ms": ms,
                "detail": "HTTP %d%s" % (r.status_code, " (slow)" if status == "degraded" else "")}
    except httpx.TimeoutException:
        return {"id": p["id"], "status": "down", "latency_ms": None, "detail": "No answer in %ss" % settings().portal_check_timeout_s}
    except Exception as e:  # noqa: BLE001
        return {"id": p["id"], "status": "down", "latency_ms": None, "detail": type(e).__name__ + ": " + str(e)[:150]}


def _save(results):
    with db.tx() as cur:
        for r in results:
            cur.execute("update portal.portal set status = %s, latency_ms = %s, status_detail = %s, status_checked_at = now()"
                        " where id = %s", (r["status"], r["latency_ms"], r["detail"], r["id"]))


def check_all() -> dict:
    """Worker job: probe every active portal in parallel; log state changes as actions."""
    with db.tx() as cur:
        cur.execute("select * from portal.portal where is_active")
        ps = cur.fetchall()
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(probe, ps))
    before = {p["id"]: p["status"] for p in ps}
    _save(results)
    changed = [r for r in results if before.get(r["id"]) not in (r["status"], "unchecked")]
    for r in changed:
        code = next(p["code"] for p in ps if p["id"] == r["id"])
        audit.action_now(None, "portal.status_change", "portal", code, {"from": before[r["id"]], "to": r["status"],
                                                                        "detail": r["detail"]})
    return {"checked": len(results), "down": sum(1 for r in results if r["status"] == "down"), "changed": len(changed)}
