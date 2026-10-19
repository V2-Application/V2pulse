"""Analytics landing page (/analytics): the dashboards of the Analytics category that the caller may open, with
their page names, grouped by subject. Visibility is exactly the report library's (bi.reports); headline figures
on the page come from the normal BI query endpoint, so store scope and audiences apply there too."""
from fastapi import APIRouter, Depends, Request

from .. import audit, db
from ..iam.context import Ctx
from ..web import require
from . import bi
try:
    from .alc_gap_catalog import CATEGORY as ALC_CATEGORY
except Exception:  # noqa: BLE001 - optional pack; see hero_catalog guard (restored 2026-10-17)
    ALC_CATEGORY = 'Allocation gap reports'
from . import bm_dashboards
from .hero_catalog import CATEGORY

router = APIRouter(prefix="/api/v1/analytics")

# Subject of each dataset, in the order the landing page shows them.
GROUPS = [("alloc", "Allocation gap report", "The allocation team's 22 gap checks on every ARS run: store, "
                                            "store x major-category, company major-category and generic-colour gaps.",
           ("alc_gap", "alc_gap_trend")),
          ("ai", "AI suggestions", "Alerts and suggested actions found this morning, with Claude's written brief.",
           ("ai_suggestions",)),
          ("fill", "Stores · Fill rate", "Sales-area stock against the ARS MBQ, by zone, store and category.",
           ("store_fill_rate",)),
          ("hero", "Merchandise · Hero articles", "Top sellers across stores, the merchandise hierarchy and time, "
                                                   "their stock and gaps.", ("analytics", "hero_position", "hero_sales")),
          ("karma", "People · Karma productivity", "HO Y/N checklist compliance, hours, maker-checker and tasks.",
           ("karma_emp", "karma_day")),
          ("bm", "Merchandising · B&M reports", "A dashboard for each scheduled Merchandising & Planning report: where "
                                                 "the gaps are and how big.",
           tuple(d["code"] for d in bm_dashboards.DATASETS if d["code"].startswith("bm_"))),
          ("srm", "Sourcing · SRM reports", "A dashboard for each scheduled Sourcing report: what is pending, where "
                                            "and for how long.",
           tuple(d["code"] for d in bm_dashboards.DATASETS if d["code"].startswith("srm_")))]


@router.get("/catalog")
def catalog(request: Request, ctx: Ctx = Depends(require("analytics.view"))):
    lib = [r for r in bi.reports("library", ctx)["data"]
           if r["category"] in (CATEGORY, ALC_CATEGORY, bm_dashboards.MERCH_CATEGORY, bm_dashboards.SRM_CATEGORY)]
    with db.tx() as cur:
        cur.execute("select id::text id, definition from bi.report where id = any(%s::uuid[])", ([r["id"] for r in lib],))
        pages = {r["id"]: [p.get("name") for p in (r["definition"] or {}).get("pages", [])] for r in cur.fetchall()}
        audit.action(cur, ctx, "analytics.home.open", "page", "analytics", {"dashboards": len(lib)}, req=request)
    out = []
    for key, title, blurb, datasets in GROUPS:
        items = [{"id": r["id"], "name": r["name"], "description": r["description"], "dataset": r["dataset"],
                  "pages": pages.get(r["id"], []), "updated_at": r["updated_at"]}
                 for r in lib if r["dataset"] in datasets]
        # The all-pages dashboard of a subject first, then by name.
        items.sort(key=lambda x: (len(x["pages"]) < 12, not x["name"].endswith("Dashboard"), x["name"]))
        if items:
            out.append({"key": key, "title": title, "description": blurb, "dashboards": items})
    others = [r for r in lib if not any(r["dataset"] in g[3] for g in GROUPS)]
    if others:
        out.append({"key": "other", "title": "More analytics", "description": "", "dashboards": [
            {"id": r["id"], "name": r["name"], "description": r["description"], "dataset": r["dataset"],
             "pages": pages.get(r["id"], []), "updated_at": r["updated_at"]} for r in others]})
    return {"data": out, "user": {"name": ctx.display_name, "scope": ctx.scope_label}}
