"""MIS Sale Trend / Stock Trend — the same day-wise facts, read along the TIME axis.

WHAT THIS IS, AND WHY IT IS NOT A REPORT DEFINITION
---------------------------------------------------
sf_mis_report answers "what does this grouping look like AS AT a date" — nine named windows
anchored on the To date, one row per group. This answers the other question: "how did it MOVE
across the range" — the range chopped into buckets, one value per bucket.

They cannot be the same query. A window is a fixed lookback from one anchor and the windows
overlap (MTD is inside YTD); a trend bucket is a partition of the range and the buckets do not
overlap, which is the only shape that can be summed back to the range total or drawn as a line.

THE TWO LAYOUTS ARE THE SAME NUMBERS
------------------------------------
    long ("unpivot")   one row per (dimensions, PERIOD), a column per measure.
    pivot              one row per (dimensions), a column per (PERIOD x measure).

Both are built from ONE inner aggregate, `per`, so a cell cannot read differently depending on
which way round it is displayed. The pivot is a CASE fan-out over that same CTE — done in
Snowflake, never in the browser: a client-side pivot has to pull every long row first, which is
the row count multiplied by the period count, and that is what put the D-Weekly custom pivot at
~90s before it moved server-side.

THE PERIOD LIST IS GENERATED, NOT DISCOVERED
--------------------------------------------
Bucket labels come from the REQUESTED RANGE in Python, not from a DISTINCT over the fact. Two
reasons, and the second is the one that matters:

  * it costs no round trip, and
  * a bucket with NO DATA still gets a column. A week the stores did not sell is a fact about the
    business; a week that silently vanishes from the middle of a trend is a fact about the query,
    and the reader cannot tell them apart.

The week bucket is ISO (DATEADD over DAYOFWEEKISO), not DATE_TRUNC('WEEK', ...): DATE_TRUNC reads
the session's WEEK_START parameter, so the same range would bucket differently on a session that
had it set. Python's weekday() and DAYOFWEEKISO agree by construction.

SALE SUMS. STOCK DOES NOT.
--------------------------
Sale, GM and discount are FLOWS: a bucket total is the sum of its days, and the buckets add back
up to the range. Stock is a POSITION, and adding two days of it gives stock-days, which is the
exact fault the report's own stock block exists to avoid. So stock offers two honest readings and
no third:

  close   the position on the LAST DAY EACH STORE REPORTED inside the bucket, per storage tag.
          Per (ST_CD, KPI), because ET_STOCK_DATA and the ZWM extracts have different day
          coverage — one store-wide MAX(DT) would let whichever source reported latest decide the
          day for both, and every tag from the other source would read empty.
  avg     the daily group total, averaged over the days that REPORTED. A day with no row is
          absent, not zero: the day-wise loader carries no forward fill, so "not reported" and
          "nothing in stock" are different facts and only one of them is in the table.

There is no TOTAL column on a close-basis stock trend. Summing closing positions across buckets
is meaningless and an "Avg" of them is not what anyone would read it as, so the column is simply
not emitted rather than emitted with a footnote.
"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from .sf_gandola import _stream_csv
from . import sf_var_combined as base
from . import sf_mis_report as rep

logger = logging.getLogger(__name__)
router = APIRouter()

# The trend's own dimension list: the report's, minus DT. The bucket IS the time axis, so grouping
# by the raw day as well would emit one row per day inside each bucket and label them all with the
# bucket — the same value repeated, and a "total" that multiplies by the days in the bucket.
DIM_COLS = [c for c in rep.DIM_COLS if c != "DT"]
# Stock only. Physically on the stock fact and meaningless on the sale one, so it is added per kind
# rather than to the shared list. It is what lets a stock trend be broken out BY storage location
# instead of only filtered to a set of them.
STOCK_DIM = "KPI"

def _allowed(kind):
    """The dimensions one kind may group or filter on. KPI is on the stock fact only."""
    return DIM_COLS + ([STOCK_DIM] if str(kind or "").strip().lower() == "stock" else [])


SALE_MEASURES = ["SALE_V", "SALE_Q", "GM_V", "DISCOUNT_V"]
STOCK_MEASURES = ["STOCK_V", "STOCK_Q"]

BUCKETS = ["day", "week", "month", "quarter", "year"]
STOCK_BASES = ["close", "avg"]

# A trend is a shape you read, and past a few hundred columns there is no shape left — only a
# spreadsheet nobody scrolls to the end of. Both caps name the two ways out (coarser bucket, fewer
# measures) rather than just refusing.
MAX_PERIODS = 400
MAX_PIVOT_COLS = 1500
MAX_LIMIT = 5000
# Enough for every store, every maj-cat, every colour. A dimension with more distinct values than
# this is one nobody is picking from a tick-list anyway.
MAX_VALUES = 5000


# ══════════════════════════════════════════════════════════════════════════════
# buckets
# ══════════════════════════════════════════════════════════════════════════════
def _bucket(b):
    b = str(b or "day").strip().lower()
    if b not in BUCKETS:
        raise HTTPException(400, f"bucket must be one of {BUCKETS}")
    return b


def _period_expr(col, bucket):
    """The bucket label, as SQL over a date column.

    Every label sorts LEXICOGRAPHICALLY into chronological order — YYYY-MM-DD, YYYY-MM, YYYY-Qn,
    YYYY — so the period needs no separate sort key travelling beside it, and an ORDER BY on the
    displayed column is already the right order.
    """
    if bucket == "day":
        return f"TO_CHAR({col}, 'YYYY-MM-DD')"
    if bucket == "week":
        # ISO Monday. NOT DATE_TRUNC('WEEK', ...) — see the module docstring.
        return f"TO_CHAR(DATEADD(day, -(DAYOFWEEKISO({col}) - 1), {col}), 'YYYY-MM-DD')"
    if bucket == "month":
        return f"TO_CHAR({col}, 'YYYY-MM')"
    if bucket == "quarter":
        return f"TO_CHAR({col}, 'YYYY') || '-Q' || TO_CHAR(QUARTER({col}))"
    return f"TO_CHAR({col}, 'YYYY')"


def _period_of(d, bucket):
    """The same label, in Python. Kept next to _period_expr because the two MUST agree: the pivot
    asks SQL for `PERIOD = <label>` using labels this function generated."""
    if bucket == "day":
        return d.isoformat()
    if bucket == "week":
        return (d - timedelta(days=d.weekday())).isoformat()
    if bucket == "month":
        return f"{d.year:04d}-{d.month:02d}"
    if bucket == "quarter":
        return f"{d.year:04d}-Q{(d.month - 1) // 3 + 1}"
    return f"{d.year:04d}"


def _periods(a, b, bucket):
    """Every bucket the range touches, in order — including the empty ones.

    Walked a day at a time rather than stepped by bucket: a month step has to special-case the
    31st, a week step has to know where the week started, and a day walk over even a decade is
    3,650 cheap iterations. The dedupe is what turns it into buckets.
    """
    d0, d1 = datetime.fromisoformat(a).date(), datetime.fromisoformat(b).date()
    out, seen = [], set()
    d = d0
    while d <= d1:
        p = _period_of(d, bucket)
        if p not in seen:
            seen.add(p)
            out.append(p)
        d += timedelta(days=1)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# request validation
# ══════════════════════════════════════════════════════════════════════════════
class TrendReq(BaseModel):
    kind: str = "sale"                       # sale | stock
    from_date: str
    to_date: str
    bucket: str = "day"
    layout: str = "pivot"                    # pivot | long  ("unpivot")
    group_by: Optional[List[str]] = None
    measures: Optional[List[str]] = None
    stock_tags: Optional[List[str]] = None   # stock: which storage locations are IN SCOPE
    stock_basis: str = "close"               # stock: close | avg
    filters: Optional[dict] = None           # {COL: [values]} — inside the scan
    col_filters: Optional[List[dict]] = None  # Excel-style, on the finished columns
    dim_filters: Optional[List[dict]] = None  # the conditions panel — inside the scan, any dim
    page: int = 1
    limit: int = 200
    sort_col: Optional[str] = None
    sort_dir: Optional[str] = None
    column: Optional[str] = None             # /values only


def _clean(req):
    """The request, normalised to something that can be interpolated into SQL.

    Same rule as the report builder's _clean_def and for the same reason: every name is FILTERED
    against a catalogue rather than escaped, so a column that is not in the list never reaches the
    query and there is no injection surface further down to get right.
    """
    kind = str(req.kind or "sale").strip().lower()
    if kind not in ("sale", "stock"):
        raise HTTPException(400, "kind must be 'sale' or 'stock'")
    bucket = _bucket(req.bucket)
    layout = "long" if str(req.layout or "pivot").strip().lower() == "long" else "pivot"

    allowed = DIM_COLS + ([STOCK_DIM] if kind == "stock" else [])
    seen, dims = set(), []
    for c in (req.group_by or []):
        c = str(c).strip().upper()
        if c == "DT":
            raise HTTPException(400, "DATE is the trend's own axis — pick a bucket (day / week / "
                                     "month) instead of grouping by DT.")
        if c in allowed and c not in seen:
            seen.add(c)
            dims.append(c)

    cat = SALE_MEASURES if kind == "sale" else STOCK_MEASURES
    want = {str(m).strip().upper() for m in (req.measures or [])}
    # Catalogue order, never the request's, so two runs with the same measures come out with the
    # same column order and yesterday's export still lines up with today's.
    meas = [m for m in cat if m in want] or list(cat)

    tags = []
    if kind == "stock":
        tags = base._tags(req.stock_tags)          # validated + canonically ordered
    basis = str(req.stock_basis or "close").strip().lower()
    if basis not in STOCK_BASES:
        raise HTTPException(400, f"stock_basis must be one of {STOCK_BASES}")

    filters = {}
    for k, v in (req.filters or {}).items():
        k = str(k).strip().upper()
        if k in allowed and isinstance(v, list):
            vals = [str(x) for x in v if str(x).strip() != ""]
            if vals:
                filters[k] = vals[:500]

    # THE CONDITIONS PANEL. The same third shape the report builder has, for the same reason: a
    # col_filter can only name a column the run PRODUCES, so a condition could otherwise sit only
    # on a grouped dimension — and the usual thing anyone wants from a filter ("only the north
    # cluster") is exactly the one that should NOT add a column and split every row.
    #
    # Applied inside the scan by rep._dim_where, against the raw column. The COLUMN is validated
    # here, unlike col_filters: this string is interpolated into the inner scan, so anything
    # outside the catalogue is dropped rather than escaped — the same rule as `filters`.
    dim_filters = []
    for f in (req.dim_filters or []):
        if not isinstance(f, dict):
            continue
        c = str(f.get("col") or "").strip().upper()
        op = str(f.get("op") or "").strip().lower()
        if c not in allowed or op not in rep._SAVED_OPS:
            continue
        row = {"col": c, "op": op, "v1": f.get("v1"), "v2": f.get("v2")}
        if isinstance(f.get("vals"), list):
            row["vals"] = [str(x) for x in f["vals"]][:rep.MAX_IN_VALUES]
        dim_filters.append(row)
        if len(dim_filters) >= 50:      # 50 conditions is a mistake, not a filter
            break

    return {"kind": kind, "bucket": bucket, "layout": layout, "group_by": dims,
            "measures": meas, "stock_tags": tags, "stock_basis": basis, "filters": filters,
            "dim_filters": dim_filters}


# ══════════════════════════════════════════════════════════════════════════════
# SQL
# ══════════════════════════════════════════════════════════════════════════════
def _expr(c):
    """Where a dimension comes from. KPI is the stock fact's own storage tag; everything else is
    the report builder's mapping verbatim, so a dimension resolves identically on both pages."""
    if c == STOCK_DIM:
        return "f.KPI"
    return rep._dim_expr(c)


