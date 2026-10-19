"""
Snowflake MIS — BIN WISE (mounted at /sf-bin-report).

Reads BRONZE.ET_ZPBI_ZWM_BIN_MOC: one row per warehouse BIN per snapshot, 420,804 rows across the
two DCs (DH24, DW01) and 347,067 distinct bins.

A DIFFERENT GRAIN FROM EVERY OTHER MIS REPORT, which is why it is its own page rather than another
saved report. The report builder's model is article x store x sale WINDOW; this table has no
article and no windows at all - it is the physical state of a bin. Forcing it through the builder
would have meant inventing a window dimension it does not have.

THE DATE DEFAULT, AND WHY IT IS NOT today-1
-------------------------------------------
This was asked for as "pur_date today-1". Measured before building: the table holds exactly ONE
day - MIN(PUR_DATE) = MAX(PUR_DATE) = 2026-09-03, one distinct date, no history. A today-1 default
would therefore open an empty report every single time, and would look like a broken page rather
than an empty day.

So the default is the LATEST snapshot the table actually holds. That is what "today-1" was reaching
for - the most recent complete bin position - and it stays right whenever the extract runs, without
anyone maintaining an offset. Every loaded date is still offered in the picker, so if the table
starts keeping history nothing here has to change.
"""
import io
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from . import sf_var_combined as base
from .sf_gandola import _stream_csv

logger = logging.getLogger(__name__)
router = APIRouter()

TBL = "V2RETAIL.BRONZE.ET_ZPBI_ZWM_BIN_MOC"

# Where a bin IS, and what state it is in. Order is the display order.
#
# "ROW" IS QUOTED EVERYWHERE. It is a reserved word in Snowflake, so a bare ROW in a SELECT list or
# an ORDER BY is a syntax error rather than a column - the sort of thing that only shows up when
# somebody sorts by that column in production.
DIMS = ["WERKS", "NAME1", "LGORT", "FLR", "SEC", "ROW", "GND", "LGTYP", "LGPLA",
        "STATUS", "STATUS_FE", "STATUS_GF", "BIN_TYEP", "CRATE", "STK_SATU",
        "BIN_TYPE_HANG_STACK", "HU_TYPE1", "HU_TYPE2", "HU_TYPE3", "LAST_MOV", "AGING"]

# What is in it and what moved. Summed for the totals strip.
MEAS = ["CURR_STK", "OP_STK", "CL_STK", "PICK_PEN", "V05", "V06", "TOT_PEN",
        "TD_IN_Q", "TD_OUT_Q"]

# Filterable, in the order the page shows them. Only the low-cardinality ones: FLR has 10 values and
# makes a usable tick-list, LGPLA has 347,067 and would be a scrolling wall - that one is served by
# the search box instead.
FILTER_COLS = ["WERKS", "FLR", "LGTYP", "SEC", "GND", "AGING"]

# The page pages 200 at a time; this ceiling exists for the FORMATTED export, which pours one
# result into a template and so has to ask for the whole selection in one call. It has to stay at
# or above the Node side's BIN_TPL_MAX_ROWS - a lower ceiling here would clamp that request
# silently, and the formatted sheet would hold the first 5,000 bins of 420,804 with nothing on it
# saying so. Node refuses a selection bigger than its own cap rather than truncating.
MAX_LIMIT = 100_000
ALL_COLS = DIMS + MEAS


def _col(c):
    """A quoted, validated column reference. Anything not in the catalogue never reaches the SQL."""
    if c not in ALL_COLS:
        raise HTTPException(400, f"unknown column '{c}'")
    return f'"{c}"'


def _latest(cur):
    cur.execute(f"SELECT MAX(PUR_DATE)::DATE FROM {TBL}")
    r = cur.fetchone()
    return base._fmt_str(r[0]) if r and r[0] else None


@router.get("/params")
def bin_params():
    """The dates that exist, and the tick-list options - both read from the table, never assumed."""
    conn = base._conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT DISTINCT PUR_DATE::DATE D FROM {TBL} ORDER BY 1 DESC LIMIT 400")
        dates = [base._fmt_str(r[0]) for r in cur.fetchall()]

        # One pass for every option list. Six separate DISTINCTs over 420k rows is six scans to
        # populate six dropdowns.
        sel = ", ".join(f'ARRAY_AGG(DISTINCT NVL(NULLIF(TRIM({_col(c)}),\'\'),\'(blank)\')) "{c}"'
                        for c in FILTER_COLS)
        cur.execute(f"SELECT {sel} FROM {TBL}")
        names = [d[0] for d in cur.description]
        row = cur.fetchone() or []
        import json as _json
        opts = {}
        for n, v in zip(names, row):
            vals = [x for x in (_json.loads(v) if v else []) if x is not None]
            opts[n] = sorted(vals)
    finally:
        conn.close()
    return {"dates": dates, "latest": dates[0] if dates else None,
            "filterCols": FILTER_COLS, "options": opts,
            "dims": DIMS, "measures": MEAS}


class BinReq(BaseModel):
    date: Optional[str] = None          # absent = the latest snapshot
    col_filters: Optional[dict] = None  # {COL: [values]}
    search: Optional[str] = None        # bin / description contains
    page: int = 1
    limit: int = 200
    sort_col: Optional[str] = None
    sort_dir: Optional[str] = "asc"


