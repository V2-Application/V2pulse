"""MIS (ported from DFS / sale-plan): saved MIS reports, sale / stock trend, day-wise trend, load monitor,
dependencies and product master, under /api/v1/mis/*. Pages are served from pulse/mis_ui.

The engine (SQL, catalogue, windows, conditions) is the DFS ml-engine code copied into pulse.mis_engine and
mounted here unchanged, so reports give the same numbers as DFS. V2Pulse adds what DFS left to the browser:
every route needs a V2Pulse sign-in and a mis.* permission, and the report owner is the signed-in employee
code (not a name the browser sends). Saved reports are shared with DFS (Snowflake BRONZE.MIS_REPORT_DEF)."""
import io
import json
import os
import re
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from .. import audit, db
from ..config import settings
from ..iam.context import Ctx
from ..mis_engine import CURRENT_USER
from ..mis_engine import export as mx
from ..mis_engine.routes import (sf_alloc_gap, sf_ars_alloc, sf_bin_report, sf_scm, sf_day_trend, sf_day_trend_report, sf_inv_dash,
                                 sf_inv_report, sf_mis_lineage, sf_mis_monitor, sf_mis_report, sf_mis_trend, sf_prod_master,
                                 sf_tna, sf_var_combined, sf_vert_inv_art)
from ..web import current, require

PREFIX = "/api/v1/mis"
VIEW, EXPORT, MANAGE, ADMIN = "mis.view", "mis.export", "mis.manage", "mis.admin"


# Engine calls that CHANGE data (loads, builds, view publishing) - mis.admin only.
ADMIN_CALLS = {("monitor", "/cache/clear"), ("lineage", "/drop-orphans"), ("var-combined", "/load"),
               ("var-combined", "/kpi-load"), ("vert-inv-art", "/build"), ("inv-report", "/publish-all"),
               ("inv-dash", "/build"), ("scm", "/custom"), ("scm", "/custom/preview")}


def _rule(section: str, method: str, sub: str) -> str:
    """The permission one engine call needs (DFS checked these only in the browser)."""
    if (section, sub) in ADMIN_CALLS or (section == "scm" and sub.startswith("/custom") and method != "GET"):
        return ADMIN
    if sub.endswith("/export") or (section, sub) == ("inv-report", "/split"):
        return EXPORT
    if section in ("report", "day-trend-report", "inv-report") and sub.startswith("/reports") and method in ("POST", "DELETE"):
        return MANAGE
    return VIEW


def _guard(section: str):
    def dep(request: Request, ctx: Ctx = Depends(current)) -> Ctx:
        sub = request.url.path[len(PREFIX) + 1 + len(section):] or "/"
        perm = _rule(section, request.method, sub)
        if not ctx.can(perm):
            audit.action_now(ctx, "access.denied", "permission", perm, {"path": request.url.path}, outcome="denied",
                             req=request)
            raise HTTPException(403, "You do not have access to this. Ask your V2Pulse administrator.")
        return ctx
    return dep


async def _bind_user(request: Request):
    """Report owner = the signed-in employee code. Async, so the value reaches the engine's worker thread."""
    ctx = request.state.ctx
    CURRENT_USER.set(ctx.emp_code if ctx else None)


# (section, engine router); mounted in app.py through ROUTERS
ENGINE = [("report", sf_mis_report.router), ("trend", sf_mis_trend.router), ("day-trend", sf_day_trend.router),
          ("day-trend-report", sf_day_trend_report.router), ("monitor", sf_mis_monitor.router),
          ("lineage", sf_mis_lineage.router), ("product-master", sf_prod_master.router),
          ("var-combined", sf_var_combined.router), ("vert-inv-art", sf_vert_inv_art.router),
          ("inv-report", sf_inv_report.router), ("bin", sf_bin_report.router), ("tna", sf_tna.router),
          ("inv-dash", sf_inv_dash.router), ("alloc", sf_ars_alloc.router), ("alloc-gap", sf_alloc_gap.router),
          ("scm", sf_scm.router)]