def _conds(defn):
    """A clause per filtered dimension, matched the way the report matches them.

    Case- and space-insensitive, because codes are uppercase in the fact and typed however the
    person typing them felt — an "is any of DB03" that returns nothing because it was typed
    "db03" reads exactly like a store with no sale.
    """
    out = []
    for k, vals in defn["filters"].items():
        lits = ", ".join(base._q(str(v).strip().upper()) for v in vals)
        out.append(f"UPPER(TRIM(TO_VARCHAR({_expr(k)}))) IN ({lits})")
    # Everything the conditions panel wrote — Equals, Contains, Between, Is none of, on a grouped
    # dimension or on one this trend never prints. Same scan, same WHERE, and rep._col_where does
    # the escaping, so there is one condition engine on both pages rather than two to keep in step.
    out += rep._dim_where(defn.get("dim_filters"), _expr, _allowed(defn.get("kind")))
    return out


def _joins(defn):
    """The masters this run needs. KPI lives on the fact, so it is dropped before asking the
    report builder — which would otherwise look it up in a map it is not in.

    The tags are FORWARDED, not dropped: rep._joins reads them to decide whether a rule tag needs
    the plant lookup, and handing it a definition without them is what made every run 500 with
    KeyError: 'stock_tags'. `.get` because /values builds a probe definition with dimensions only,
    and a sale-kind run has no tags at all."""
    used = [c for c in defn["group_by"] if c != STOCK_DIM]
    fl = {k: v for k, v in defn["filters"].items() if k != STOCK_DIM}
    # A condition on a store or product attribute needs that master joined even though the column
    # is never projected — rep._joins counts dim_filters as USED for exactly this.
    df = [f for f in (defn.get("dim_filters") or []) if f.get("col") != STOCK_DIM]
    return rep._joins({"group_by": used, "filters": fl, "dim_filters": df,
                       "stock_tags": defn.get("stock_tags") or []})


