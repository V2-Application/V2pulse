"""
Snowflake MIS — DEPENDENCIES (mounted at /sf-mis-lineage).

Every object the MIS module reads or writes, in one list, with live row counts and the last time
each changed. Answers the question a report reader actually has: "this number came from where, and
when was that last refreshed?"

WHY THE MAP IS CURATED AND THE NUMBERS ARE LIVE
-----------------------------------------------
The ROLE an object plays - source, fact, registry, published view - is a fact about the pipeline,
not about the database, and nothing in Snowflake records it. ACCOUNT_USAGE.OBJECT_DEPENDENCIES can
tell you a view references a table; it cannot tell you that ET_STOCK_DATA is the stock SOURCE while
DAY_WISE_ST_VAR_STOCK is what the report reads. So the roles are declared here and the counts,
sizes and timestamps come from INFORMATION_SCHEMA at request time. A curated list also fails
LOUDLY: an object that disappears shows as MISSING rather than quietly dropping out of a
discovered set, which is the failure mode that matters for a lineage page.

The one part that IS discovered is the published report views (SILVER.VW_MIS_*), because those are
created and dropped by the report builder itself - see sf_mis_report._sync_view - so a fixed list
would go stale the first time someone saves a report.

CHAIN, not just a list. Each entry says what FEEDS it, so the page can show sale, stock and KPI as
three columns of the same shape rather than one flat inventory where the reader has to know which
table matters. See PIPE below - the order within a pipe IS the order data moves.
"""
import io
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from . import sf_var_combined as base
from . import sf_mis_report as rep

logger = logging.getLogger(__name__)
router = APIRouter()

DB = "V2RETAIL"

# The phrase sf_mis_report._sync_view writes into every view it publishes. It is what separates a
# report's view from a hand-built one that merely shares the VW_MIS_ prefix - see _published_views.
OWNED_MARK = "Auto-published from BRONZE.MIS_REPORT_DEF slug"

