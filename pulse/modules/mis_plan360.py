"""Plan Live 360 (DFS /aks/plan-live-360): the four monthly plans - Sale, Space, Transfer In (store grain) and
Purchase (RDC grain) - read together. Port of DFS planning-service routes/akash/planLive360.js, same Snowflake
objects (db-scripts 355 / 357): the page reads the SNAPSHOT tables AKS_GOLD.PLAN_LIVE_360*_MAT, which "Refresh
snapshot" rebuilds from the VW_PLAN_LIVE_360* views (INSERT OVERWRITE, single-flight) and stamps in
AKS_OPS.PLAN_LIVE_360_STATE with the four sources' signature, so the page can say when the snapshot is stale.
Nothing here re-derives a number: unit scaling, the key spine and NULL-not-zero all live in the views."""
import io
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from .. import audit
from ..iam.context import Ctx
from ..mis_engine import export as mx
from ..mis_engine.routes import sf_var_combined as base
from ..web import require

router = APIRouter(prefix="/api/v1/mis/plan360", tags=["mis"])
VIEW, EXPORT, ADMIN = "mis.view", "mis.export", "mis.admin"
G, OPS = "V2RETAIL.AKS_GOLD", "V2RETAIL.AKS_OPS"
V_STORE, V_RDC, V_HEALTH, V_LINE = (f"{G}.VW_PLAN_LIVE_360", f"{G}.VW_PLAN_LIVE_360_RDC", f"{G}.VW_PLAN_LIVE_360_HEALTH",
                                    f"{G}.VW_PLAN_LIVE_360_LINEAGE")
T_STORE, T_RDC, T_HEALTH, T_LINE = (f"{G}.PLAN_LIVE_360_MAT", f"{G}.PLAN_LIVE_360_RDC_MAT", f"{G}.PLAN_LIVE_360_HEALTH_MAT",
                                    f"{G}.PLAN_LIVE_360_LINEAGE_MAT")
T_STATE = f"{OPS}.PLAN_LIVE_360_STATE"
SIGNATURE_SQL = f"""
  SELECT COALESCE((SELECT TO_VARCHAR(MAX(SAVED_AT)) || '#' || TO_VARCHAR(MAX(ID)) FROM {OPS}.MONTH_PLAN_SAVE_LOG), '-') || '|' ||
         COALESCE((SELECT TO_VARCHAR(MAX(SAVED_AT)) || '#' || TO_VARCHAR(MAX(ID)) FROM {OPS}.SP_SAVE_LOG), '-') || '|' ||
         COALESCE((SELECT TO_VARCHAR(MAX(CREATED_AT)) FROM {G}.MTF_IN_PLAN), '-') || '|' ||
         COALESCE((SELECT TO_VARCHAR(MAX(CREATED_AT)) FROM {G}.MTF_PP_PLAN), '-') AS SIG"""

# k, header, type (s string, q quantity, v value, n number, b coverage flag), group - DFS STORE_COLS / RDC_COLS
STORE_COLS = [("MONTH_LABEL", "Month", "s", "IDENTITY"), ("ST_CD", "Store", "s", "IDENTITY"), ("ST_NM", "Store Name", "s", "IDENTITY"),
              ("RDC_CD", "RDC", "s", "IDENTITY"), ("MAJ_CAT", "Category", "s", "IDENTITY"), ("SSN", "Season", "s", "IDENTITY"),
              ("SALE_W_Q", "Sale Qty (W/STR)", "q", "SALE"), ("SALE_W_V", "Sale Val (W/STR)", "v", "SALE"),
              ("SALE_WO_Q", "Sale Qty (WO STR)", "q", "SALE"), ("SALE_WO_V", "Sale Val (WO STR)", "v", "SALE"),
              ("SPACE_DISP_Q", "Initial Disp Qty", "q", "SPACE"), ("TRF_SALE_Q", "TRF Sale Qty", "q", "TRF"),
              ("TRF_COVER_SALE_Q", "Cover Sale Qty", "q", "TRF"), ("TRF_OP_STK", "Opening Stock", "q", "TRF"),
              ("TRF_DISP_CL_Q", "Bgt Disp Cl Qty", "q", "TRF"), ("TRF_IN_STK_Q", "Transfer In Qty", "q", "TRF"),
              ("TRF_CL_STK", "Closing Stock", "q", "TRF"), ("TRF_SHORT_Q", "Short Qty", "q", "TRF"), ("TRF_EXCESS_Q", "Excess Qty", "q", "TRF"),
              ("TRF_COVER_DAY", "Cover Days", "n", "TRF"), ("HAS_SALE", "Has Sale", "b", "COVERAGE"), ("HAS_SPACE", "Has Space", "b", "COVERAGE"),
              ("HAS_TRF", "Has TRF", "b", "COVERAGE")]