def _per_cte(defn, a, b):
    """`per` — one row per (dimensions, PERIOD), a column per measure.

    THE ONE aggregate. Both layouts read it, which is what makes the pivot and the long view the
    same numbers by construction rather than by anyone remembering to keep two queries in step.
    """
    dims = defn["group_by"]
    dim_sel = ", ".join(f"{_expr(c)} AS {c}" for c in dims)
    # GROUP BY the QUALIFIED expressions, not the output aliases: ST_CD exists on the fact AND on
    # the store master, so a bare "GROUP BY ST_CD" is ambiguous the moment a store attribute is
    # used — it compiles for product-only groupings and fails for store ones.
    dim_grp = ", ".join(_expr(c) for c in dims)
    lead = (dim_sel + ", ") if dims else ""
    grp = (dim_grp + ", ") if dims else ""
    joins, conds = _joins(defn), _conds(defn)

    if defn["kind"] == "sale":
        pe = _period_expr("f.DT", defn["bucket"])
        sel = ", ".join(f'ROUND(SUM(f.{m}), 4) AS "{m}"' for m in defn["measures"])
        where = " AND ".join([f"f.DT BETWEEN '{a}'::DATE AND '{b}'::DATE"] + conds)
        return (f"per AS (SELECT {lead}{pe} AS PERIOD, {sel} "
                f"FROM {base.SALE_TBL} f{joins} WHERE {where} "
                f"GROUP BY {grp}{pe})")

    tag_lits = ", ".join(base._q(t) for t in defn["stock_tags"])
    if defn["stock_basis"] == "close":
        # The pick is per (ST_CD, KPI) and NOT joined to the masters: it only has to name the last
        # reported day, and dragging the 3.7M-row product view through it would pay for the join
        # twice. The masters are applied on the fact side, where the dimensions are read.
        pe_pick = _period_expr("DT", defn["bucket"])
        pick = (f"(SELECT ST_CD, KPI, {pe_pick} AS P, MAX(DT) AS DT FROM {base.STOCK_TBL} "
                f"WHERE DT BETWEEN '{a}'::DATE AND '{b}'::DATE AND KPI IN ({tag_lits}) "
                f"GROUP BY 1, 2, 3)")
        sel = ", ".join(f'ROUND(SUM(f.{m}), 4) AS "{m}"' for m in defn["measures"])
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        return (f"per AS (SELECT {lead}p.P AS PERIOD, {sel} "
                f"FROM {base.STOCK_TBL} f{joins} "
                f"JOIN {pick} p ON p.ST_CD = f.ST_CD AND p.KPI = f.KPI AND p.DT = f.DT"
                f"{where} GROUP BY {grp}p.P)")

    # avg: total ACROSS the dimension first, average ACROSS the days second. In that order, or it
    # becomes an average of averages — the same rule the report's AVG_STK columns follow.
    pe = _period_expr("f.DT", defn["bucket"])
    day_sel = ", ".join(f"SUM(f.{m}) AS {m}" for m in defn["measures"])
    where = " AND ".join([f"f.DT BETWEEN '{a}'::DATE AND '{b}'::DATE",
                          f"f.KPI IN ({tag_lits})"] + conds)
    dim_csv = ", ".join(dims)
    lead2 = (dim_csv + ", ") if dims else ""
    out_sel = ", ".join(f'ROUND(AVG({m}), 4) AS "{m}"' for m in defn["measures"])
    return (f"per_day AS (SELECT {lead}{pe} AS PERIOD, f.DT, {day_sel} "
            f"FROM {base.STOCK_TBL} f{joins} WHERE {where} "
            f"GROUP BY {grp}{pe}, f.DT), "
            f"per AS (SELECT {lead2}PERIOD, {out_sel} FROM per_day "
            f"GROUP BY {lead2}PERIOD)")