def include(app):
    for section, r in ENGINE:
        app.include_router(r, prefix=f"{PREFIX}/{section}", tags=["mis"],
                           dependencies=[Depends(_guard(section)), Depends(_bind_user)])
    app.include_router(router)
    from . import mis_plan360, mis_store_master
    app.include_router(mis_store_master.router)
    app.include_router(mis_plan360.router)

    # A Snowflake refusal (no grant, missing object, bad SQL) reaches the page with Snowflake's own reason instead of
    # the generic "error was logged" - MIS pages show it in their notice boxes.
    from snowflake.connector.errors import DatabaseError
    from ..web import problem

    @app.exception_handler(DatabaseError)
    async def _snowflake_problem(request: Request, exc: DatabaseError):
        msg = str(getattr(exc, "msg", "") or exc).strip().splitlines()
        return problem(502, "Snowflake refused this", (msg[-1] if msg else "Snowflake error")[:600], request)


router = APIRouter(prefix=PREFIX, tags=["mis"])


# ── Monitor: load a gap (DFS: planning-service /sf-mis-monitor-load-gap -> ml-engine /sf-var-combined/load) ──
class LoadGap(BaseModel):
    from_date: str
    to_date: str
    stores: list[str] = []


@router.post("/monitor/load-gap")
def load_gap(body: LoadGap, request: Request, ctx: Ctx = Depends(require(ADMIN))):
    """Re-load sale and stock for a date range (max 31 days) and optional stores: CALL the two BRONZE load
    procedures, logged in BRONZE.MIS_LOAD_RUN as triggered by the monitor."""
    req = sf_var_combined.LoadReq(from_date=body.from_date, to_date=body.to_date, stores=body.stores or None,
                                  triggered_by="monitor", operator=ctx.emp_code)
    out = sf_var_combined.sf_var_combined_load(req)
    audit.action_now(ctx, "mis.monitor.load_gap", "mis", "day_wise_load", body.model_dump(), req=request)
    return out


# ── Excel output: one report as .xlsx, and the multi-report workbook (DFS: Node exceljs) ─────────────
def _run_req(d: dict) -> sf_mis_report.RunReq:
    return sf_mis_report.RunReq(**{k: v for k, v in d.items() if k in sf_mis_report.RunReq.model_fields})


