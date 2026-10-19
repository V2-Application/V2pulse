"""Data inventory: every table in the raw/ingest schemas and HOW it gets updated.

REPORT_PORTAL.DATA_INVENTORY is rebuilt 5x a day by task T_PORTAL_DATA_INVENTORY (owned by
ACCOUNTADMIN, so the portal role never needs ACCOUNT_USAGE). It holds, per table, the last 30
days of writers from ACCESS_HISTORY: who wrote, how often, which statement types, the commonest
QUERY_TAG and a sample statement. This module turns those facts into a plain-language channel
("SAP OData sync", "File upload", "Axapta CRM sync"...) using, in order:
  1. the BRONZE.SYNC_TABLES registry (its SERVICE column names the loader),
  2. the QUERY_TAG (DFS app, HRMS sync, upload app, MPOS),
  3. the writing user, refined by the statement shape (COPY from a table stage = file upload,
     MERGE from *_INCR_STG = SAP incremental, write_pandas = Python loader).
The mapping was built from the actual writers on 2026-10-07.
"""
import json, re
from datetime import datetime, timedelta

import db

SQL = "select * from V2RETAIL.REPORT_PORTAL.DATA_INVENTORY"
REG_SQL = "select TABLE_NAME, TARGET, SERVICE, MODE, STATUS from V2RETAIL.BRONZE.SYNC_TABLES"

SAP_MASTERS = {"MARA", "MAKT", "MARC", "MBEW", "MVKE", "LFA1", "LFB1", "LFBK", "LFBW", "LFM1", "KNA1", "KNVV", "ADRC", "ADR2",
               "ADR6", "ADRP", "BNKA", "BUT000", "CSKS", "CSKT", "SKAT", "CABN", "CAWN", "WRF1", "LAGP", "PROJ", "MARV", "T001",
               "T001B", "T001L", "T001W", "T005T", "T005U", "T023T", "T141T", "T16FS", "T300", "T301", "T301T", "ZMC_MASTER",
               "ZCOLOR_MASTER", "ZGRID_MASTER", "ZCLA_PALETTE", "ZART_MC", "ZART_CHAR_MATNR", "MSKU"}
STAGING = re.compile(r"(_STG|_STAGE|__STAGE|_BAK|_BACKUP|_OLD|_TMP|_TEMP|_ARCHIVE|^STG_|^TMP_|^_ARCHIVE|_\d{8}$|_\d{6}$|_V\d+$|_TEST$|_QA$|__BIG$|_INCR_STG$)")
USER_CHANNEL = {
    "SAP_SYNC_SVC": "SAP sync (.24 server)", "HRMS_SYNC_SVC": "HRMS sync (SQL Server)", "DHYANU_SVC": "Axapta CRM sync",
    "ARS_REPLICATE_SVC": "ARS replicate (HOPC622)", "UNIVERSAL_MCP_SVC": "MCP API (Cloudflare worker)", "SYSTEM": "Snowflake task",
    "AMJAD": "Karma / Retail Hub app sync", "PROGRAMMER_SVC": "Python script (service)", "SHUBHAM_SVC": "Python script (service)",
    "REPORT_PORTAL_SVC": "Report portal", "LOVABLE_RO": "Supabase push (Lovable)", "STOCK_API_SVC": "Stock API",
}
HUMANS_NOTE = "Manual / script"


def _naive(v):
    if isinstance(v, datetime):
        return v.replace(tzinfo=None) if v.tzinfo is None else v.astimezone().replace(tzinfo=None)
    return None


def _iso(v):
    return v.isoformat(timespec="seconds") if isinstance(v, datetime) else v


def _j(v):
    if v is None:
        return None
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return None


