"""
Snowflake MIS — MODULE MONITOR (mounted at /sf-mis-monitor). Read-only, except /load-gap.

WHAT THIS ANSWERS, AND WHY NOTHING ELSE COULD
─────────────────────────────────────────────
The MIS module reports over two day-wise facts that are loaded a range at a time, by hand or by
schedule (db-scripts/298, SP_DAY_WISE_ST_VAR_SALE_LOAD / _STOCK_LOAD):

    BRONZE.DAY_WISE_ST_VAR_SALE     DT x ST_CD x ARTICLE_NUMBER
    BRONZE.DAY_WISE_ST_VAR_STOCK    DT x ST_CD x ARTICLE_NUMBER x KPI

Because every window - TD, MTD, YTD, LAST_6MONTH - is a SUM over a date range of those facts, a
day that was loaded for ONE STORE contributes that one store to the total and the report prints
the result in exactly the same typeface as a whole one. There is no cell, no colour and no
footnote that distinguishes them. /sf-var-combined/params already reports "512 days, 419 stores",
which reads as healthy and is the reason nobody looked.

Measured when this module was written (31-Aug-2026):

    SALE   512 days stored, of which  2 whole, 30 at 101-350 stores, 479 AT ONE STORE
    STOCK   11 days stored, of which  2 whole,  1 at 3 stores,          7 at one store
    2026-08-29 absent from the sale fact entirely, though VBRP holds 408 stores for it

So every window wider than two days was being answered from a single store's data. That is the
condition this page exists to make impossible to miss.

HOW A DAY IS SCORED — AGAINST THE PROC'S OWN SOURCE PREDICATE
─────────────────────────────────────────────────────────────
A day is scored by comparing the fact against the SOURCE ROWS THAT SHOULD HAVE PRODUCED IT, using
the predicate copied out of the loader proc itself (FKART_ANA='FP' AND SHKZG<>'X'). That is what
makes the comparison exact rather than approximate: on a properly loaded day the fact store count
and the source store count TIE - measured 413/413 on 31-Aug and 408/408 on 30-Aug - so a shortfall
is a real shortfall and not a scoping artefact. Scoring against "the number of stores that exist"
would have carried a permanent unexplained remainder and taught everyone to ignore the page.

    NOT_IN_SOURCE  the source has no rows for that day either. NOT a fault - a Sunday with no
                   billing, or a stock day never snapshotted. Shown in grey, never counted as a gap.
    MISSING        the source has the day and the fact does not. Always a fault.
    PARTIAL        loaded, but short - see the thresholds below.
    FULL           loaded to the source's own extent.

THE SALE SIDE IS SCORED ON MONEY AS WELL AS STORES
───────────────────────────────────────────────────
Store count alone cannot see a day that was loaded while it was still being billed. So the sale
day also ties SUM(SALE_V) back to SUM(NETWR) at source. Measured on whole days that ratio sits at
94.0-96.6%, NOT 100%: the loader joins DIM_PRODUCT and keeps SEG IN ('APP','GM') with a non-blank
MAJ_CAT, so out-of-scope articles are legitimately absent. The threshold is set at 80% - far below
the observed floor and far above the 0.1-0.9% a single-store day scores - so the band is
diagnostic rather than arbitrary.

Stock is deliberately NOT tied on value. Its source, ET_STOCK_DATA, is ~19.7M rows a day; summing
it across a 60-day window is a billion-row scan for a page that has to open quickly. Stock is
scored on store count only, and its threshold is lower (70%) because its structural remainder is
larger and cannot be cheaply removed: the same APP/GM product scope leaves 478 in-scope stores
against 516 reporting ones (92.6%), and replicating that filter would mean joining DIM_PRODUCT
across those 19.7M rows a day. The number is stated in the payload so the page can print it.

CLOCKS
──────
Every "today" and "yesterday" here is an IST day, computed in Python. Snowflake's CURRENT_DATE on
this account is America/Los_Angeles and is a day behind IST for the first 12.5 hours of every
Indian day, which would make the nightly health check declare yesterday missing every morning.
Run-log timestamps are TIMESTAMP_TZ and are returned with their offset, so the browser localises
them; nothing here formats a wall clock.

COST
────
The fact-side aggregates are cheap. The source-side ones are the expensive half, so every result
is TTL-cached (SF_MIS_MONITOR_CACHE_SEC, default 600) keyed on the exact range, and the strips
that the report pages call on every date change hit /range-health, which is the same cache.
"""
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from ..worker.auto_gandola_sf import get_sf_connection