RDC_COLS = [("MONTH_LABEL", "Month", "s", "IDENTITY"), ("RDC_CD", "RDC", "s", "IDENTITY"), ("RDC_NM", "RDC Name", "s", "IDENTITY"),
            ("MAJ_CAT", "Category", "s", "IDENTITY"), ("SSN_LIST", "Season", "s", "IDENTITY"), ("STORES", "Stores", "n", "IDENTITY"),
            ("SALE_W_Q", "Sale Qty (W/STR)", "q", "SALE"), ("SALE_W_V", "Sale Val (W/STR)", "v", "SALE"), ("SPACE_DISP_Q", "Initial Disp Qty", "q", "SPACE"),
            ("TRF_IN_STK_Q", "Transfer In Qty", "q", "TRF"), ("TRF_DISP_CL_Q", "Bgt Disp Cl Qty", "q", "TRF"), ("TRF_CL_STK", "Closing Stock", "q", "TRF"),
            ("PP_BGT_PUR_Q", "Purchase Qty", "q", "PURCHASE"), ("PP_POS_PO_RAISED", "PO Raised (+)", "q", "PURCHASE"),
            ("PP_NEG_PO_RAISED", "PO Raised (-)", "q", "PURCHASE"), ("PP_DC_STK_Q", "DC Stock", "q", "PURCHASE"),
            ("PP_DC_CL_STK_Q", "DC Closing Stock", "q", "PURCHASE"), ("PP_TTL_TRF_OUT_Q", "PP Trf Out Qty", "q", "PURCHASE"),
            ("PP_DC_SHORT_Q", "DC Short", "q", "PURCHASE"), ("PP_DC_EXCESS_Q", "DC Excess", "q", "PURCHASE"),
            ("TRF_VS_PP_OUT_DIFF", "TRF vs PP Diff", "q", "CHECK"), ("HAS_STORE_PLANS", "Has Store Plans", "b", "CHECK"),
            ("HAS_PURCHASE", "Has Purchase", "b", "CHECK")]
GRAINS = {
    "store": {"view": T_STORE, "cols": STORE_COLS, "filterable": ["ST_CD", "ST_NM", "RDC_CD", "MAJ_CAT", "SSN"],
              "order": "ST_CD ASC, MAJ_CAT ASC", "sheet": "Plan 360 Store", "file": "PlanLive360_Store",
              "gaps": {"no_trf": "NOT HAS_TRF", "no_space": "NOT HAS_SPACE", "no_sale": "NOT HAS_SALE",
                       "sale_no_trf": "HAS_SALE AND NOT HAS_TRF", "complete": "HAS_SALE AND HAS_SPACE AND HAS_TRF"}},
    "rdc": {"view": T_RDC, "cols": RDC_COLS, "filterable": ["RDC_CD", "RDC_NM", "MAJ_CAT"], "order": "RDC_CD ASC, MAJ_CAT ASC",
            "sheet": "Plan 360 RDC", "file": "PlanLive360_RDC",
            "gaps": {"no_purchase": "NOT HAS_PURCHASE", "no_store": "NOT HAS_STORE_PLANS",
                     "pp_mismatch": "TRF_VS_PP_OUT_DIFF IS NOT NULL AND TRF_VS_PP_OUT_DIFF <> 0"}},
}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _label(mid):
    y, m = divmod(int(mid), 100)
    return "%s-%d" % (MONTHS[m - 1] if 1 <= m <= 12 else m, y)


def _grain(g):
    cfg = GRAINS.get((g or "store").lower())
    if not cfg:
        raise HTTPException(400, f"Unknown grain '{g}'. Expected 'store' or 'rdc'.")
    return cfg


class Q(BaseModel):
    grain: str = "store"
    month_id: int | None = None
    col_filters: dict | None = None      # {COL: [values]} on the filterable columns
    gap: str | None = None
    page: int = 1
    limit: int = 500
    sort_col: str | None = None
    sort_dir: str | None = "asc"


def _where(q: Q, cfg, skip=None):
    parts, binds = [], []
    if q.month_id is not None:
        parts.append("MONTH_ID = %s"); binds.append(int(q.month_id))
    for col, vals in (q.col_filters or {}).items():
        col = str(col).upper()
        if col in cfg["filterable"] and col != skip and isinstance(vals, list) and vals:
            parts.append("%s IN (%s)" % (col, ", ".join(["%s"] * len(vals[:2000])))); binds += [str(v) for v in vals[:2000]]
    if q.gap and q.gap.lower() in cfg["gaps"]:
        parts.append("(%s)" % cfg["gaps"][q.gap.lower()])
    return (" AND ".join(parts) or "1 = 1"), binds