def _total_agg(defn):
    """How the TOTAL column adds a measure up across the buckets, or None for no total column.

    sale        SUM — the buckets partition the range, so they add back up to it exactly.
    stock/avg   AVG — an average of the bucket averages. Labelled "Avg" on screen, never "Total".
    stock/close nothing. See the module docstring: a sum of closing positions is stock-days and
                an average of them is not what the word Total means.
    """
    if defn["kind"] == "sale":
        return "SUM"
    return "AVG" if defn["stock_basis"] == "avg" else None


def build_sql(defn, a, b):
    """Return (sql, keys, cols, periods) for one run."""
    dims = defn["group_by"]
    periods = _periods(a, b, defn["bucket"])
    if len(periods) > MAX_PERIODS:
        raise HTTPException(400, f"{len(periods)} {defn['bucket']} buckets in this range — the cap "
                                 f"is {MAX_PERIODS}. Pick a coarser bucket (week, month) or a "
                                 f"narrower range.")
    cte = _per_cte(defn, a, b)
    dim_csv = ", ".join(dims)

    if defn["layout"] == "long":
        cols = dims + ["PERIOD"] + defn["measures"]
        sel = ", ".join(list(dims) + ["PERIOD"] + [f'"{m}"' for m in defn["measures"]])
        return f"WITH {cte} SELECT {sel} FROM per", dims + ["PERIOD"], cols, periods

    total = _total_agg(defn)
    n_cols = len(periods) * len(defn["measures"]) + (len(defn["measures"]) if total else 0)
    if n_cols > MAX_PIVOT_COLS:
        raise HTTPException(400, f"{len(periods)} buckets x {len(defn['measures'])} measures is "
                                 f"{n_cols} columns — the cap is {MAX_PIVOT_COLS}. Pick a coarser "
                                 f"bucket, fewer measures, or switch to the Unpivot layout.")
    cols = list(dims)
    sel = list(dims)
    for p in periods:
        for m in defn["measures"]:
            alias = f"P_{p}__{m}"
            cols.append(alias)
            # The period labels are GENERATED here (_period_of), never caller text, so the literal
            # is ours; _q quotes it anyway rather than leaving that fact load-bearing.
            sel.append(f'ROUND(SUM(CASE WHEN PERIOD = {base._q(p)} THEN "{m}" END), 4) '
                       f'AS "{alias}"')
    if total:
        for m in defn["measures"]:
            alias = f"TOTAL__{m}"
            cols.append(alias)
            sel.append(f'ROUND({total}("{m}"), 4) AS "{alias}"')
    grp = f" GROUP BY {dim_csv}" if dims else ""
    return f"WITH {cte} SELECT {', '.join(sel)} FROM per{grp}", list(dims), cols, periods