logger = logging.getLogger(__name__)
router = APIRouter()

SALE_TBL = "V2RETAIL.BRONZE.DAY_WISE_ST_VAR_SALE"
STOCK_TBL = "V2RETAIL.BRONZE.DAY_WISE_ST_VAR_STOCK"
VBRP_TBL = "V2RETAIL.BRONZE.SAP_VBRP"
ETSTK_TBL = "V2RETAIL.BRONZE.ET_STOCK_DATA"
RUN_TBL = "V2RETAIL.BRONZE.MIS_LOAD_RUN"

# VBRP stores the billing date as text in more than one shape, so the loader coalesces two parses.
# Copied verbatim from SP_DAY_WISE_ST_VAR_SALE_LOAD: if this and the proc ever disagree, the
# monitor scores days the loader never wrote.
_VBRP_DT = ("COALESCE(TRY_TO_DATE(FKDAT_ANA,'YYYYMMDD'), TRY_TO_TIMESTAMP(FKDAT_ANA)::date)")
_VBRP_WHERE = "FKART_ANA = 'FP' AND NVL(SHKZG,'') <> 'X'"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
IST = timezone(timedelta(hours=5, minutes=30))

# The load endpoint caps a single call at 31 days because stock is ~19.7M rows a day. Gap ranges
# are split to the same size so every range this page proposes can actually be run as-is.
MAX_LOAD_DAYS = 31
# A guard on the generator that builds the calendar spine, and on how much a browser is asked to
# render. A year and a bit covers "is my YTD sound?", which is the widest real question.
MAX_RANGE_DAYS = 400


def _env_int(name, default):
    try:
        return int(str(os.getenv(name, "")).strip() or default)
    except ValueError:
        return default


def _env_float(name, default):
    try:
        return float(str(os.getenv(name, "")).strip() or default)
    except ValueError:
        return default


def cache_sec():
    return _env_int("SF_MIS_MONITOR_CACHE_SEC", 600)


# Thresholds are CONFIG, not literals buried in the scoring function, because the structural
# remainders they absorb are properties of the data and will drift. Defaults are set from measured
# values - see the module docstring - not from a round number that felt safe.
def th_sale_store():
    return _env_float("SF_MIS_TH_SALE_STORE", 0.80)


def th_sale_value():
    return _env_float("SF_MIS_TH_SALE_VALUE", 0.80)


def th_stock_store():
    return _env_float("SF_MIS_TH_STOCK_STORE", 0.70)


# ══════════════════════════════════════════════════════════════════════════════
# plumbing
# ══════════════════════════════════════════════════════════════════════════════
_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()


def _cached(key, ttl, fn):
    """fn()'s value, recomputed only when the cached copy is older than ttl seconds.

    A stale copy is served and FLAGGED when the refresh itself fails, so a Snowflake wobble
    degrades the monitor rather than emptying it - an empty monitor reads as "nothing is wrong",
    which is the one thing it must never say by accident.
    """
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit and now - hit["at"] < ttl:
        out = dict(hit["val"])
        out["cached"] = True
        out["cachedAgeSec"] = round(now - hit["at"])
        return out
    try:
        val = fn()
    except HTTPException:
        raise
    except Exception as e:                                    # noqa: BLE001
        if hit:
            logger.warning("[mis-monitor] %s refresh failed (%s) - serving cached copy", key, e)
            out = dict(hit["val"])
            out.update({"cached": True, "stale": True, "error": str(e)[:300],
                        "cachedAgeSec": round(now - hit["at"])})
            return out
        raise
    with _CACHE_LOCK:
        _CACHE[key] = {"at": now, "val": val}
    out = dict(val)
    out["cached"] = False
    return out


def _conn():
    try:
        return get_sf_connection()
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[mis-monitor] Snowflake connection failed: %s", e)
        raise HTTPException(503, f"Could not connect to Snowflake: {e}")


def _q(v):
    return "'" + str(v).replace("'", "''") + "'"


def _check_date(d, what):
    d = str(d or "").strip()
    if not _DATE_RE.match(d):
        raise HTTPException(400, f"{what} must be a date in YYYY-MM-DD form")
    try:
        datetime.fromisoformat(d)
    except ValueError:
        raise HTTPException(400, f"{what} is not a real date ({d})")
    return d