def _xlsx(buf: bytes, filename: str):
    return Response(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/report/export-xlsx")
def export_xlsx(body: dict, request: Request, ctx: Ctx = Depends(require(EXPORT))):
    req = _run_req(body)
    CURRENT_USER.set(ctx.emp_code)
    buf = io.BytesIO()
    out = mx.write_xlsx(buf, [(None, req)])
    audit.action_now(ctx, "mis.report.export", "mis_report", req.slug or "preview",
                     {"format": "xlsx", "rows": out[0]["rows"], "from": req.from_date, "to": req.to_date}, req=request)
    return _xlsx(buf.getvalue(), f"{mx.file_safe(out[0]['name'])}_{req.from_date}_to_{req.to_date}.xlsx")


class BookSheet(BaseModel):
    slug: str
    sheet: str | None = None
    filters: dict | None = None
    col_filters: list | None = None
    layout: str | None = None


class Book(BaseModel):
    name: str = "MIS workbook"
    from_date: str
    to_date: str
    sheets: list[BookSheet]


@router.post("/book")
def book(body: Book, request: Request, ctx: Ctx = Depends(require(EXPORT))):
    """Several saved reports, one sheet each, in one workbook (DFS 'multi-report workbook')."""
    if not body.sheets or len(body.sheets) > 30:
        raise HTTPException(422, "Pick 1 to 30 reports.")
    CURRENT_USER.set(ctx.emp_code)
    reqs = [(s.sheet, sf_mis_report.RunReq(slug=s.slug, from_date=body.from_date, to_date=body.to_date, filters=s.filters,
                                           col_filters=s.col_filters, layout=s.layout)) for s in body.sheets]
    buf = io.BytesIO()
    try:
        out = mx.write_xlsx(buf, reqs)
    except ValueError as e:
        raise HTTPException(422, str(e))
    audit.action_now(ctx, "mis.book.export", "mis_report", ",".join(s.slug for s in body.sheets)[:200],
                     {"sheets": out, "from": body.from_date, "to": body.to_date}, req=request)
    return _xlsx(buf.getvalue(), f"{mx.file_safe(body.name)}_{body.from_date}_to_{body.to_date}.xlsx")


# ── Report templates (DFS: planning-service templates/MIS/<slug>.xlsx) ─────────────────────────────────
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")


def _tpl_dir():
    d = settings().mis_template_dir
    os.makedirs(d, exist_ok=True)
    return d


def _slug(slug):
    if not _SLUG.match(slug or ""):
        raise HTTPException(400, "bad report URL")
    return slug


class _Plain:
    """Stands in for sf_cancel's QueryGuard where nothing can disconnect (server-side calls)."""
    @staticmethod
    def execute(cur, sql, *a, **k):
        return cur.execute(sql, *a, **k)


def _report_columns(slug):
    j = sf_mis_report._mis_run_body(sf_mis_report.RunReq(slug=slug, from_date="2026-01-01", to_date="2026-01-01",
                                                         limit=1), _Plain)
    return j.get("columns") or [], j.get("name") or slug


@router.get("/template/{slug}")
def template_get(slug: str, ctx: Ctx = Depends(require(VIEW))):
    p = os.path.join(_tpl_dir(), f"{_slug(slug)}.xlsx")
    if not os.path.isfile(p):
        raise HTTPException(404, "No template uploaded for this report yet.")
    with open(p, "rb") as fh:
        return _xlsx(fh.read(), f"{slug}-TEMPLATE.xlsx")


@router.get("/template/{slug}/info")
def template_info(slug: str, ctx: Ctx = Depends(require(VIEW))):
    folder = _tpl_dir()
    p = os.path.join(folder, f"{_slug(slug)}.xlsx")
    meta = {}
    try:
        with open(p + ".meta.json", encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        pass
    rows = mx.template_rows(folder, slug)
    return {"exists": os.path.isfile(p), "bytes": os.path.getsize(p) if os.path.isfile(p) else 0,
            "nameRow": rows["nameRow"], "dataRow": rows["dataRow"], "updatedAt": meta.get("updatedAt"),
            "updatedBy": meta.get("updatedBy"), "sourceFile": meta.get("sourceFile"),
            "mappedColumns": meta.get("mappedColumns")}


try:   # multipart uploads need python-multipart (requirements.txt); without it only the upload is missing
    import python_multipart  # noqa: F401
    _UPLOADS = True
except ImportError:
    _UPLOADS = False


async def template_upload(slug: str, request: Request, file: UploadFile = File(...), nameRow: int = Form(1),
                          dataRow: int = Form(0), ctx: Ctx = Depends(require(MANAGE))):
    import openpyxl
    _slug(slug)
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "Template is larger than 25 MB.")
    if nameRow < 1 or dataRow < 0 or (dataRow and dataRow <= nameRow):
        raise HTTPException(422, "Column-name row must be 1 or more; data row must be 0 (append) or below the name row.")
    try:
        ws = openpyxl.load_workbook(io.BytesIO(data)).worksheets[0]
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Not a readable .xlsx workbook: {e}")
    from starlette.concurrency import run_in_threadpool
    CURRENT_USER.set(ctx.emp_code)
    columns, rname = await run_in_threadpool(_report_columns, slug)
    valid = {str(c).upper() for c in columns}
    names = [n for _, n in mx._row_names(ws, nameRow)]
    unknown = [n for n in names if n not in valid]
    folder = _tpl_dir()
    p = os.path.join(folder, f"{slug}.xlsx")
    archived = None
    if os.path.isfile(p):
        archived = f"{p}.{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}.bak"
        os.replace(p, archived)
    with open(p, "wb") as fh:
        fh.write(data)
    with open(p + ".meta.json", "w", encoding="utf-8") as fh:
        json.dump({"nameRow": nameRow, "dataRow": dataRow, "updatedAt": datetime.now().isoformat(timespec="seconds"),
                   "updatedBy": ctx.emp_code, "sourceFile": file.filename, "mappedColumns": len(names)}, fh, indent=2)
    audit.action_now(ctx, "mis.template.upload", "mis_report", slug, {"file": file.filename, "matched": len(names) - len(unknown)},
                     req=request)
    matched = len(names) - len(unknown)
    return {"ok": True, "nameRow": nameRow, "dataRow": dataRow, "mappedColumns": matched, "unmatched": unknown[:40],
            "headerRowGuess": mx.best_header_row(ws, valid), "archived": os.path.basename(archived) if archived else None,
            "message": f'Template saved for "{rname}": {matched} of {len(names)} heading(s) on row {nameRow} match report columns'
                       + (", rows appended after the end of the sheet." if not dataRow else f", data written from row {dataRow} down.")}


if _UPLOADS:
    router.post("/template/{slug}")(template_upload)


@router.post("/template-export")
def template_export(body: dict, request: Request, ctx: Ctx = Depends(require(EXPORT))):
    req = _run_req(body)
    slug = _slug(req.slug or "")
    folder = _tpl_dir()
    tpl = os.path.join(folder, f"{slug}.xlsx")
    if not os.path.isfile(tpl):
        raise HTTPException(404, "No template uploaded for this report yet.")
    CURRENT_USER.set(ctx.emp_code)
    conn, cur, hdr, meta = mx.report_cursor(req)
    try:
        buf, n = mx.template_fill(tpl, mx.template_rows(folder, slug), hdr, cur)
    except ValueError as e:
        raise HTTPException(422, str(e))
    finally:
        conn.close()
    audit.action_now(ctx, "mis.report.export", "mis_report", slug, {"format": "template", "rows": n}, req=request)
    return _xlsx(buf, f"{slug}_{req.from_date}_to_{req.to_date}.xlsx")


# ── saved views (column layout, filters, sort) per user and page - DFS kept these in SQL Server ─────────
class ViewIn(BaseModel):
    name: str
    state: dict
    is_default: bool = False


@router.get("/views/{page}")
def views_list(page: str, ctx: Ctx = Depends(require(VIEW))):
    with db.tx() as cur:
        cur.execute("select id, name, state, is_default, updated_at from ops.mis_view where user_id = %s and page = %s"
                    " order by name", (ctx.user_id, page[:80]))
        return {"data": cur.fetchall()}


@router.post("/views/{page}")
def views_save(page: str, body: ViewIn, ctx: Ctx = Depends(require(VIEW))):
    from psycopg.types.json import Jsonb
    name = body.name.strip()[:80]
    if not name:
        raise HTTPException(422, "Give the view a name.")
    with db.tx() as cur:
        if body.is_default:
            cur.execute("update ops.mis_view set is_default = false where user_id = %s and page = %s", (ctx.user_id, page[:80]))
        cur.execute("insert into ops.mis_view (user_id, page, name, state, is_default) values (%s,%s,%s,%s,%s)"
                    " on conflict (user_id, page, name) do update set state = excluded.state, is_default = excluded.is_default,"
                    " updated_at = now() returning id", (ctx.user_id, page[:80], name, Jsonb(body.state), body.is_default))
        return {"id": cur.fetchone()["id"]}


@router.delete("/views/{page}/{vid}")
def views_delete(page: str, vid: int, ctx: Ctx = Depends(require(VIEW))):
    with db.tx() as cur:
        cur.execute("delete from ops.mis_view where id = %s and user_id = %s", (vid, ctx.user_id))
    return {"ok": True}


@router.get("/me")
def me(ctx: Ctx = Depends(require(VIEW))):
    """What the MIS pages may show for this user."""
    return {"emp_code": ctx.emp_code, "name": ctx.display_name,
            "can": {"export": ctx.can(EXPORT), "manage": ctx.can(MANAGE), "admin": ctx.can(ADMIN),
                    "dumps": ctx.can("dumps.view")}}