# Each row: (pipe, stage, schema, name, note)
#
# `stage` is the position in the chain, and it is what lets the page render a lineage rather than an
# inventory: SOURCE -> LOADER -> FACT -> READ BY. The stock pipe has three sources because the fact
# is assembled from three different systems (db-scripts 302/312/313/314), which is exactly the sort
# of thing nobody remembers when a number looks wrong.
PIPE = [
    # ── SALE ────────────────────────────────────────────────────────────────
    ("SALE", "SOURCE", "BRONZE", "VBRP",
     "SAP billing lines. FKART_ANA='FP'. FKDAT_ANA is TEXT in TWO formats (8-char YYYYMMDD and "
     "19-char timestamp) - never filter it as a plain text range."),
    ("SALE", "SOURCE", "BRONZE", "SAP_VBRP",
     "A compat VIEW over BRONZE.VBRP. Four KPI procs hard-code this name, so dropping it breaks "
     "every KPI load."),
    ("SALE", "FACT", "BRONZE", "DAY_WISE_ST_VAR_SALE",
     "One row per DT x ST_CD x ARTICLE_NUMBER. THE table every sale window is summed from - "
     "TD/MTD/QTR/YTD are ranges over this, nothing is pre-computed."),
    ("SALE", "FACT", "BRONZE", "DAY_WISE_ST_VAR_SALE_KPI",
     "Optional fast path: the nine windows materialised per ANCHOR day, straight from VBRP "
     "(db-scripts/309). Correct even when the day-wise fact has no history."),
    ("SALE", "REGISTRY", "BRONZE", "DAY_WISE_ST_VAR_SALE_KPI_LOG",
     "One row per stored anchor. STORE_SCOPE='ALL' is what makes an anchor usable; every 'is this "
     "anchor usable?' question reads here, never the 20M-row KPI table."),

    # ── STOCK ───────────────────────────────────────────────────────────────
    ("STOCK", "SOURCE", "BRONZE", "ET_STOCK_DATA",
     "Store stock by storage location (LGORT). Every non-blank LGORT loads since db-scripts/312 - "
     "there is no location whitelist any more."),
    ("STOCK", "SOURCE", "BRONZE", "LQUA_SNAPSHOT",
     "Warehouse bin stock WITH a date (SNAP_DATE). Read instead of BRONZE.LQUA, which has no date "
     "column - feeds the LQ_/LQL_/LQB_ tags."),
    ("STOCK", "SOURCE", "BRONZE", "MBEW",
     "Moving average price. Values LQUA quantity as VERME x VERPR / PEINH, own plant only. MATNR "
     "here is NOT zero-padded (13/10 chars) while the PRD/PTL/HUB sources are 18-char padded - "
     "every join strips zeros both sides or it silently matches nothing."),
    ("STOCK", "SOURCE", "BRONZE", "ET_ZWM_PRD_BCG_REP_RFC",
     "Packed and awaiting dispatch. Feeds one tag per shipping point (<VSTEL>_PRD, so DH24_PRD and "
     "DW01_PRD), credited to SITE. Qty VEMNG, value VEMNG x its own VERPR column - no MBEW needed "
     "(db-scripts/316). Replaced the single PRD tag that 315 removed."),
    ("STOCK", "SOURCE", "BRONZE", "ET_ZWM_PTL_STK_RFC",
     "Put-to-light stock. Feeds the PTL tag, attributed to DWERKS (destination store) but PRICED at "
     "SWERKS, the DC holding it - measured 99.4% of rows can be valued at the DC against 47.9% at "
     "the store (db-scripts/317)."),
    ("STOCK", "SOURCE", "BRONZE", "ZWM_PRD_HUB",
     "Hub PRD. Feeds one tag per HU type (F_HUBPRD, G_HUBPRD), credited to STORE_WERKS. Uses its "
     "own VALUE column, which equals QTY x MAP to 0.00% - so it ties to source exactly "
     "(db-scripts/318). NOTE: G_HUBPRD loads only ~29% of source qty; the rest is outside the "
     "APP/GM product scope every block applies."),
    ("STOCK", "FACT", "BRONZE", "DAY_WISE_ST_VAR_STOCK",
     "One row per DT x ST_CD x ARTICLE_NUMBER x KPI(tag). A POSITION, never summed across days. "
     "Tag families OVERLAP - see the report's own column notes before totalling."),

    # ── MASTERS, shared by both ─────────────────────────────────────────────
    ("MASTER", "MASTER", "SILVER", "VW_PRODUCT_MASTER",
     "THE product master. Scopes both loaders to SEG IN ('APP','GM') with a non-blank MAJ_CAT."),
    ("MASTER", "MASTER", "AKS_SILVER", "SLV_DIM_STORE",
     "THE store master. LEFT JOINed, so a DC absent from it still reports its numbers."),

    # ── REPORTS ─────────────────────────────────────────────────────────────
    ("REPORTS", "DEFINITION", "BRONZE", "MIS_REPORT_DEF",
     "The saved reports themselves, as JSON. A few hundred bytes each - nothing is materialised "
     "per report, every run re-aggregates."),
    ("REPORTS", "RUN LOG", "BRONZE", "MIS_LOAD_RUN",
     "One row per loader run. ROWS_DELETED / ROWS_INSERTED are scraped from the proc's message; "
     "PROC_MSG keeps the prose so a failed parse is recoverable."),
]

# The procs are objects too, and a lineage page that omitted them would leave the reader wondering
# what turns a source into a fact.
PROCS = [
    ("SALE", "LOADER", "SP_DAY_WISE_ST_VAR_SALE_LOAD", "VBRP -> DAY_WISE_ST_VAR_SALE, by date range."),
    ("SALE", "LOADER", "SP_DAY_WISE_ST_VAR_SALE_KPI_LOAD", "VBRP -> the nine stored windows, ONE anchor."),
    ("SALE", "LOADER", "SP_DAY_WISE_ST_VAR_SALE_KPI_PURGE", "Drops stored anchors before a date."),
    ("STOCK", "LOADER", "SP_DAY_WISE_ST_VAR_STOCK_LOAD",
     "ET_STOCK_DATA + LQUA_SNAPSHOT + ET_ZWM_PRD_BCG_REP_RFC + ET_ZWM_PTL_STK_RFC + ZWM_PRD_HUB "
     "-> DAY_WISE_ST_VAR_STOCK, in seven blocks. ZWM_PRD_DATE1 is no longer read (315)."),
]