def _order(q: Q, cfg):
    key = (q.sort_col or "").upper()
    if key not in [c[0] for c in cfg["cols"]]:
        return cfg["order"]
    return "%s %s NULLS LAST" % (key, "DESC" if (q.sort_dir or "") == "desc" else "ASC")


def _rows(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, [base._fmt_str(v) for v in r])) for r in cur.fetchall()]


@router.get("/months")
def months(ctx: Ctx = Depends(require(VIEW))):
    conn = base._conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT MONTH_ID, SOURCE, ROW_COUNT, NON_ZERO_ROWS, STORES, CATEGORIES, PROVENANCE_LABEL, LAST_WRITTEN "
                    f"FROM {T_HEALTH} ORDER BY MONTH_ID, SOURCE")
        health = _rows(cur)
        cur.execute(f"SELECT * FROM {T_LINE}")
        line = (_rows(cur) or [None])[0]
        cur.execute(f"SELECT * FROM {T_STATE} WHERE ID = 1")
        state = (_rows(cur) or [None])[0] or {}
        cur.execute(SIGNATURE_SQL)
        sig = cur.fetchone()[0]
    finally:
        conn.close()
    by = {}
    for r in health:
        mid = int(r["MONTH_ID"])
        m = by.setdefault(mid, {"month_id": mid, "label": _label(mid), "sources": {}})
        m["sources"][r["SOURCE"]] = {"rows": r["ROW_COUNT"], "non_zero": r["NON_ZERO_ROWS"], "stores": r["STORES"],
                                    "categories": r["CATEGORIES"], "provenance": r["PROVENANCE_LABEL"], "last_written": r["LAST_WRITTEN"]}
    built = state.get("STATUS") == "DONE"
    return {"months": [by[k] for k in sorted(by)], "lineage": line,
            "snapshot": {"status": state.get("STATUS") or "NEVER", "refreshed_at": state.get("REFRESHED_AT"),
                         "refreshed_by": state.get("REFRESHED_BY"), "duration_ms": state.get("DURATION_MS"),
                         "rows_store": state.get("ROWS_STORE"), "rows_rdc": state.get("ROWS_RDC"), "error": state.get("ERROR_MSG"),
                         "stale": (not built) or state.get("SIGNATURE") != sig},
            "columns": {g: [{"k": c[0], "h": c[1], "t": c[2], "grp": c[3]} for c in cfg["cols"]] for g, cfg in GRAINS.items()},
            "filterable": {g: cfg["filterable"] for g, cfg in GRAINS.items()}, "gaps": {g: list(cfg["gaps"]) for g, cfg in GRAINS.items()}}


_lock = threading.Lock()
_running = {"t": None}


@router.post("/refresh")
def refresh(request: Request, ctx: Ctx = Depends(require(ADMIN))):
    """Rebuild the four snapshots from the views (single-flight: a second press waits for the first)."""
    if not _lock.acquire(blocking=False):
        with _lock:
            return {"joined": True, **(_running["t"] or {})}
    try:
        t0 = time.time()
        conn = base._conn()
        try:
            cur = conn.cursor()
            cur.execute(SIGNATURE_SQL); sig = cur.fetchone()[0]
            cur.execute(f"UPDATE {T_STATE} SET STATUS = 'RUNNING', REFRESHED_BY = %s, ERROR_MSG = NULL WHERE ID = 1", ("V2Pulse:" + ctx.emp_code,))
            try:
                for t, v in ((T_STORE, V_STORE), (T_RDC, V_RDC), (T_HEALTH, V_HEALTH), (T_LINE, V_LINE)):
                    cur.execute(f"INSERT OVERWRITE INTO {t} SELECT * FROM {v}")
                cur.execute(f"SELECT (SELECT COUNT(*) FROM {T_STORE}), (SELECT COUNT(*) FROM {T_RDC})")
                ns, nr = cur.fetchone()
                ms = int((time.time() - t0) * 1000)
                cur.execute(f"UPDATE {T_STATE} SET SIGNATURE = %s, REFRESHED_AT = SYSDATE(), DURATION_MS = %s, ROWS_STORE = %s, "
                            f"ROWS_RDC = %s, STATUS = 'DONE', ERROR_MSG = NULL WHERE ID = 1", (sig, ms, int(ns), int(nr)))
            except Exception as e:
                cur.execute(f"UPDATE {T_STATE} SET STATUS = 'FAILED', ERROR_MSG = %s WHERE ID = 1", (str(e)[:2000],))
                raise HTTPException(500, f"Refresh failed: {e}")
        finally:
            conn.close()
        out = {"rows_store": int(ns), "rows_rdc": int(nr), "duration_ms": ms}
        _running["t"] = out
        audit.action_now(ctx, "mis.plan360.refresh", "mis", "plan360", out, req=request)
        return {"joined": False, **out}
    finally:
        _lock.release()