def _range(a, b):
    a, b = _check_date(a, "from"), _check_date(b, "to")
    if a > b:
        raise HTTPException(400, f"from ({a}) is after to ({b})")
    n = (datetime.fromisoformat(b) - datetime.fromisoformat(a)).days + 1
    if n > MAX_RANGE_DAYS:
        raise HTTPException(400, f"{n} days requested; this page scores at most {MAX_RANGE_DAYS} "
                                 f"at a time. Narrow the range.")
    return a, b, n


def _today_ist():
    """The business day, not Snowflake's. CURRENT_DATE on this account is America/Los_Angeles and
    is a day behind IST until 12:30 IST, which would make a morning health check report yesterday
    as missing every single day."""
    return datetime.now(IST).date()


def _iso(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _num(v):
    return None if v is None else float(v)


def _dicts(cur, sql):
    cur.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ══════════════════════════════════════════════════════════════════════════════
# scoring
# ══════════════════════════════════════════════════════════════════════════════
VERDICTS = ["FULL", "PARTIAL", "MISSING", "NOT_IN_SOURCE"]


def _spine(a, n):
    """A row per calendar day, so a day ABSENT from the fact still produces a row. Without this the
    grid would show only days that were loaded, and a missing day - the most important thing here -
    would be invisible by construction."""
    return (f"SELECT DATEADD(day, SEQ4(), {_q(a)}::DATE) DT "
            f"FROM TABLE(GENERATOR(ROWCOUNT => {int(n)}))")


def _sale_sql(a, b, n):
    return f"""
WITH days AS ({_spine(a, n)}),
f AS (SELECT DT, COUNT(*) N, COUNT(DISTINCT ST_CD) S,
             SUM(SALE_V) V, MAX(UPDATE_DT) UPD
        FROM {SALE_TBL} WHERE DT BETWEEN {_q(a)} AND {_q(b)} GROUP BY 1),
s AS (SELECT {_VBRP_DT} DT, COUNT(DISTINCT WERKS) S,
             SUM(TRY_TO_DECIMAL(NETWR,38,2)) V
        FROM {VBRP_TBL}
       WHERE {_VBRP_WHERE} AND {_VBRP_DT} BETWEEN {_q(a)} AND {_q(b)} GROUP BY 1)
SELECT d.DT DT, NVL(f.N,0) FACT_ROWS, NVL(f.S,0) FACT_ST, f.V FACT_V, f.UPD LAST_UPD,
       NVL(s.S,0) SRC_ST, s.V SRC_V
  FROM days d LEFT JOIN f ON f.DT = d.DT LEFT JOIN s ON s.DT = d.DT
 ORDER BY d.DT"""


def _stock_sql(a, b, n):
    # No value tie-out and no product-scoped source count: see the module docstring. The cheap
    # DISTINCT WERKS is enough to separate "one store" from "every store", which is the failure
    # that actually happened.
    return f"""
WITH days AS ({_spine(a, n)}),
f AS (SELECT DT, COUNT(*) N, COUNT(DISTINCT ST_CD) S, COUNT(DISTINCT KPI) TAGS,
             MAX(UPDATE_DT) UPD
        FROM {STOCK_TBL} WHERE DT BETWEEN {_q(a)} AND {_q(b)} GROUP BY 1),
s AS (SELECT STOCK_DATE::date DT, COUNT(DISTINCT WERKS) S
        FROM {ETSTK_TBL} WHERE STOCK_DATE::date BETWEEN {_q(a)} AND {_q(b)} GROUP BY 1)
SELECT d.DT DT, NVL(f.N,0) FACT_ROWS, NVL(f.S,0) FACT_ST, NULL FACT_V, f.UPD LAST_UPD,
       NVL(s.S,0) SRC_ST, NULL SRC_V, NVL(f.TAGS,0) TAGS
  FROM days d LEFT JOIN f ON f.DT = d.DT LEFT JOIN s ON s.DT = d.DT
 ORDER BY d.DT"""


def _score(kind, r):
    """One day -> a verdict and the two ratios behind it.

    Order matters. NOT_IN_SOURCE is tested FIRST, so a day the source never had can never be
    reported as a gap: telling someone to load a Sunday that was never billed is how a monitor
    trains its readers to stop believing it.
    """
    fact_st, src_st = int(r["FACT_ST"] or 0), int(r["SRC_ST"] or 0)
    fact_v, src_v = _num(r.get("FACT_V")), _num(r.get("SRC_V"))
    st_pct = (fact_st / src_st) if src_st else None
    v_pct = (fact_v / src_v) if (src_v and fact_v is not None and src_v > 0) else None

    if src_st == 0:
        verdict, why = "NOT_IN_SOURCE", "the source has no rows for this day either"
    elif fact_st == 0:
        verdict, why = "MISSING", f"the source has {src_st} stores for this day; the fact has none"
    else:
        st_th = th_sale_store() if kind == "sale" else th_stock_store()
        if st_pct is not None and st_pct < st_th:
            verdict = "PARTIAL"
            why = f"{fact_st} of {src_st} stores loaded ({st_pct * 100:.1f}%)"
        elif kind == "sale" and v_pct is not None and v_pct < th_sale_value():
            # Stores all present but the money is short - a day loaded while it was still being
            # billed. Store count alone cannot see this.
            verdict = "PARTIAL"
            why = (f"all {fact_st} stores present but only {v_pct * 100:.1f}% of the day's billed "
                   f"value - loaded before the day finished?")
        else:
            verdict = "FULL"
            why = f"{fact_st} of {src_st} stores"
    return {
        "dt": _iso(r["DT"]),
        "verdict": verdict,
        "why": why,
        "factRows": int(r["FACT_ROWS"] or 0),
        "factStores": fact_st,
        "srcStores": src_st,
        "storePct": None if st_pct is None else round(st_pct * 100, 1),
        "factValue": fact_v,
        "srcValue": src_v,
        "valuePct": None if v_pct is None else round(v_pct * 100, 1),
        "tags": int(r["TAGS"]) if r.get("TAGS") is not None else None,
        "lastLoadedAt": _iso(r.get("LAST_UPD")),
    }


def _coverage(a, b, n, kinds):
    conn = _conn()
    try:
        cur = conn.cursor()
        out = {}
        for kind in kinds:
            sql = _sale_sql(a, b, n) if kind == "sale" else _stock_sql(a, b, n)
            out[kind] = [_score(kind, r) for r in _dicts(cur, sql)]
    finally:
        conn.close()
    return out


def _tally(days):
    t = {v: 0 for v in VERDICTS}
    for d in days:
        t[d["verdict"]] = t.get(d["verdict"], 0) + 1
    # Days that COULD be loaded and are not sound. NOT_IN_SOURCE is excluded on purpose - it is
    # not actionable, and counting it would make every quiet Sunday look like work to do.
    t["actionable"] = t["MISSING"] + t["PARTIAL"]
    return t


def _thresholds():
    return {
        "saleStorePct": round(th_sale_store() * 100, 1),
        "saleValuePct": round(th_sale_value() * 100, 1),
        "stockStorePct": round(th_stock_store() * 100, 1),
        "note": ("A whole SALE day ties to source EXACTLY on stores (measured 413/413) and to "
                 "94-96.6% on value - the shortfall is the loader's SEG IN ('APP','GM') product "
                 "scope. A whole STOCK day reaches ~92.6% of reporting stores (478 of 516) for "
                 "the same reason, which is why its threshold is lower. Stock is not tied on "
                 "value: its source is ~19.7M rows a day and summing it would not open quickly."),
    }


# ══════════════════════════════════════════════════════════════════════════════
# coverage — the day grid
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/coverage")
def coverage(from_date: str = Query(..., alias="from"),
             to_date: str = Query(..., alias="to"),
             kind: str = "both",
             refresh: int = 0):
    a, b, n = _range(from_date, to_date)
    kinds = ["sale", "stock"] if kind == "both" else [kind]
    for k in kinds:
        if k not in ("sale", "stock"):
            raise HTTPException(400, "kind must be sale, stock or both")

    def _load():
        cov = _coverage(a, b, n, kinds)
        return {"from": a, "to": b, "days": n,
                "coverage": cov,
                "tally": {k: _tally(v) for k, v in cov.items()},
                "thresholds": _thresholds()}

    key = f"cov:{a}:{b}:{','.join(kinds)}"
    if refresh:
        with _CACHE_LOCK:
            _CACHE.pop(key, None)
    return _cached(key, cache_sec(), _load)


# ══════════════════════════════════════════════════════════════════════════════
# range-health — the compact verdict the REPORT pages show above their results
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/range-health")
def range_health(from_date: str = Query(..., alias="from"),
                 to_date: str = Query(..., alias="to")):
    """Is the range the user is about to report on actually sound?

    This is the endpoint that matters most, because it fires where the wrong number is READ rather
    than where it is loaded. It shares /coverage's cache, so a page calling it on every date change
    costs one Snowflake read per range per TTL, not one per keystroke.

    It answers with the WORST days rather than a percentage: "37 of 60 days are single-store" is
    actionable, "62% healthy" is not.
    """
    a, b, n = _range(from_date, to_date)

    def _load():
        cov = _coverage(a, b, n, ["sale", "stock"])
        tally = {k: _tally(v) for k, v in cov.items()}
        worst = {}
        for k, days in cov.items():
            bad = [d for d in days if d["verdict"] in ("MISSING", "PARTIAL")]
            bad.sort(key=lambda d: (d["storePct"] if d["storePct"] is not None else -1))
            worst[k] = bad[:10]
        # One sentence a page can print without doing arithmetic of its own.
        sale_bad, stock_bad = tally["sale"]["actionable"], tally["stock"]["actionable"]
        if sale_bad == 0 and stock_bad == 0:
            headline, level = "Every day in this range is fully loaded.", "ok"
        else:
            parts = []
            if sale_bad:
                parts.append(f"{sale_bad} of {n} sale days")
            if stock_bad:
                parts.append(f"{stock_bad} of {n} stock days")
            headline = (" and ".join(parts) + " in this range are missing or only partly loaded, "
                        "so the totals below are short by whatever those days held.")
            # A range where MOST days are unsound is not a warning, it is a wrong answer.
            worst_share = max(sale_bad, stock_bad) / max(n, 1)
            level = "error" if worst_share >= 0.5 else "warn"
        return {"from": a, "to": b, "days": n, "level": level, "headline": headline,
                "tally": tally, "worst": worst, "thresholds": _thresholds()}

    return _cached(f"health:{a}:{b}", cache_sec(), _load)


# ══════════════════════════════════════════════════════════════════════════════
# gaps — what to load, in ranges the load endpoint will actually accept
# ══════════════════════════════════════════════════════════════════════════════
def _collapse(days, kind):
    """Consecutive actionable days -> contiguous ranges, each capped at MAX_LOAD_DAYS.

    Ranges, not days: the procs scan VBRP and ET_STOCK_DATA once per CALL, so loading 10 days as
    one call does the expensive part once instead of ten times. The 31-day cap is the load
    endpoint's own limit, applied here so every range this page proposes can be run without
    being re-split by hand.
    """
    bad = [d for d in days if d["verdict"] in ("MISSING", "PARTIAL")]
    out, cur = [], None
    for d in bad:
        dt = datetime.fromisoformat(d["dt"]).date()
        if (cur and (dt - cur["_end"]).days == 1
                and cur["days"] < MAX_LOAD_DAYS):
            cur["_end"], cur["to"] = dt, d["dt"]
            cur["days"] += 1
            cur["missing"] += 1 if d["verdict"] == "MISSING" else 0
            cur["partial"] += 1 if d["verdict"] == "PARTIAL" else 0
        else:
            if cur:
                out.append(cur)
            cur = {"kind": kind, "from": d["dt"], "to": d["dt"], "_end": dt, "days": 1,
                   "missing": 1 if d["verdict"] == "MISSING" else 0,
                   "partial": 1 if d["verdict"] == "PARTIAL" else 0}
    if cur:
        out.append(cur)
    for r in out:
        r.pop("_end", None)
    return out


@router.get("/gaps")
def gaps(from_date: str = Query(..., alias="from"),
         to_date: str = Query(..., alias="to"),
         kind: str = "both"):
    a, b, n = _range(from_date, to_date)
    kinds = ["sale", "stock"] if kind == "both" else [kind]

    def _load():
        cov = _coverage(a, b, n, kinds)
        ranges = []
        for k in kinds:
            ranges.extend(_collapse(cov[k], k))
        # Widest first: the range that fixes the most days is the one worth running.
        ranges.sort(key=lambda r: -r["days"])
        return {"from": a, "to": b, "ranges": ranges,
                "totalDays": sum(r["days"] for r in ranges),
                "maxLoadDays": MAX_LOAD_DAYS}

    return _cached(f"gaps:{a}:{b}:{','.join(kinds)}", cache_sec(), _load)


# ══════════════════════════════════════════════════════════════════════════════
# runs — the durable load log
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/runs")
def runs(days: int = 30, limit: int = 200):
    days = max(1, min(int(days or 30), 365))
    limit = max(1, min(int(limit or 200), 1000))
    conn = _conn()
    try:
        cur = conn.cursor()
        try:
            rows = _dicts(cur, f"""
                SELECT RUN_ID, RUN_GROUP, KIND, FROM_DT, TO_DT, N_DAYS, STORES, N_STORES,
                       TRIGGERED_BY, OPERATOR, STATUS, ROWS_DELETED, ROWS_INSERTED,
                       PROC_MSG, ERROR_MSG, STARTED_AT, FINISHED_AT, DURATION_MS, HOST_NAME
                  FROM {RUN_TBL}
                 WHERE STARTED_AT >= DATEADD(day, -{days}, CURRENT_TIMESTAMP())
                 ORDER BY STARTED_AT DESC
                 LIMIT {limit}""")
        except Exception as e:                                # noqa: BLE001
            # The table is created by db-scripts/305. Before it is run the rest of the page must
            # still work, so this reports the absence instead of failing the request.
            logger.warning("[mis-monitor] run log unavailable: %s", e)
            return {"runs": [], "error": f"{RUN_TBL} not readable - has db-scripts/305 been run? ({e})"}
    finally:
        conn.close()

    out = []
    for r in rows:
        out.append({
            "runId": r["RUN_ID"], "runGroup": r["RUN_GROUP"], "kind": r["KIND"],
            "from": _iso(r["FROM_DT"]), "to": _iso(r["TO_DT"]), "days": int(r["N_DAYS"] or 0),
            # NULL means every store, which is what the procs' P_STORE=NULL means. Spelled out
            # here rather than left as a blank cell for the page to guess at.
            "stores": r["STORES"], "nStores": int(r["N_STORES"] or 0),
            "allStores": not r["STORES"],
            "triggeredBy": r["TRIGGERED_BY"], "operator": r["OPERATOR"], "status": r["STATUS"],
            "rowsDeleted": None if r["ROWS_DELETED"] is None else int(r["ROWS_DELETED"]),
            "rowsInserted": None if r["ROWS_INSERTED"] is None else int(r["ROWS_INSERTED"]),
            "procMsg": r["PROC_MSG"], "error": r["ERROR_MSG"],
            "startedAt": _iso(r["STARTED_AT"]), "finishedAt": _iso(r["FINISHED_AT"]),
            "durationMs": None if r["DURATION_MS"] is None else int(r["DURATION_MS"]),
            "host": r["HOST_NAME"],
        })
    return {"runs": out, "days": days}


# ══════════════════════════════════════════════════════════════════════════════
# overview — what the page opens on
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/overview")
def overview(days: int = 60, refresh: int = 0):
    days = max(7, min(int(days or 60), MAX_RANGE_DAYS))
    today = _today_ist()
    a = (today - timedelta(days=days - 1)).isoformat()
    b = today.isoformat()

    def _load():
        cov = _coverage(a, b, days, ["sale", "stock"])
        tally = {k: _tally(v) for k, v in cov.items()}

        # "Freshest sound day" is the headline number, not "max DT". Max DT answers "when did
        # something last land", which on this module was 31-Aug while most of the month held one
        # store - the exact reading that let the problem persist.
        freshest = {}
        for k, ds in cov.items():
            ok = [d for d in ds if d["verdict"] == "FULL"]
            freshest[k] = ok[-1]["dt"] if ok else None

        conn = _conn()
        try:
            cur = conn.cursor()
            # Span of the whole fact, beyond the scored window, so the page can say how much
            # history exists as well as how much of it is sound.
            span = _dicts(cur, f"""
                SELECT 'sale' KIND, MIN(DT) MIN_DT, MAX(DT) MAX_DT, COUNT(DISTINCT DT) N_DAYS,
                       COUNT(*) N_ROWS FROM {SALE_TBL}
                UNION ALL
                SELECT 'stock', MIN(DT), MAX(DT), COUNT(DISTINCT DT), COUNT(*) FROM {STOCK_TBL}""")
            try:
                last_runs = _dicts(cur, f"""
                    SELECT KIND, STATUS, STARTED_AT, FINISHED_AT, DURATION_MS,
                           ROWS_INSERTED, TRIGGERED_BY, FROM_DT, TO_DT
                      FROM {RUN_TBL} QUALIFY ROW_NUMBER() OVER (PARTITION BY KIND
                                                                ORDER BY STARTED_AT DESC) = 1""")
                stale = _dicts(cur, "SELECT * FROM V2RETAIL.BRONZE.V_MIS_LOAD_RUN_STALE "
                                    "WHERE AGE_MIN > 120 LIMIT 20")
                run_err = None
            except Exception as e:                            # noqa: BLE001
                last_runs, stale, run_err = [], [], str(e)[:300]
        finally:
            conn.close()

        return {
            "window": {"from": a, "to": b, "days": days},
            "todayIst": b,
            "tally": tally,
            "freshestSoundDay": freshest,
            "span": {r["KIND"]: {"minDt": _iso(r["MIN_DT"]), "maxDt": _iso(r["MAX_DT"]),
                                 "days": int(r["N_DAYS"] or 0), "rows": int(r["N_ROWS"] or 0)}
                     for r in span},
            "lastRuns": {r["KIND"]: {"status": r["STATUS"], "startedAt": _iso(r["STARTED_AT"]),
                                     "finishedAt": _iso(r["FINISHED_AT"]),
                                     "durationMs": (None if r["DURATION_MS"] is None
                                                    else int(r["DURATION_MS"])),
                                     "rowsInserted": (None if r["ROWS_INSERTED"] is None
                                                      else int(r["ROWS_INSERTED"])),
                                     "triggeredBy": r["TRIGGERED_BY"],
                                     "from": _iso(r["FROM_DT"]), "to": _iso(r["TO_DT"])}
                         for r in last_runs},
            "staleRuns": [{"runId": r["RUN_ID"], "kind": r["KIND"], "ageMin": int(r["AGE_MIN"] or 0),
                           "from": _iso(r["FROM_DT"]), "to": _iso(r["TO_DT"]),
                           "host": r["HOST_NAME"], "startedAt": _iso(r["STARTED_AT"])}
                          for r in stale],
            "runLogError": run_err,
            "thresholds": _thresholds(),
            "coverage": cov,
        }

    key = f"overview:{a}:{b}"
    if refresh:
        with _CACHE_LOCK:
            _CACHE.pop(key, None)
    return _cached(key, cache_sec(), _load)


# ══════════════════════════════════════════════════════════════════════════════
# health — the one the SCHEDULER calls, and the only endpoint with a pass/fail
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/health")
def health(days: int = 7):
    """Did the recent past load? Returns ok=false with a reason a scheduler can log.

    Scored over a trailing window rather than yesterday alone: a check that only looks at
    yesterday passes every day of a week in which nothing but yesterday ever loads, which is a
    fair description of how this module got into the state that prompted the monitor.

    'Yesterday' is an IST day. Snowflake's CURRENT_DATE would be a day behind for the first
    12.5 hours of every Indian day and the check would cry wolf every morning.
    """
    days = max(2, min(int(days or 7), 90))
    today = _today_ist()
    # Yesterday backwards: today is still being billed and is expected to be short.
    b = (today - timedelta(days=1)).isoformat()
    a = (today - timedelta(days=days)).isoformat()
    n = days

    def _load():
        cov = _coverage(a, b, n, ["sale", "stock"])
        tally = {k: _tally(v) for k, v in cov.items()}
        problems = []
        for k, ds in cov.items():
            y = next((d for d in ds if d["dt"] == b), None)
            if y and y["verdict"] == "MISSING":
                problems.append(f"{k}: yesterday ({b}) is not loaded - {y['why']}")
            elif y and y["verdict"] == "PARTIAL":
                problems.append(f"{k}: yesterday ({b}) is only partly loaded - {y['why']}")
            n_bad = tally[k]["actionable"]
            if n_bad > 1:
                problems.append(f"{k}: {n_bad} of the last {n} days are missing or partial")
        return {"ok": not problems, "from": a, "to": b, "days": n,
                "problems": problems, "tally": tally,
                "checkedAt": datetime.now(timezone.utc).isoformat()}

    return _cached(f"hc:{a}:{b}", min(cache_sec(), 300), _load)


@router.post("/cache/clear")
def cache_clear():
    with _CACHE_LOCK:
        n = len(_CACHE)
        _CACHE.clear()
    return {"cleared": n}