# HOW FRESH IS THE DATA, as opposed to when the object was last altered.
#
# INFORMATION_SCHEMA.LAST_ALTERED is metadata about the OBJECT. It moves when the table is written,
# but it says nothing about WHICH DAY the data now reaches - and "which day" is the question a
# reader of a short MTD actually has. So each object that carries a date gets probed for two things:
#
#   dataThrough  MAX(<the date column>)  - the newest day the table holds
#   lastLoaded   MAX(<the load stamp>)   - when that content was last written
#
# (table, date column, load-stamp column). A NULL column name means the table has no such stamp.
# Probed with MAX() on NATIVE date/timestamp columns only, which Snowflake answers from metadata
# rather than a scan - ET_STOCK_DATA is 2.5 billion rows and still costs nothing here.
#
# VBRP, SAP_VBRP and MBEW are deliberately ABSENT. Their date columns are TEXT (and
# VBRP's holds two different formats), so a MAX would be a string comparison over hundreds of
# millions of rows - wrong AND slow. Better to show a dash than a number nobody should trust.
_FRESHNESS = {
    ("BRONZE", "DAY_WISE_ST_VAR_SALE"):         ("DT", "UPDATE_DT"),
    ("BRONZE", "DAY_WISE_ST_VAR_STOCK"):        ("DT", "UPDATE_DT"),
    ("BRONZE", "DAY_WISE_ST_VAR_SALE_KPI"):     ("DT", "UPDATE_DT"),
    ("BRONZE", "DAY_WISE_ST_VAR_SALE_KPI_LOG"): ("DT", "LOADED_AT"),
    ("BRONZE", "ET_STOCK_DATA"):                ("STOCK_DATE", None),
    ("BRONZE", "LQUA_SNAPSHOT"):                ("SNAP_DATE", "SNAP_TS"),
    ("BRONZE", "ET_ZWM_PRD_BCG_REP_RFC"):       ("PRD_DATE", None),
    ("BRONZE", "ET_ZWM_PTL_STK_RFC"):           ("PUR_DATE", None),
    ("BRONZE", "ZWM_PRD_HUB"):                  ("PUR_DATE", None),
    ("BRONZE", "MIS_LOAD_RUN"):                 (None, "STARTED_AT"),
    ("BRONZE", "MIS_REPORT_DEF"):               (None, "UPDATED_AT"),
    ("AKS_SILVER", "SLV_DIM_STORE"):            (None, "UPDATED_AT"),
}


def _freshness(cur, present):
    """MAX(date) and MAX(load stamp) per probeable object, in ONE round trip.

    UNION ALL rather than a query each: this page lists twenty-odd objects and a round trip apiece
    would make a metadata page feel like a report. Wrapped so one unprobeable table cannot fail the
    whole page - a dash in two columns is a far smaller loss than an error instead of the list.
    """
    parts = []
    for (sch, name), (dcol, ucol) in _FRESHNESS.items():
        if (sch, name) not in present:
            continue
        d = f"MAX({dcol})::VARCHAR" if dcol else "NULL::VARCHAR"
        u = f"MAX({ucol})::VARCHAR" if ucol else "NULL::VARCHAR"
        parts.append(f"SELECT {base._q(sch)} S, {base._q(name)} N, {d} D, {u} U "
                     f"FROM {DB}.{sch}.{name}")
    if not parts:
        return {}
    try:
        cur.execute(" UNION ALL ".join(parts))
        return {(s, n): {"dataThrough": d, "lastLoaded": u} for s, n, d, u in cur.fetchall()}
    except Exception as e:
        logger.warning("[mis-lineage] freshness probe failed: %s", e)
        return {}


def _stats(cur, wanted):
    """Live row counts, bytes and last-altered for the curated objects, in ONE query per schema.

    INFORMATION_SCHEMA.TABLES covers views too (TABLE_TYPE tells them apart), and its ROW_COUNT is
    metadata rather than a scan - so this page costs nothing whatever the tables weigh. Views
    report NULL rows, which is honest: a view has no rows of its own.
    """
    out = {}
    schemas = sorted({s for s, _ in wanted})
    for sch in schemas:
        names = sorted({n for s, n in wanted if s == sch})
        in_list = ", ".join(base._q(n) for n in names)
        cur.execute(f"""
            SELECT TABLE_NAME, TABLE_TYPE, ROW_COUNT, BYTES, LAST_ALTERED, CREATED
              FROM {DB}.INFORMATION_SCHEMA.TABLES
             WHERE TABLE_SCHEMA = {base._q(sch)} AND TABLE_NAME IN ({in_list})""")
        for nm, typ, rows, byts, alt, crt in cur.fetchall():
            out[(sch, nm)] = {
                "type": "VIEW" if "VIEW" in str(typ).upper() else "TABLE",
                "rows": int(rows) if rows is not None else None,
                "bytes": int(byts) if byts is not None else None,
                "lastAltered": base._fmt_str(alt), "created": base._fmt_str(crt),
            }
    return out