def category(schema, name):
    n = name.upper()
    if STAGING.search(n):
        return "Staging / backup"
    if n in SAP_MASTERS or "MASTER" in n or n.endswith("_MAST") or re.fullmatch(r"T\d{3}[A-Z]?", n):
        return "Master data"
    if n.startswith(("HRMS_",)) and n not in ("HRMS_ATTENDANCE", "HRMS_PAYROLL_TRAN", "HRMS_SYNC_LOG", "HRMS_MONTHLY_PUNCHES_RANGE"):
        return "Master data"
    if n.endswith(("_LOG", "_AUDIT", "_ALERTS", "_HISTORY_LOG")) or n.startswith(("SYNC_", "_HEALTH")):
        return "Logs / control"
    if n.endswith(("_DEF", "_DEF_HIST", "_CONFIG", "_SETTINGS", "_PARAMS", "_SAVED_VIEWS", "_DATASETS")) or n.startswith(("UPL_META_", "APP_")):
        return "Config / app data"
    return "Transaction / other"


def channel(row, reg):
    """Return (channel, method, evidence)."""
    name = row["table_name"].upper()
    tag = (row.get("top_query_tag") or "")
    user = row.get("top_user") or ""
    sql = (row.get("sample_sql") or "").upper()
    qtypes = set(_j(row.get("query_types")) or [])
    method = ("Python write_pandas" if "WRITE_PANDAS" in sql else
              "File upload (CSV / stage COPY)" if "COPY" in qtypes and ("@%" in sql or " FROM @" in sql or "FROM (SELECT $1" in sql) else
              "Bulk COPY" if "COPY" in qtypes else
              "Incremental MERGE" if "MERGE" in qtypes and "_INCR_STG" in sql else
              "JSON MERGE (app/API payload)" if "MERGE" in qtypes and "F.VALUE" in sql else
              "MERGE / upsert" if "MERGE" in qtypes else
              "Truncate + reload" if "TRUNCATE_TABLE" in qtypes else
              "Rebuild (CTAS / clone)" if qtypes & {"CREATE_TABLE_AS_SELECT", "CREATE_TABLE"} and not qtypes & {"INSERT", "MERGE", "COPY"} else
              "Insert / delete" if qtypes else "—")
    r = reg.get(name)
    if r and r.get("status") != "INACTIVE":
        svc = (r.get("service") or "")
        s = svc.lower()
        ch = ("SAP RFC API (.36)" if "rfc-api" in s else
              "SAP OData sync (.24)" if s.startswith("z_sb") or "odata" in s else
              "HRMS sync (SQL Server)" if "hrms" in s else
              "ARS push" if "ars_push" in s else
              "SAP RFC (MCP read)" if "rfc_read_table" in s else
              "SAP HANA CSV" if "hana" in s else
              "SAP sync (.24 server)" if ".24" in s else "Registered sync")
        return ch, method, "BRONZE.SYNC_TABLES: %s" % svc
    if tag:
        t = tag.lower()
        if '"app":"df"' in t.replace(" ", ""):
            ep = (_j(tag) or {}).get("ep") or ""
            return "DFS app", method, "query tag: DFS %s" % ep
        if "hrms_sync" in t:
            return "HRMS sync (SQL Server)", method, "query tag: hrms_sync"
        if "data_uploader" in t:
            return "Upload app (DATA_UPLOADER)", method, "query tag: DATA_UPLOADER"
        if "mpos" in t:
            return "MPOS app API", method, "query tag: %s" % tag[:40]
    if user == "AKASHV2KART":
        if "_INCR_STG" in sql or name.endswith("_OD"):
            return "SAP OData sync (.24)", method, "MERGE from *_INCR_STG (Akash pipeline)"
        if name.endswith("_RFC") or name.startswith("ET_"):
            return "SAP RFC (Akash pipeline)", method, "RFC-named table loaded by AKASHV2KART"
        if "File upload" in method:
            return "File upload", method, "COPY from a table stage by AKASHV2KART"
        if row["table_schema"] == "AKS_BRONZE":
            return "AKS planning loads", method, "AKS_BRONZE written by AKASHV2KART"
        return "Akash pipelines", method, "written by AKASHV2KART, no tag"
    if user in USER_CHANNEL:
        ch = USER_CHANNEL[user]
        if user == "SAP_SYNC_SVC" and "File upload" in method:
            ch = "SAP sync (.24 server)"
        return ch, method, "written by %s" % user
    if user:
        return "%s (%s)" % (HUMANS_NOTE, user), method, "written by %s" % user
    return "Not updated in 30 days", "—", "no writes in ACCESS_HISTORY for 30 days"


