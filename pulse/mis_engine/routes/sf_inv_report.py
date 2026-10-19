"""
Snowflake MIS — INVENTORY REPORTS (mounted at /sf-inv-report).

Saved reports over the Vertical Inventory (Article) slice, BRONZE.MIS_VERT_INV_ART - the same
card list / builder / open flow as MIS -> Reports (sf_mis_report.py), because that is the UI the
users already work in. A report is a DEFINITION, never stored rows:

    group_by   the dimensions, in pick order (sf_vert_inv_art.DIMS plus 'PM:<column>' for any
               VW_PRODUCT_MASTER column) - the grid's column order
    measures   a subset of OP_Q / OP_V / IN_Q / OUT_Q / CL_Q / CL_V / NEG_Q / NEG_V
    filters    {column: [values]} over sf_vert_inv_art.FILTER_COLS, saved with the report
    units      'kl' (thousand pcs / Rs lakh) or 'raw' - how the page and the dump show numbers
    dim_filters  CONDITIONS on any dimension, grouped or not ({col, op, v1, v2, vals}, the MIS
               Reports panel's shape) - applied inside the scan
    col_filters  CONDITIONS on a measure's total (CL_Q > 0, NEG_Q < 0 ...) - applied after the sum
    grid       optional FATHER / CHILD columns (2026-09-26): group_by is the father, and each grid
               column adds a block of child rows (GRID_COL / GRID_VAL) under every father, with a
               TOTAL row and a share % per measure - see sf_vert_inv_art._grid_sql

Every run reads one AS-OF date of the stored slice (default: the latest ET_STOCK_DATA reading,
built on first use exactly as the Vertical Inventory (Article) page builds it), so a saved report
follows the data and never drifts from it. The Dump Scheduler's 'inv_report' kind calls /export
(and /values for a level split) at fire time.

Definitions live in BRONZE.MIS_INV_REPORT_DEF, the same columns as BRONZE.MIS_REPORT_DEF; created
on first use. Visibility is the MIS rule: shared reports plus the caller's own; only the creator
edits or deletes, and a delete is soft.
"""
import json
import logging
import re
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import sf_var_combined as base
from . import sf_vert_inv_art as art

logger = logging.getLogger(__name__)
router = APIRouter()

DEF_TBL = f"{art.DB}.BRONZE.MIS_INV_REPORT_DEF"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
_ddl = {"ok": False}
# No match on purpose: a filter whose saved and run-time picks do not overlap must return nothing.
_NO_MATCH = "\x00no-match"


def _q(v):
    return base._q(v)