def _published_views(cur):
    """The report builder's own SILVER views - discovered, because it creates and drops them.

    STARTSWITH, not LIKE. Snowflake rejects `ESCAPE '\\'` outright, and an unescaped
    LIKE 'VW_MIS_%' reads both underscores as single-character wildcards - so it would also match
    names that are not ours.
    """
    cur.execute(f"""
        SELECT TABLE_NAME, LAST_ALTERED, CREATED, NVL(COMMENT,'')
          FROM {DB}.INFORMATION_SCHEMA.VIEWS
         WHERE TABLE_SCHEMA = 'SILVER' AND STARTSWITH(TABLE_NAME, 'VW_MIS_')
         ORDER BY TABLE_NAME""")
    return [{"schema": "SILVER", "name": n, "lastAltered": base._fmt_str(a),
             "created": base._fmt_str(c), "comment": cm or "",
             # OWNERSHIP MUST BE PROVEN, NOT INFERRED FROM THE NAME.
             #
             # The VW_MIS_ prefix is NOT exclusive to this module. Found in SILVER on 2026-09-02:
             # VW_MIS_STATE_SSN_FIX ("Published read surface for the STATE-SSN MODEL CONT% model")
             # and VW_MIS_STATE_MAJCAT_FIX - both hand-built by other work, neither related to a
             # saved report. Treating the prefix as ownership marked them ORPHAN and put them one
             # click from being dropped.
             #
             # _sync_view stamps every view it publishes with this exact phrase, so the comment is
             # the only reliable proof. A view without it is somebody else's and is never touched.
             "owned": OWNED_MARK in (cm or "")}
            for n, a, c, cm in cur.fetchall()]


def _proc_status(cur):
    """Which of the loader procs actually exist, so a dropped one is visible rather than assumed."""
    cur.execute(f"""SELECT PROCEDURE_NAME, LAST_ALTERED FROM {DB}.INFORMATION_SCHEMA.PROCEDURES
                     WHERE PROCEDURE_SCHEMA = 'BRONZE'""")
    return {n: base._fmt_str(a) for n, a in cur.fetchall()}


def _rows():
    """The whole dependency list, curated roles married to live stats."""
    conn = base._conn()
    try:
        cur = conn.cursor()
        stats = _stats(cur, [(s, n) for _p, _st, s, n, _no in PIPE])
        procs = _proc_status(cur)
        views = _published_views(cur)

        # ORPHAN CHECK. Deleting a report drops its view (sf_mis_report._drop_view), but that drop is
        # best-effort on purpose - it must never fail the delete, because the definition is the record.
        # The cost of that choice is that a failed drop leaves a view answering under the name of a
        # report nobody can see the definition of. Nothing else would ever notice, so this page does:
        # any VW_MIS_* whose report is gone or soft-deleted is flagged ORPHAN.
        cur.execute(f"SELECT SLUG FROM {rep.DEF_TBL} WHERE IS_DELETED = FALSE")
        live = {rep._view_name(r[0]).split(".")[-1] for r in cur.fetchall()}
        # Probe only what exists: a MISSING table in the UNION would fail the whole probe.
        fresh = _freshness(cur, set(stats.keys()))

        out = []
        for pipe, stage, sch, name, note in PIPE:
            st = stats.get((sch, name))
            out.append({
                "pipe": pipe, "stage": stage, "schema": sch, "name": name,
                "fqn": f"{DB}.{sch}.{name}", "note": note,
                # MISSING is the point of a curated list: a source that vanished must be loud.
                "status": "OK" if st else "MISSING",
                "type": (st or {}).get("type"),
                "rows": (st or {}).get("rows"),
                "bytes": (st or {}).get("bytes"),
                "lastAltered": (st or {}).get("lastAltered"),
                # The two that answer "is this current?" - see _FRESHNESS.
                "dataThrough": fresh.get((sch, name), {}).get("dataThrough"),
                "lastLoaded": fresh.get((sch, name), {}).get("lastLoaded"),
            })
        for pipe, stage, name, note in PROCS:
            here = name in procs
            out.append({
                "pipe": pipe, "stage": stage, "schema": "BRONZE", "name": name,
                "fqn": f"{DB}.BRONZE.{name}", "note": note,
                "status": "OK" if here else "MISSING", "type": "PROCEDURE",
                "rows": None, "bytes": None, "lastAltered": procs.get(name),
                "dataThrough": None, "lastLoaded": None,
            })
        for v in views:
            # UNMANAGED: shares the prefix, is not ours. Listed so it is visible rather than
            # silently ignored, but it can never be ORPHAN and so can never be dropped.
            if not v["owned"]:
                out.append({
                    "pipe": "REPORTS", "stage": "PUBLISHED VIEW", "schema": v["schema"],
                    "name": v["name"], "fqn": f"{DB}.{v['schema']}.{v['name']}",
                    "note": "NOT from the report builder - it only shares the VW_MIS_ prefix. "
                            "Nothing here manages it and the orphan cleanup will never touch it."
                            + (f" Its own comment says: {v['comment'][:150]}" if v["comment"] else ""),
                    "status": "UNMANAGED", "type": "VIEW", "rows": None, "bytes": None,
                    "lastAltered": v["lastAltered"], "dataThrough": None, "lastLoaded": None,
                })
                continue
            orphan = v["name"] not in live
            out.append({
                "pipe": "REPORTS", "stage": "PUBLISHED VIEW", "schema": v["schema"],
                "name": v["name"], "fqn": f"{DB}.{v['schema']}.{v['name']}",
                "note": ("ORPHAN - the report behind this view no longer exists, so its drop-on-delete "
                         "did not complete. Nothing maintains it and nobody can see its definition; "
                         "it is safe to drop."
                         if orphan else
                         "Auto-published from a saved report. Anchored on the last day of the "
                         "previous month, resolved at read time. Replaced on every save."),
                "status": "ORPHAN" if orphan else "OK",
                "type": "VIEW", "rows": None, "bytes": None,
                "lastAltered": v["lastAltered"], "dataThrough": None, "lastLoaded": None,
            })
        return out
    finally:
        conn.close()