def build(rows, reg, now):
    out = []
    for r in rows:
        last_write = _naive(r.get("last_write_ist"))
        last_alt = _naive(r.get("last_altered_ist"))
        n = r.get("writes_30d") or 0
        days = r.get("write_days_30d") or 0
        cat = category(r["table_schema"], r["table_name"])
        ch, method, ev = channel(r, reg)
        age_h = (now - last_write).total_seconds() / 3600 if last_write else None
        if not n:
            status, reason = "DORMANT", "No writes in the last 30 days"
        elif days >= 15 and cat not in ("Config / app data", "Staging / backup") and age_h is not None and age_h > 30:
            status, reason = "LATE", "Written on %d of the last 30 days, but nothing for %s" % (
                days, "%.0f h" % age_h if age_h < 72 else "%.1f days" % (age_h / 24))
        elif age_h is not None and age_h <= 26:
            status, reason = "OK", "Written %.1f h ago" % age_h
        elif age_h is not None and age_h <= 24 * 7:
            status, reason = "RECENT", "Written %.0f days ago" % (age_h / 24)
        else:
            status, reason = "STALE", "Last written %.0f days ago" % ((age_h or 0) / 24)
        writers = _j(r.get("writers")) or []
        out.append({
            "schema": r["table_schema"], "table": r["table_name"], "rows": r.get("row_count"), "bytes": r.get("bytes"),
            "created": _iso(_naive(r.get("created_ist"))), "last_altered": _iso(last_alt), "comment": r.get("table_comment") or "",
            "writes_30d": n, "write_days_30d": days, "last_write": _iso(last_write), "writers": [{"user": w.get("user"), "writes": w.get("writes"),
                                                                         "last": w.get("last")} for w in writers[:6]],
            "query_types": _j(r.get("query_types")) or [], "top_user": r.get("top_user"), "tag": r.get("top_query_tag"),
            "warehouse": r.get("top_warehouse"), "sample_sql": r.get("sample_sql"),
            "channel": ch, "method": method, "evidence": ev, "category": cat,
            "status": status, "reason": reason,
        })
    sev = {"LATE": 5, "STALE": 3, "RECENT": 2, "OK": 1, "DORMANT": 0}
    out.sort(key=lambda x: (x["category"] == "Staging / backup", -sev[x["status"]], x["schema"], x["table"]))
    refreshed = max((_naive(r.get("refreshed_at_ist")) for r in rows if r.get("refreshed_at_ist")), default=None)
    return out, _iso(refreshed)


def fetch(conn, now):
    try:
        rows = db.run(SQL, conn=conn)
    except Exception as e:
        return {"tables": [], "refreshed": None,
                "error": "Cannot read REPORT_PORTAL.DATA_INVENTORY (%s). It is built by task T_PORTAL_DATA_INVENTORY." % str(e)[:150]}
    reg = {}
    try:
        for g in db.run(REG_SQL, conn=conn):
            for k in {(g["table_name"] or "").upper(), (g["target"] or "").upper()}:
                if k and k not in reg:
                    reg[k] = {"service": g["service"], "mode": g["mode"], "status": (g["status"] or "").upper()}
    except Exception:
        pass
    tables, refreshed = build(rows, reg, now)
    return {"tables": tables, "refreshed": refreshed, "error": None if rows else
            "The inventory is empty; task T_PORTAL_DATA_INVENTORY has not finished its first run yet."}