# ══════════════════════════════════════════════════════════════════════════════
# endpoints
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/catalogue")
def trend_catalogue():
    """Everything the two pages need to render their pickers.

    Dimensions are grouped by where they come from, because a flat list of 160 names is unusable:
    somebody hunting for CLUSTER thinks "store attribute", not "column 47".
    """
    return {
        "dims": {
            "Fact": [c for c in rep.FACT_DIMS if c != "DT"] + ["GEN_ART_DESC"],
            "Store master": list(base.STORE_ATTRS),
            "Product master": list(base.PROD_ATTRS),
        },
        "stockDim": STOCK_DIM,
        "saleMeasures": SALE_MEASURES,
        "stockMeasures": STOCK_MEASURES,
        "stockTags": list(base.STOCK_TAGS),
        "buckets": BUCKETS,
        "stockBases": STOCK_BASES,
        "maxPeriods": MAX_PERIODS,
    }


def _order(cols, keys, sort_col, sort_dir):
    sc = str(sort_col or "")
    if sc in cols:
        direction = "DESC" if str(sort_dir or "").lower() == "desc" else "ASC"
        return f' ORDER BY "{sc}" {direction} NULLS LAST'
    return (" ORDER BY " + ", ".join(f'"{k}"' for k in keys)) if keys else ""


