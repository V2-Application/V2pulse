"""
Snowflake MIS — DAY WISE TREND REPORTS (mounted at /sf-day-trend-report).

Saved reports over the Day Wise Sale & Stock Trend (sf_day_trend.py) - the MIS -> Reports /
Inventory Reports card list, builder and open flow, because that is the UI the users already work
in. A report is a DEFINITION, never stored rows:

    group_by     the grain, in pick order (sf_day_trend.ALL_DIMS)
    stock_tags   the stock tags, two columns (Q, V) each inside every day's block
    measures     the day's sale measures (SALE_Q / SALE_V / GM_V / DISCOUNT_V)
    filters      {DIM: [values]} - ANDed across dimensions, ORed within one
    v2r_plants   which V2R plants count toward the V2R_* columns ([] = every plant)
    store_only   ET stock tags count STORE plants only (columns only - no row is dropped)
    stock_total  'add' | 'only' | 'off' - the combined stock column when several tags are picked
    days         the window: the last N loaded days (1..31), resolved on every open / dump

THE WINDOW IS A LENGTH, NOT TWO DATES. A stored From / To would freeze a saved report on the day
it was made; "the last 30 days" follows the latest loaded stock day, exactly as the Day Wise Trend
dump already does. The opened page still lets the reader move the dates.

NO SNOWFLAKE VIEW per report (unlike VW_MIS_* / VW_INV_*): the columns ARE the days, so a view's
column list would change every morning and break whatever reads it.

Every run goes through sf_day_trend's own /result, /values and /export with the definition spread
into the request - one engine, so a saved report can never disagree with the page it opens.

Definitions live in BRONZE.MIS_DAY_TREND_REPORT_DEF, the same columns as BRONZE.MIS_REPORT_DEF;
created on first use. Visibility is the MIS rule: shared reports plus the caller's own; only the
creator edits or deletes, and a delete is soft.
"""
import json
import re
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import sf_var_combined as base
from . import sf_vert_inv_art as art
from . import sf_day_trend as trend

router = APIRouter()

DEF_TBL = f"{art.DB}.BRONZE.MIS_DAY_TREND_REPORT_DEF"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
_ddl = {"ok": False}


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


def _all_tags():
    return set(base.STOCK_TAGS) | set(base.COMPOSITE_TAGS) | set(base.RULE_TAGS) | set(base.PICK_TAGS)


def _clean_def(d):
    """Keep only what the Day Wise Trend understands, in a stable shape."""
    d = d or {}
    gb = []
    for x in d.get("group_by") or []:
        c = str(x).strip().upper()
        if c in trend.ALL_DIMS and c not in gb:
            gb.append(c)
    if not gb:
        raise HTTPException(400, "Pick at least one Group by column")
    ok_tags = _all_tags()
    tags = []
    for x in d.get("stock_tags") or []:
        t = str(x).strip()
        if t in ok_tags and t not in tags:
            tags.append(t)
    want = {str(m).upper() for m in (d.get("measures") or [])}
    meas = [m for m in trend.SALE_MEASURES if m in want]
    # Both are required: the Day Wise Trend engine reads an empty pick as its 0001 + 0006 / sale
    # default, so a report saved "without" either would silently show the default instead.
    if not tags:
        raise HTTPException(400, "Pick at least one stock tag")
    if not meas:
        raise HTTPException(400, "Pick at least one sale measure")
    flt = {}
    for c, vals in (d.get("filters") or {}).items():
        cu = str(c).strip().upper()
        clean = [str(v) for v in (vals or []) if str(v).strip() != ""]
        if cu in trend.ALL_DIMS and clean:
            flt[cu] = clean
    mode = str(d.get("stock_total") or "add").lower()
    if mode not in trend.STOCK_TOTAL_MODES:
        mode = "add"
    try:
        days = int(d.get("days") or trend.DEFAULT_DAYS)
    except (TypeError, ValueError):
        days = trend.DEFAULT_DAYS
    days = max(1, min(days, trend.MAX_DAYS))
    # CONDITIONS, the MIS Reports panel's shape: dim_filters on any dimension (WHERE, inside the
    # scan), col_filters on a measure id from sf_day_trend.meas_cond_ids (sale = window total,
    # stock = the last day).
    meas_ok = {i for i, _l in trend.meas_cond_ids(tags, meas)}

    def conds(rows, ok, what):
        out = []
        for c in rows or []:
            col = str((c or {}).get("col") or "").strip().upper()
            if not ok(col):
                raise HTTPException(400, f"A condition names '{col}', which is not {what}")
            row = {"col": col, "op": str(c.get("op") or "").lower()}
            for k in ("v1", "v2", "vals"):
                if c.get(k) not in (None, ""):
                    row[k] = c[k]
            out.append(row)
        return out
    return {"group_by": gb, "stock_tags": tags, "measures": meas, "filters": flt,
            "dim_filters": conds(d.get("dim_filters"), lambda c: c in trend.ALL_DIMS, "a dimension"),
            "col_filters": conds(d.get("col_filters"), lambda c: c in meas_ok,
                                 "one of this report's measures or stock tags"),
            "v2r_plants": [str(p).strip() for p in (d.get("v2r_plants") or []) if str(p).strip()],
            "store_only": bool(d.get("store_only")), "stock_total": mode, "days": days}