@router.get("/objects")
def lineage_objects():
    rows = _rows()
    missing = [r["fqn"] for r in rows if r["status"] == "MISSING"]
    orphans = [r["fqn"] for r in rows if r["status"] == "ORPHAN"]
    return {
        "rows": rows,
        "pipes": ["SALE", "STOCK", "MASTER", "REPORTS"],
        "counts": {"total": len(rows), "missing": len(missing), "orphans": len(orphans)},
        "missing": missing,
        "orphans": orphans,
    }


@router.post("/drop-orphans")
def lineage_drop_orphans():
    """Drop every VW_MIS_* view whose report is gone.

    Narrow on purpose: it drops ONLY views this module publishes, and only those whose report is
    absent or soft-deleted. It cannot touch a view belonging to a live report, and it cannot touch
    anything outside SILVER.VW_MIS_* - the orphan list is recomputed here rather than taken from the
    caller, so a stale page cannot ask for a drop that is no longer correct.
    """
    rows = _rows()
    orphans = [r for r in rows if r["status"] == "ORPHAN"]
    if not orphans:
        return {"dropped": [], "failed": [], "message": "no orphan views"}

    dropped, failed = [], []
    conn = base._conn()
    try:
        cur = conn.cursor()
        for r in orphans:
            try:
                cur.execute(f"DROP VIEW IF EXISTS {r['fqn']}")
                dropped.append(r["fqn"])
                logger.info("[mis-lineage] dropped orphan view %s", r["fqn"])
            except Exception as e:
                failed.append({"view": r["fqn"], "error": str(e)[:300]})
                logger.warning("[mis-lineage] could not drop orphan %s: %s", r["fqn"], e)
    finally:
        conn.close()
    return {"dropped": dropped, "failed": failed,
            "message": f"dropped {len(dropped)} orphan view(s)"}


@router.get("/export")
def lineage_export():
    rows = _rows()
    cols = ["pipe", "stage", "schema", "name", "fqn", "type", "status", "rows", "bytes",
            "dataThrough", "lastLoaded", "lastAltered", "note"]
    buf = io.StringIO()
    buf.write(",".join(cols) + "\n")
    for r in rows:
        vals = []
        for c in cols:
            v = r.get(c)
            v = "" if v is None else str(v)
            # The notes carry commas and the odd quote; quote every field and double the quotes.
            vals.append('"' + v.replace('"', '""') + '"')
        buf.write(",".join(vals) + "\n")
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="MIS_DEPENDENCIES.csv"'})