def _where(cur, req):
    """The WHERE for one request, plus the date it resolved to."""
    day = (req.date or "").strip() or _latest(cur)
    if not day:
        raise HTTPException(404, "no bin snapshots have been loaded")
    base._check_date(day, "date")
    conds = [f"PUR_DATE::DATE = {base._q(day)}::DATE"]

    for c, vals in (req.col_filters or {}).items():
        cu = str(c).strip().upper()
        if cu not in FILTER_COLS:
            continue
        clean = [str(v) for v in (vals or []) if str(v).strip() != ""]
        if not clean:
            continue
        # '(blank)' is a real choice in the tick-list, so it has to mean IS NULL / '' rather than
        # matching the literal text nobody has.
        lits = [v for v in clean if v != "(blank)"]
        parts = []
        if lits:
            parts.append(f"TRIM({_col(cu)}) IN ({', '.join(base._q(v) for v in lits)})")
        if "(blank)" in clean:
            parts.append(f"NVL(TRIM({_col(cu)}),'') = ''")
        conds.append("(" + " OR ".join(parts) + ")")

    s = str(req.search or "").strip()
    if s:
        like = base._q("%" + s + "%")
        conds.append(f'(UPPER("LGPLA") LIKE UPPER({like}) OR UPPER("NAME1") LIKE UPPER({like}) '
                     f'OR UPPER("LAST_MOV") LIKE UPPER({like}))')
    return " WHERE " + " AND ".join(conds), day


@router.post("/result")
def bin_result(req: BinReq):
    page = max(1, int(req.page or 1))
    limit = max(1, min(int(req.limit or 200), MAX_LIMIT))
    conn = base._conn()
    try:
        cur = conn.cursor()
        where, day = _where(cur, req)
        proj = ", ".join(_col(c) for c in ALL_COLS)

        direction = "DESC" if str(req.sort_dir or "").lower() == "desc" else "ASC"
        sc = str(req.sort_col or "")
        order = (f" ORDER BY {_col(sc)} {direction} NULLS LAST"
                 if sc in ALL_COLS else ' ORDER BY "WERKS", "FLR", "LGPLA"')

        cur.execute(f"SELECT COUNT(*) FROM {TBL}{where}")
        total = int(cur.fetchone()[0] or 0)

        # The totals strip is over the WHOLE filtered set, not the page - a page total of 200 bins
        # out of 347,067 would be a number nobody wants.
        tsel = ", ".join(f'ROUND(SUM({_col(m)}),2) "{m}"' for m in MEAS)
        cur.execute(f"SELECT {tsel} FROM {TBL}{where}")
        tnames = [d[0] for d in cur.description]
        totals = {n: base._f(v) for n, v in zip(tnames, cur.fetchone() or [])}

        cur.execute(f"SELECT {proj} FROM {TBL}{where}{order} "
                    f"LIMIT {limit} OFFSET {(page - 1) * limit}")
        cols = [c[0] for c in cur.description]
        rows = [[base._fmt_str(v) for v in r] for r in cur.fetchall()]
    finally:
        conn.close()
    return {"date": day, "columns": cols, "rows": rows, "total": total,
            "page": page, "limit": limit, "totals": totals,
            "dims": DIMS, "measures": MEAS}


@router.post("/export")
def bin_export(req: BinReq):
    conn = base._conn()
    cur = conn.cursor()
    where, day = _where(cur, req)
    proj = ", ".join(_col(c) for c in ALL_COLS)
    cur.execute(f'SELECT {proj} FROM {TBL}{where} ORDER BY "WERKS", "FLR", "LGPLA"')
    hdr = [c[0] for c in cur.description]
    return _stream_csv(cur, hdr, f"BIN_WISE_{day}.csv")


class BinValuesReq(BinReq):
    columns: Optional[list] = None


@router.post("/values")
def bin_values(req: BinValuesReq):
    """The distinct COMBINATIONS of the split columns that actually OCCUR.

    A folder dump asks this before it writes anything, so it plans one file per combination that
    exists rather than crossing every value of every level - two levels crossed blindly would order
    hundreds of files that are nearly all empty.

    SPLIT LEVELS ARE THE FILTER COLUMNS, not every dimension. Two reasons, and they are the same
    reason: a slice is taken by re-running the report with that value as a col_filter, so a level
    has to be something _where() can filter on; and LGPLA has 347,067 values, so "split by bin"
    means a third of a million files.
    """
    cols = [str(c).strip().upper() for c in (req.columns or [])]
    for c in cols:
        if c not in FILTER_COLS:
            raise HTTPException(400, f"'{c}' cannot be split on - choose from {', '.join(FILTER_COLS)}")
    if not cols:
        return {"combos": []}

    conn = base._conn()
    try:
        cur = conn.cursor()
        where, day = _where(cur, req)
        # '(blank)' here and in the tick-list are the SAME token on purpose: the combination goes
        # straight back as a col_filter, where it means IS NULL / '' rather than that literal text.
        proj = ", ".join(f"NVL(NULLIF(TRIM({_col(c)}),''),'(blank)')" for c in cols)
        order = ", ".join(str(i + 1) for i in range(len(cols)))
        # COUNT(*) alongside the combination, in the SAME group-by. The caller needs the true size
        # of every slice BEFORE it starts building, so it can refuse a slice it cannot deliver
        # whole rather than quietly handing back the first N rows of it. Asking per slice would be
        # one query each; this is one query for all of them.
        cur.execute(f"SELECT {proj}, COUNT(*) FROM {TBL}{where} "
                    f"GROUP BY {order} ORDER BY {order}")
        rows = cur.fetchall()
        combos = [[base._fmt_str(v) for v in r[:-1]] for r in rows]
        counts = [int(r[-1] or 0) for r in rows]
    finally:
        conn.close()
    return {"date": day, "columns": cols, "combos": combos, "counts": counts,
            "total": sum(counts)}