@router.post("/run")
def trend_run(req: TrendReq):
    defn = _clean(req)
    a, b = base._range(req.from_date, req.to_date)
    limit = max(1, min(int(req.limit or 200), MAX_LIMIT))
    page = max(1, int(req.page or 1))
    conn = base._conn()
    try:
        cur = conn.cursor()
        sql, keys, cols, periods = build_sql(defn, a, b)
        # The header conditions run on the FINISHED columns — a measure does not exist until after
        # the aggregation, and in pivot layout neither does a period column.
        where = rep._col_where_sql(cols, req.col_filters)
        # Counted THROUGH the same WHERE, so the row total and the page numbering agree with what
        # the filter left. A count of the unfiltered result would page into emptiness.
        cur.execute(f"SELECT COUNT(*) FROM ({sql}){where}")
        total = int(cur.fetchone()[0] or 0)
        cur.execute(f"SELECT * FROM ({sql}){where}"
                    f"{_order(cols, keys, req.sort_col, req.sort_dir)} "
                    f"LIMIT {limit} OFFSET {(page - 1) * limit}")
        out_cols = [c[0] for c in cur.description]
        rows = [[base._fmt_str(v) for v in r] for r in cur.fetchall()]
        return {"columns": out_cols, "keys": keys, "rows": rows, "total": total,
                "page": page, "limit": limit, "periods": periods,
                "totalAgg": _total_agg(defn), "definition": defn,
                "from": a, "to": b}
    finally:
        conn.close()


@router.post("/export")
def trend_export(req: TrendReq):
    """The grid, unpaged, as CSV — with the same conditions applied.

    An export that quietly dropped the header filters would hand back more rows than the page it
    was clicked from, which is the one thing a download must never do.
    """
    defn = _clean(req)
    a, b = base._range(req.from_date, req.to_date)
    conn = base._conn()
    cur = conn.cursor()
    sql, keys, cols, _p = build_sql(defn, a, b)
    where = rep._col_where_sql(cols, req.col_filters)
    cur.execute(f"SELECT * FROM ({sql}){where}{_order(cols, keys, req.sort_col, req.sort_dir)}")
    hdr = [c[0] for c in cur.description]
    name = f"{defn['kind']}_trend_{defn['bucket']}_{defn['layout']}_{a}_to_{b}.csv"
    return _stream_csv(cur, hdr, name)


@router.post("/values")
def trend_values(req: TrendReq):
    """The distinct values of one dimension over this range, for the header's tick-list.

    Read straight off the fact rather than through the trend aggregate: the question is "what
    values EXIST here", and the buckets and measures have no bearing on the answer. The run's own
    filters are deliberately NOT applied — a tick-list that hid the values you had just unticked
    would leave no way to tick them back on.
    """
    defn = _clean(req)
    col = str(req.column or "").strip().upper()
    allowed = DIM_COLS + ([STOCK_DIM] if defn["kind"] == "stock" else [])
    if col not in allowed:
        raise HTTPException(400, f"'{col}' is not a column this trend can filter on")
    a, b = base._range(req.from_date, req.to_date)
    tbl = base.SALE_TBL if defn["kind"] == "sale" else base.STOCK_TBL
    probe = {"group_by": [col], "filters": {}}
    conn = base._conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT DISTINCT {_expr(col)} AS V FROM {tbl} f{_joins(probe)} "
                    f"WHERE f.DT BETWEEN '{a}'::DATE AND '{b}'::DATE "
                    f"ORDER BY 1 LIMIT {MAX_VALUES}")
        return {"column": col, "values": [base._fmt_str(r[0]) for r in cur.fetchall()]}
    finally:
        conn.close()