@router.post("/rows")
def rows(q: Q, ctx: Ctx = Depends(require(VIEW))):
    cfg = _grain(q.grain)
    if q.month_id is None:
        raise HTTPException(400, "month_id required")
    limit, page = min(max(1, q.limit or 500), 2000), max(1, q.page or 1)
    w, b = _where(q, cfg)
    sel = ", ".join(["MONTH_ID"] + [c[0] for c in cfg["cols"]])
    conn = base._conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {cfg['view']} WHERE {w}", b)
        total = cur.fetchone()[0]
        cur.execute(f"SELECT {sel} FROM {cfg['view']} WHERE {w} ORDER BY {_order(q, cfg)} LIMIT {limit} OFFSET {(page - 1) * limit}", b)
        cols = [d[0] for d in cur.description]
        return {"total": total, "page": page, "limit": limit, "columns": cols,
                "rows": [[base._fmt_str(v) for v in r] for r in cur.fetchall()]}
    finally:
        conn.close()


@router.post("/summary")
def summary(q: Q, ctx: Ctx = Depends(require(VIEW))):
    cfg = _grain(q.grain)
    if q.month_id is None:
        raise HTTPException(400, "month_id required")
    w, b = _where(q, cfg)
    sums = ", ".join("SUM(%s) AS %s" % (c[0], c[0]) for c in cfg["cols"] if c[2] in ("q", "v"))
    flags = ", ".join("COUNT_IF(%s) AS N_%s, COUNT_IF(NOT %s) AS N_NOT_%s" % (c[0], c[0], c[0], c[0]) for c in cfg["cols"] if c[2] == "b")
    conn = base._conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) AS ROWS_TOTAL, {sums}, {flags} FROM {cfg['view']} WHERE {w}", b)
        return (_rows(cur) or [{}])[0]
    finally:
        conn.close()


@router.post("/col-values")
def col_values(q: Q, col: str, ctx: Ctx = Depends(require(VIEW))):
    cfg = _grain(q.grain)
    col = col.upper()
    if col not in cfg["filterable"]:
        raise HTTPException(400, f"Column '{col}' is not filterable at this grain (allowed: {', '.join(cfg['filterable'])}).")
    w, b = _where(q, cfg, skip=col)
    conn = base._conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT DISTINCT {col} AS V FROM {cfg['view']} WHERE {w} AND {col} IS NOT NULL ORDER BY 1 LIMIT 5000", b)
        return {"col": col, "values": [base._fmt_str(r[0]) for r in cur.fetchall()]}
    finally:
        conn.close()


@router.post("/export")
def export(q: Q, request: Request, ctx: Ctx = Depends(require(EXPORT))):
    """The filtered grid as .xlsx (NULL stays an empty cell, coverage flags as Yes / No), as DFS exports it."""
    cfg = _grain(q.grain)
    if q.month_id is None:
        raise HTTPException(400, "month_id required")
    w, b = _where(q, cfg)
    conn = base._conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT %s FROM %s WHERE %s ORDER BY %s" % (", ".join(c[0] for c in cfg["cols"]), cfg["view"], w, _order(q, cfg)), b)
        buf = io.BytesIO()
        wb, fmts = mx.new_workbook(buf)
        ws = wb.add_worksheet(cfg["sheet"])
        num = wb.add_format({"num_format": "#,##0.00"})
        for j, c in enumerate(cfg["cols"]):
            ws.write_string(0, j, c[1], fmts["head"]); ws.set_column(j, j, 14 if c[2] != "s" else 18)
        ws.freeze_panes(1, 0)
        n = 0
        while True:
            batch = cur.fetchmany(5000)
            if not batch:
                break
            for r in batch:
                n += 1
                for j, (c, v) in enumerate(zip(cfg["cols"], r)):
                    if v is None:
                        continue
                    if c[2] == "b":
                        ws.write_string(n, j, "Yes" if v else "No")
                    elif c[2] in ("q", "v", "n"):
                        ws.write_number(n, j, float(v), num)
                    else:
                        ws.write_string(n, j, str(v))
        wb.close()
    finally:
        conn.close()
    label = _label(q.month_id)
    audit.action_now(ctx, "mis.plan360.export", "mis", "plan360", {"grain": q.grain, "month": label, "rows": n}, req=request)
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{cfg["file"]}_{label}.xlsx"'})