def _ensure(cur):
    if _ddl["ok"]:
        return
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {DEF_TBL} (
        REPORT_ID VARCHAR(40) NOT NULL, SLUG VARCHAR(80) NOT NULL, NAME VARCHAR(200) NOT NULL,
        DESCRIPTION VARCHAR(1000), DEF_JSON VARIANT, IS_SHARED BOOLEAN DEFAULT FALSE,
        SHOW_IN_SIDEBAR BOOLEAN DEFAULT TRUE, CREATED_BY VARCHAR(100), CREATED_AT TIMESTAMP_NTZ,
        UPDATED_BY VARCHAR(100), UPDATED_AT TIMESTAMP_NTZ, IS_DELETED BOOLEAN DEFAULT FALSE)""")
    _ddl["ok"] = True


def _now():
    return "CONVERT_TIMEZONE('Asia/Kolkata', CURRENT_TIMESTAMP())::TIMESTAMP_NTZ"


def _slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")
    return s[:60] or "report"


def _who(u):
    # V2Pulse: the signed-in employee code (pulse.mis_engine.CURRENT_USER), not the browser's value
    from .. import who
    return who(u)


def _who_dfs(u):
    return str(u or "").strip() or "unknown"


def _clean_def(d, cur):
    """Keep only what this report kind understands, in a stable shape."""
    d = d or {}
    pm_ok = set(art._pm_columns(cur))
    gb = []
    for x in d.get("group_by") or []:
        c = art._norm_dim(x)
        ok = (c in art.DIMS or (c.startswith(art.PM_PREFIX) and c[len(art.PM_PREFIX):] in pm_ok)
              or (c.startswith(art.SM_PREFIX) and c[len(art.SM_PREFIX):] in art.SM_COLS))
        if ok and c not in gb:
            gb.append(c)
    if not gb:
        raise HTTPException(400, "Pick at least one Group by column")
    want = {str(m).upper() for m in (d.get("measures") or [])}
    ms = [m for m in art.MEASURES if m in want]
    if not ms:
        raise HTTPException(400, "Pick at least one measure")
    flt = {}
    for c, vals in (d.get("filters") or {}).items():
        cu = str(c).strip().upper()
        clean = [str(v) for v in (vals or []) if str(v).strip() != ""]
        if cu in art.FILTER_COLS and clean:
            flt[cu] = clean
    units = "raw" if str(d.get("units") or "").lower() == "raw" else "kl"

    def conds(rows, ok):
        out = []
        for c in rows or []:
            col = art._norm_dim((c or {}).get("col") or "")
            if not ok(col):
                raise HTTPException(400, f"A condition names '{col}', which this report cannot filter on")
            row = {"col": col, "op": str(c.get("op") or "").lower()}
            for k in ("v1", "v2", "vals"):
                if c.get(k) not in (None, ""):
                    row[k] = c[k]
            out.append(row)
        return out
    dim_ok = lambda c: (c in art.DIMS or (c.startswith(art.PM_PREFIX) and c[len(art.PM_PREFIX):] in pm_ok)
                        or (c.startswith(art.SM_PREFIX) and c[len(art.SM_PREFIX):] in art.SM_COLS))
    grid = []
    for x in d.get("grid") or []:
        c = art._norm_dim(x)
        if dim_ok(c) and c not in gb and c not in grid:
            grid.append(c)
    if len(grid) > art.GRID_MAX:
        raise HTTPException(400, f"At most {art.GRID_MAX} grid columns")
    out = {"group_by": gb, "measures": ms, "filters": flt, "units": units,
           "dim_filters": conds(d.get("dim_filters"), lambda c: dim_ok(c) or (grid and c in art.GRID_OUT)),
           "col_filters": conds(d.get("col_filters"), lambda c: c in art.MEASURES)}
    if grid:
        out["grid"] = grid
    return out


def _row(r):
    return {"reportId": r[0], "slug": r[1], "name": r[2], "description": r[3],
            "definition": json.loads(r[4]) if r[4] else {}, "isShared": bool(r[5]),
            "showInSidebar": r[6] is not False, "createdBy": r[7], "createdAt": base._fmt_str(r[8]),
            "updatedAt": base._fmt_str(r[9]), "view": _view_name(r[1])}


_COLS = "REPORT_ID, SLUG, NAME, DESCRIPTION, DEF_JSON, IS_SHARED, SHOW_IN_SIDEBAR, CREATED_BY, CREATED_AT, UPDATED_AT"

# -- SNOWFLAKE VIEWS ---------------------------------------------------------------------------
# Every saved report is also published as V2RETAIL.SILVER.VW_INV_<SLUG> (the MIS reports' VW_MIS_
# pattern; its own prefix so the MIS lineage clean-up, which owns VW_MIS_*, never touches these).
# The view is the report exactly - Group by, measures, saved filters and conditions - on the LATEST
# built as-of date of BRONZE.MIS_VERT_INV_ART, resolved at read time, with an AS_OF_DATE column, so
# it follows the 08:00 rebuild by itself. Store / product-master columns get their plain names.
VIEW_SCHEMA = f"{art.DB}.SILVER"
_SENTINEL = date(1900, 1, 1)


def _view_name(slug):
    ident = re.sub(r"[^A-Za-z0-9_]", "_", str(slug or "").strip().lower()).upper()[:100]
    return f"{VIEW_SCHEMA}.VW_INV_{ident}"


def _plain(c):
    return c.split(":", 1)[1] if (c.startswith(art.PM_PREFIX) or c.startswith(art.SM_PREFIX)) else c


def _view_sql(defn, cur):
    vr = art.ViReq(dims=defn["group_by"], measures=defn["measures"], col_filters=defn.get("filters") or {},
                   dim_conds=defn.get("dim_filters") or [], meas_conds=defn.get("col_filters") or [],
                   grid=defn.get("grid") or None)
    ms = art._ms(vr)
    latest = f"(SELECT MAX(AS_OF_DATE) FROM {art.LOG_TBL})"

    def where_of(r):
        return art._where(r, _SENTINEL).replace(f"v.AS_OF_DATE = '{_SENTINEL.isoformat()}'",
                                                f"v.AS_OF_DATE = {latest}")
    if vr.grid:
        # The father / child rows (no TOTAL rows since 2026-09-28); GRID_SEQ (the grid column's pick order) lets
        # a reader of the view put them back in the report's order.
        sql, shown, _order = art._grid_sql(vr, cur, _SENTINEL, ms, where=where_of(art._split_grid_conds(vr)[0]))
        outer = ", ".join(f'q."{c}" AS "{_plain(c)}"' for c in shown)
        return f"SELECT {latest} AS AS_OF_DATE, {outer}, q._K AS GRID_SEQ FROM ({sql}) q"
    cols = art._out_cols(art._dims(vr, cur))
    sel, grp = art._select(cols, ms)
    where = where_of(vr)
    inner = f"{sel}{art._from(cols, art._cond_cols(vr))}{where} GROUP BY {grp}{art._having(vr)}"
    outer = ", ".join(f'q."{c}" AS "{_plain(c)}"' for c in cols + ms)
    return f"SELECT {latest} AS AS_OF_DATE, {outer} FROM ({inner}) q"


def _sync_view(cur, slug, defn, old_slug=None):
    """CREATE OR REPLACE the view; drop the old name after a rename (second, so a failed create leaves
    the old, still-correct view standing). Best effort: never fails the save."""
    name = _view_name(slug)
    try:
        cur.execute(f"CREATE OR REPLACE VIEW {name} AS {_view_sql(defn, cur)}")
        cur.execute(f"COMMENT ON VIEW {name} IS " + _q(
            f"Auto-published from BRONZE.MIS_INV_REPORT_DEF slug {slug}. The Inventory Report on the LATEST "
            f"built as-of date of BRONZE.MIS_VERT_INV_ART, resolved at read time. Edit the report in the app - "
            f"do not ALTER this view, it is replaced on every save."))
        if old_slug and str(old_slug).lower() != str(slug).lower():
            try:
                cur.execute(f"DROP VIEW IF EXISTS {_view_name(old_slug)}")
            except Exception as e:
                logger.warning("[inv-report] view rename left %s behind: %s", old_slug, e)
        return {"view": name, "ok": True}
    except Exception as e:
        logger.warning("[inv-report] could not publish view %s: %s", name, e)
        return {"view": name, "ok": False, "error": str(e)[:300]}


def _drop_view(cur, slug):
    try:
        cur.execute(f"DROP VIEW IF EXISTS {_view_name(slug)}")
    except Exception as e:
        logger.warning("[inv-report] could not drop view %s: %s", _view_name(slug), e)


@router.get("/catalogue")
def inv_catalogue():
    """What the builder can pick: the article page's dims + product-master columns, its measures and
    the filter vocabulary of the latest built date."""
    p = art.via_params(None)
    # Group-by in hierarchy groups, as MIS Reports shows its Fact / Store master / Product master.
    inv = p["dims"]
    pm = [p["pmPrefix"] + c for c in p["pmCols"]]
    pm_h = [p["pmPrefix"] + c for c in art.PM_HIERARCHY if c in p["pmCols"]]
    dims = {
        "Store hierarchy": [c for c in ("ST_CD", "ST_TYP") if c in inv] + [p["smPrefix"] + c for c in p["smCols"]],
        "Product hierarchy": [c for c in ("SEG", "DIV", "SUB_DIV", "MJ", "ART_TYPE", "GEN_ART", "CLR", "ART") if c in inv] + pm_h,
        "Location": [c for c in ("SLOC", "SRC") if c in inv],
        "Product master (other)": [c for c in pm if c not in pm_h],
    }
    return {"dims": dims,
            "measures": p["measures"], "labels": p["labels"], "filterCols": p["filterCols"],
            "options": p["options"], "pmPrefix": p["pmPrefix"], "asOf": p["asOf"],
            "latestEtDate": p["latestEtDate"]}


@router.post("/publish-all")
def inv_publish_all():
    """(Re)publish the view of every live report - for reports saved before views existed."""
    conn = base._conn()
    try:
        cur = conn.cursor()
        _ensure(cur)
        cur.execute(f"SELECT SLUG, DEF_JSON FROM {DEF_TBL} WHERE IS_DELETED = FALSE")
        out = []
        for slug, dj in cur.fetchall():
            out.append(_sync_view(conn.cursor(), slug, _clean_def(json.loads(dj) if dj else {}, cur)))
        return {"views": out}
    finally:
        conn.close()


@router.get("/reports")
def inv_list(user: Optional[str] = None):
    conn = base._conn()
    try:
        cur = conn.cursor()
        _ensure(cur)
        cur.execute(f"SELECT {_COLS} FROM {DEF_TBL} WHERE IS_DELETED = FALSE "
                    f"AND (IS_SHARED = TRUE OR CREATED_BY = {_q(_who(user))}) ORDER BY UPPER(NAME)")
        return {"reports": [_row(r) for r in cur.fetchall()]}
    finally:
        conn.close()


class SaveReq(BaseModel):
    report_id: Optional[str] = None
    name: str
    slug: Optional[str] = None
    description: Optional[str] = None
    definition: dict
    is_shared: bool = False
    show_in_sidebar: bool = True
    user: Optional[str] = None


@router.post("/reports")
def inv_save(req: SaveReq):
    name = str(req.name or "").strip()
    if not name:
        raise HTTPException(400, "Report name is required")
    slug = _slugify(req.slug or name)
    if not _SLUG_RE.match(slug):
        raise HTTPException(400, f"URL '{slug}' must be 2-61 of a-z, 0-9 and '-'")
    user = _who(req.user)
    conn = base._conn()
    try:
        cur = conn.cursor()
        _ensure(cur)
        defn = _clean_def(req.definition, cur)
        cur.execute(f"SELECT REPORT_ID FROM {DEF_TBL} WHERE IS_DELETED = FALSE AND SLUG = {_q(slug)}")
        clash = [r[0] for r in cur.fetchall() if r[0] != req.report_id]
        if clash:
            raise HTTPException(409, f"Another inventory report already uses the URL '{slug}'")
        dj = _q(json.dumps(defn))
        if req.report_id:
            cur.execute(f"SELECT CREATED_BY, SLUG FROM {DEF_TBL} WHERE REPORT_ID = {_q(req.report_id)} AND IS_DELETED = FALSE")
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, "Report not found")
            old_slug = r[1]
            if r[0] != user:
                raise HTTPException(403, f"Only {r[0]} can edit this report - Duplicate it to make your own")
            cur.execute(f"""UPDATE {DEF_TBL} SET SLUG = {_q(slug)}, NAME = {_q(name)},
                               DESCRIPTION = {_q(req.description or '')}, DEF_JSON = PARSE_JSON({dj}),
                               IS_SHARED = {bool(req.is_shared)}, SHOW_IN_SIDEBAR = {bool(req.show_in_sidebar)},
                               UPDATED_BY = {_q(user)}, UPDATED_AT = {_now()}
                             WHERE REPORT_ID = {_q(req.report_id)}""")
            rid = req.report_id
        else:
            old_slug = None
            rid = uuid.uuid4().hex
            cur.execute(f"""INSERT INTO {DEF_TBL} (REPORT_ID, SLUG, NAME, DESCRIPTION, DEF_JSON, IS_SHARED,
                               SHOW_IN_SIDEBAR, CREATED_BY, CREATED_AT, UPDATED_BY, UPDATED_AT, IS_DELETED)
                            SELECT {_q(rid)}, {_q(slug)}, {_q(name)}, {_q(req.description or '')}, PARSE_JSON({dj}),
                                   {bool(req.is_shared)}, {bool(req.show_in_sidebar)}, {_q(user)}, {_now()},
                                   {_q(user)}, {_now()}, FALSE""")
        view = _sync_view(cur, slug, defn, old_slug)
        cur.execute(f"SELECT {_COLS} FROM {DEF_TBL} WHERE REPORT_ID = {_q(rid)}")
        return {"report": _row(cur.fetchone()), "viewSync": view}
    finally:
        conn.close()


@router.delete("/reports/{report_id}")
def inv_delete(report_id: str, user: Optional[str] = None):
    conn = base._conn()
    try:
        cur = conn.cursor()
        _ensure(cur)
        cur.execute(f"SELECT CREATED_BY, SLUG FROM {DEF_TBL} WHERE REPORT_ID = {_q(report_id)} AND IS_DELETED = FALSE")
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Report not found")
        if r[0] != _who(user):
            raise HTTPException(403, f"Only {r[0]} can delete this report")
        cur.execute(f"UPDATE {DEF_TBL} SET IS_DELETED = TRUE, UPDATED_BY = {_q(_who(user))}, "
                    f"UPDATED_AT = {_now()} WHERE REPORT_ID = {_q(report_id)}")
        _drop_view(cur, r[1])
        return {"ok": True}
    finally:
        conn.close()


@router.get("/defs")
def inv_defs(slugs: str = ""):
    """Name + definition for a list of slugs, in the order asked, by URL rather than by owner - the
    multi-report workbook's lookup (tab names, which columns stay text, which a template may name).
    /reports filters by owner and a schedule has no user; /run would build a whole result just to
    read a definition. An unknown slug is simply absent - the caller reports it on its sheet."""
    want = [s.strip().lower() for s in str(slugs or "").split(",") if s.strip()][:100]
    if not want:
        return {"reports": []}
    conn = base._conn()
    try:
        cur = conn.cursor()
        _ensure(cur)
        cur.execute(f"SELECT {_COLS} FROM {DEF_TBL} WHERE IS_DELETED = FALSE "
                    f"AND SLUG IN ({', '.join(_q(s) for s in want)})")
        got = {r[1]: _row(r) for r in cur.fetchall()}
        return {"reports": [got[s] for s in want if s in got]}
    finally:
        conn.close()


def _load(slug):
    conn = base._conn()
    try:
        cur = conn.cursor()
        _ensure(cur)
        cur.execute(f"SELECT {_COLS} FROM {DEF_TBL} WHERE IS_DELETED = FALSE AND SLUG = {_q(str(slug or '').lower())}")
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, f"No inventory report '{slug}'")
        return _row(r)
    finally:
        conn.close()


class RunReq(BaseModel):
    slug: Optional[str] = None
    definition: Optional[dict] = None     # an unsaved definition (the builder's Preview)
    as_of: Optional[str] = None
    col_filters: Optional[dict] = None    # run-time picks, ANDed with the saved filters
    search: Optional[str] = None
    page: int = 1
    limit: int = 200
    sort_col: Optional[str] = None
    sort_dir: Optional[str] = "asc"
    columns: Optional[list] = None        # /values: the level columns to list combinations of
    units: Optional[str] = None           # /export: overrides the report's own units
    # Run-time header conditions (the grid's Excel-style column menus), ANDed with the saved ones.
    dim_conds: Optional[list] = None
    meas_conds: Optional[list] = None
    col: Optional[str] = None             # /col-values: the column whose value list is wanted


def _resolve_def(req):
    if req.definition is not None:
        conn = base._conn()
        try:
            return {"name": "Preview", "slug": "preview", "definition": _clean_def(req.definition, conn.cursor())}
        finally:
            conn.close()
    if not req.slug:
        raise HTTPException(400, "slug or definition is required")
    return _load(req.slug)


def _merge_filters(saved, run):
    """The saved filters AND the run-time ones: the same column in both keeps only the overlap."""
    out = {k: list(v) for k, v in (saved or {}).items()}
    for c, vals in (run or {}).items():
        cu = str(c).strip().upper()
        clean = [str(v) for v in (vals or []) if str(v).strip() != ""]
        if not clean:
            continue
        if cu in out:
            both = [v for v in out[cu] if v in clean]
            out[cu] = both or [_NO_MATCH]
        else:
            out[cu] = clean
    return out


def _vireq(rep, req, cls=art.ViReq, **extra):
    d = rep["definition"]
    return cls(as_of=req.as_of, dims=d["group_by"], measures=d["measures"],
               col_filters=_merge_filters(d.get("filters"), req.col_filters), search=req.search,
               dim_conds=(d.get("dim_filters") or []) + list(req.dim_conds or []),
               meas_conds=(d.get("col_filters") or []) + list(req.meas_conds or []),
               grid=d.get("grid") or None,
               page=req.page, limit=req.limit, sort_col=req.sort_col, sort_dir=req.sort_dir, **extra)


@router.post("/run")
def inv_run(req: RunReq):
    rep = _resolve_def(req)
    out = art.via_result(_vireq(rep, req))
    return {**out, "name": rep["name"], "slug": rep["slug"], "definition": rep["definition"]}


@router.post("/values")
def inv_values(req: RunReq):
    """The combinations of `columns` that occur under the report's filters on the as-of date - one
    dump file per combination. A level may be ANY of the report's own Group-by columns (as on MIS
    Reports): the dump cuts each file with that value - a filter where the column is one, else a
    per-file condition (store / product-master columns, GEN_ART, ARTICLE ...)."""
    rep = _resolve_def(req)
    lv = [art._norm_dim(c) for c in (req.columns or [])]
    bad = [c for c in lv if c not in rep["definition"]["group_by"]]
    if not lv or bad:
        raise HTTPException(400, f"Levels must be the report's own Group by columns; not {', '.join(bad) or '(none)'}")
    conn = base._conn()
    try:
        d, _info = art._resolve(conn, req.as_of)
        vr = _vireq(rep, req)
        sel = ", ".join(f"NVL(TRIM(TO_VARCHAR({art._x(c, lv)})),'') \"{c}\"" for c in lv)
        cur = conn.cursor()
        cur.execute(f"SELECT DISTINCT {sel}{art._from(lv, art._cond_cols(vr))}{art._where(vr, d)} ORDER BY {', '.join(str(i + 1) for i in range(len(lv)))}")
        return {"asOf": d.isoformat(), "columns": lv,
                "combos": [[(v if v != "" else "(blank)") for v in r] for r in cur.fetchall()]}
    finally:
        conn.close()


@router.post("/col-values")
def inv_col_values(req: RunReq):
    """A header menu's tick-list inside an opened report: the report's saved filters and
    conditions apply, the run-time header conditions do not."""
    rep = _resolve_def(req)
    return art._col_values(_vireq(rep, req.model_copy(update={"dim_conds": None, "meas_conds": None}),
                                  cls=art.ValuesReq, col=req.col or ""))


@router.post("/as-of")
def inv_as_of(req: RunReq):
    """The as-of date a run would read, and proof the report exists - without running it. The dump
    pinned its date with a /run of one row, and on a grid report that one row cost the whole
    estate's father / child query twice (~80 s each for ALL-GRID)."""
    rep = _resolve_def(req)
    conn = base._conn()
    try:
        d, _info = art._resolve(conn, req.as_of)
    finally:
        conn.close()
    return {"asOf": d.isoformat(), "name": rep["name"], "slug": rep["slug"],
            "units": rep["definition"].get("units") or "kl"}


@router.post("/split")
def inv_split(req: RunReq, mode: str = "rows"):
    """The report ONCE for every combination of `columns` (the dump's levels), streamed as NDJSON
    ordered by them - the dump writes one file per combination off this single read instead of one
    Snowflake run per store. mode 'rows' = the cells /run gives (for a template), 'csv' = the lines
    /export gives. See sf_vert_inv_art.via_split."""
    rep = _resolve_def(req)
    units = req.units or rep["definition"].get("units") or "kl"
    return art.via_split(_vireq(rep, req, cls=art.ExportReq, units=units, file_stem=rep["slug"]),
                         req.columns, "csv" if mode == "csv" else "rows")


@router.post("/export")
def inv_export(req: RunReq):
    rep = _resolve_def(req)
    units = req.units or rep["definition"].get("units") or "kl"
    return art.via_export(_vireq(rep, req, cls=art.ExportReq, units=units, file_stem=rep["slug"]))