def _row(r):
    return {"reportId": r[0], "slug": r[1], "name": r[2], "description": r[3],
            "definition": json.loads(r[4]) if r[4] else {}, "isShared": bool(r[5]),
            "showInSidebar": r[6] is not False, "createdBy": r[7], "createdAt": base._fmt_str(r[8]),
            "updatedAt": base._fmt_str(r[9])}


_COLS = "REPORT_ID, SLUG, NAME, DESCRIPTION, DEF_JSON, IS_SHARED, SHOW_IN_SIDEBAR, CREATED_BY, CREATED_AT, UPDATED_AT"


@router.get("/reports")
def dt_list(user: Optional[str] = None):
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
def dt_save(req: SaveReq):
    name = str(req.name or "").strip()
    if not name:
        raise HTTPException(400, "Report name is required")
    slug = _slugify(req.slug or name)
    if not _SLUG_RE.match(slug):
        raise HTTPException(400, f"URL '{slug}' must be 2-61 of a-z, 0-9 and '-'")
    user = _who(req.user)
    defn = _clean_def(req.definition)
    conn = base._conn()
    try:
        cur = conn.cursor()
        _ensure(cur)
        cur.execute(f"SELECT REPORT_ID FROM {DEF_TBL} WHERE IS_DELETED = FALSE AND SLUG = {_q(slug)}")
        if [r[0] for r in cur.fetchall() if r[0] != req.report_id]:
            raise HTTPException(409, f"Another day wise trend report already uses the URL '{slug}'")
        dj = _q(json.dumps(defn))
        if req.report_id:
            cur.execute(f"SELECT CREATED_BY FROM {DEF_TBL} WHERE REPORT_ID = {_q(req.report_id)} AND IS_DELETED = FALSE")
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, "Report not found")
            if r[0] != user:
                raise HTTPException(403, f"Only {r[0]} can edit this report - Duplicate it to make your own")
            cur.execute(f"""UPDATE {DEF_TBL} SET SLUG = {_q(slug)}, NAME = {_q(name)},
                               DESCRIPTION = {_q(req.description or '')}, DEF_JSON = PARSE_JSON({dj}),
                               IS_SHARED = {bool(req.is_shared)}, SHOW_IN_SIDEBAR = {bool(req.show_in_sidebar)},
                               UPDATED_BY = {_q(user)}, UPDATED_AT = {_now()}
                             WHERE REPORT_ID = {_q(req.report_id)}""")
            rid = req.report_id
        else:
            rid = uuid.uuid4().hex
            cur.execute(f"""INSERT INTO {DEF_TBL} (REPORT_ID, SLUG, NAME, DESCRIPTION, DEF_JSON, IS_SHARED,
                               SHOW_IN_SIDEBAR, CREATED_BY, CREATED_AT, UPDATED_BY, UPDATED_AT, IS_DELETED)
                            SELECT {_q(rid)}, {_q(slug)}, {_q(name)}, {_q(req.description or '')}, PARSE_JSON({dj}),
                                   {bool(req.is_shared)}, {bool(req.show_in_sidebar)}, {_q(user)}, {_now()},
                                   {_q(user)}, {_now()}, FALSE""")
        cur.execute(f"SELECT {_COLS} FROM {DEF_TBL} WHERE REPORT_ID = {_q(rid)}")
        return {"report": _row(cur.fetchone())}
    finally:
        conn.close()


@router.delete("/reports/{report_id}")
def dt_delete(report_id: str, user: Optional[str] = None):
    conn = base._conn()
    try:
        cur = conn.cursor()
        _ensure(cur)
        cur.execute(f"SELECT CREATED_BY FROM {DEF_TBL} WHERE REPORT_ID = {_q(report_id)} AND IS_DELETED = FALSE")
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Report not found")
        if r[0] != _who(user):
            raise HTTPException(403, f"Only {r[0]} can delete this report")
        cur.execute(f"UPDATE {DEF_TBL} SET IS_DELETED = TRUE, UPDATED_BY = {_q(_who(user))}, "
                    f"UPDATED_AT = {_now()} WHERE REPORT_ID = {_q(report_id)}")
        return {"ok": True}
    finally:
        conn.close()


@router.get("/defs")
def dt_defs(slugs: str = ""):
    """Name + definition for a list of slugs, in the order asked, by URL rather than by owner - the
    Dump Scheduler's lookup at fire time (a schedule has no user). An unknown slug is absent."""
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
