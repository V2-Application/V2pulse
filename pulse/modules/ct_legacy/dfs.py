"""DFS app monitor: the reports it publishes and the jobs it runs, read from Snowflake.

DFS is the internal web app whose Snowflake queries carry QUERY_TAG {"app":"df", ...} (runs as
AKASHV2KART). In Snowflake it leaves two kinds of footprint:

Reports - saved definitions, some auto-published as SILVER views ("Auto-published from ... slug"):
  BRONZE.MIS_REPORT_DEF            MIS report builder     -> SILVER.VW_MIS_<SLUG>
  BRONZE.MIS_INV_REPORT_DEF        Inventory reports      -> SILVER.VW_INV_<SLUG>
  BRONZE.MIS_DAY_TREND_REPORT_DEF  Day-trend reports      (no published view)
  BRONZE.SCM_REPORT_DEF            SCM report views       -> VIEW_NAME column
A published view is only as fresh as its source feed, so each family is tied to the feed it reads.

Jobs - DFS writes one row per background job and updates it as it progresses:
  AKS_OPS.AKS_COMPUTE_JOBS    transfer-in / planning computes     (IST)
  AKS_OPS.PLAN_JOB            sale-plan month saves               (US Pacific)
  AKS_OPS.SP_SAVE_JOB         space-plan saves                    (IST)
  AKS_OPS.CO_OPT_PR_RUN_LOG   CO option PR runs                   (timestamp with zone)
  GOLD.RFC_SYNC_JOB / RFC_SYNC_LOG   the DFS RFC scheduler        (US Pacific)

Time zones were measured 2026-10-07 by comparing each table's newest timestamp with Snowflake's
own LAST_ALTERED. Everything is converted to IST in SQL, so DST in Los Angeles is handled.
"""
from collections import defaultdict
from datetime import datetime, timedelta

import db

LA = "'America/Los_Angeles'"
IST = "'Asia/Kolkata'"
la = lambda col: "convert_timezone(%s, %s, %s)::timestamp_ntz" % (LA, IST, col)
STUCK_AFTER_H = 2

REPORT_SQL = {
    "MIS": "select REPORT_ID, SLUG, NAME, DESCRIPTION, CREATED_BY, %s CREATED, UPDATED_BY, %s UPDATED, IS_SHARED, SHOW_IN_SIDEBAR "
           "from V2RETAIL.BRONZE.MIS_REPORT_DEF where not coalesce(IS_DELETED, false)" % (la("CREATED_AT"), la("UPDATED_AT")),
    "INVENTORY": "select REPORT_ID, SLUG, NAME, DESCRIPTION, CREATED_BY, CREATED_AT CREATED, UPDATED_BY, UPDATED_AT UPDATED, IS_SHARED, SHOW_IN_SIDEBAR "
                 "from V2RETAIL.BRONZE.MIS_INV_REPORT_DEF where not coalesce(IS_DELETED, false)",
    "DAY_TREND": "select REPORT_ID, SLUG, NAME, DESCRIPTION, CREATED_BY, CREATED_AT CREATED, UPDATED_BY, UPDATED_AT UPDATED, IS_SHARED, SHOW_IN_SIDEBAR "
                 "from V2RETAIL.BRONZE.MIS_DAY_TREND_REPORT_DEF where not coalesce(IS_DELETED, false)",
    "SCM": "select SLUG REPORT_ID, SLUG, SLUG NAME, ORIGIN DESCRIPTION, CREATED_BY, %s CREATED, UPDATED_BY, %s UPDATED, "
           "true IS_SHARED, true SHOW_IN_SIDEBAR, VIEW_NAME from V2RETAIL.BRONZE.SCM_REPORT_DEF where not coalesce(IS_DELETED, false)"
           % (la("CREATED_AT"), la("UPDATED_AT")),
}
FAMILY = {
    "MIS": {"name": "MIS report builder", "view": "V2RETAIL.SILVER.VW_MIS_%s",
            "sources": ["V2RETAIL.BRONZE.DAY_WISE_ST_VAR_SALE", "V2RETAIL.BRONZE.DAY_WISE_ST_VAR_STOCK"], "max_age_h": 30,
            "note": "Views anchor on the last day of the previous month and read the MIS day-wise sale/stock feed."},
    "INVENTORY": {"name": "Inventory reports", "view": "V2RETAIL.SILVER.VW_INV_%s",
                  "sources": ["V2RETAIL.BRONZE.MIS_VERT_INV_ART_LOG"], "max_age_h": 30,
                  "note": "Views always serve the latest AS_OF_DATE in BRONZE.MIS_VERT_INV_ART_LOG."},
    "DAY_TREND": {"name": "Day-trend reports", "view": None,
                  "sources": ["V2RETAIL.BRONZE.DAY_WISE_ST_VAR_SALE"], "max_age_h": 30,
                  "note": "Run on demand inside DFS; no published view."},
    "SCM": {"name": "SCM report views", "view": "SCM", "sources": [], "max_age_h": None,
            "note": "User-added SCM views; the SQL is stored with the definition."},
}

JOB_SQL = {
    "AKS compute": "select JOB_ID::string ID, JOB_TYPE KIND, STATUS, STAGE PHASE, PCT, ROWS_DONE DONE_ROWS, TOTAL_ROWS, ERROR_MSG MSG, "
                   "null BY_USER, STARTED_AT STARTED, UPDATED_AT UPDATED, null DETAIL from V2RETAIL.AKS_OPS.AKS_COMPUTE_JOBS "
                   "where STARTED_AT > dateadd('day', -14, current_timestamp()::timestamp_ntz)",
    "Sale plan save": "select JOB_ID::string ID, 'PLAN_SAVE' KIND, STATUS, PHASE, iff(TOTAL_ROWS > 0, round(100 * SAVED_ROWS / TOTAL_ROWS), null) PCT, "
                      "SAVED_ROWS DONE_ROWS, TOTAL_ROWS, MESSAGE MSG, CREATED_BY BY_USER, %s STARTED, %s UPDATED, null DETAIL "
                      "from V2RETAIL.AKS_OPS.PLAN_JOB where CREATED_AT > dateadd('day', -14, current_timestamp()::timestamp_ntz)"
                      % (la("CREATED_AT"), la("UPDATED_AT")),
    "Space plan save": "select ID::string ID, 'SPACE_PLAN_SAVE' KIND, STATUS, PHASE, iff(TOTAL_ROWS > 0, round(100 * SAVED_ROWS / TOTAL_ROWS), null) PCT, "
                       "SAVED_ROWS DONE_ROWS, TOTAL_ROWS, MESSAGE MSG, SAVED_BY BY_USER, CREATED_AT STARTED, UPDATED_AT UPDATED, "
                       "PLAN_MONTH || coalesce(' -> ' || TABLE_NAME, '') DETAIL from V2RETAIL.AKS_OPS.SP_SAVE_JOB "
                       "where CREATED_AT > dateadd('day', -14, current_timestamp()::timestamp_ntz)",
    "CO option PR": "select RUN_TOKEN ID, SHAPE KIND, STATUS, null PHASE, null PCT, STORED_ROWS DONE_ROWS, ROW_COUNT TOTAL_ROWS, ERROR_TEXT MSG, "
                    "RUN_BY BY_USER, convert_timezone(%s, STARTED_AT)::timestamp_ntz STARTED, "
                    "convert_timezone(%s, coalesce(ENDED_AT, STARTED_AT))::timestamp_ntz UPDATED, HOST DETAIL "
                    "from V2RETAIL.AKS_OPS.CO_OPT_PR_RUN_LOG where STARTED_AT > dateadd('day', -14, current_timestamp())" % (IST, IST),
}
RFC_JOBS_SQL = ("select ID, RFC_CODE, INTERVAL_MINUTES, ENV, IS_ACTIVE, %s LAST_RUN, %s NEXT_RUN, %s UPDATED "
                "from V2RETAIL.GOLD.RFC_SYNC_JOB" % (la("LAST_RUN_DT"), la("NEXT_RUN_DT"), la("UPDATED_DT")))
RFC_LOG_SQL = ("select JOB_ID, RFC_CODE, STATUS, %s STARTED, %s COMPLETED, DURATION_SEC, ROWS_FETCHED, ROWS_WRITTEN, "
               "left(ERROR_MESSAGE, 600) ERROR_MESSAGE, TRIGGERED_BY from V2RETAIL.GOLD.RFC_SYNC_LOG "
               "where STARTED_DT > dateadd('day', -30, current_timestamp()::timestamp_ntz)" % (la("STARTED_DT"), la("COMPLETED_DT")))

GRANT = "grant select on table %s to role REPORT_PORTAL_SVC_ROLE;"
TABLE_OF = {"MIS": "V2RETAIL.BRONZE.MIS_REPORT_DEF", "INVENTORY": "V2RETAIL.BRONZE.MIS_INV_REPORT_DEF",
            "DAY_TREND": "V2RETAIL.BRONZE.MIS_DAY_TREND_REPORT_DEF", "SCM": "V2RETAIL.BRONZE.SCM_REPORT_DEF",
            "AKS compute": "V2RETAIL.AKS_OPS.AKS_COMPUTE_JOBS", "Sale plan save": "V2RETAIL.AKS_OPS.PLAN_JOB",
            "Space plan save": "V2RETAIL.AKS_OPS.SP_SAVE_JOB", "CO option PR": "V2RETAIL.AKS_OPS.CO_OPT_PR_RUN_LOG",
            "rfc_jobs": "V2RETAIL.GOLD.RFC_SYNC_JOB", "rfc_log": "V2RETAIL.GOLD.RFC_SYNC_LOG"}


def _iso(v):
    return v.isoformat(timespec="seconds") if isinstance(v, datetime) else v


def _naive(v):
    if isinstance(v, datetime):
        return v.replace(tzinfo=None) if v.tzinfo is None else v.astimezone().replace(tzinfo=None)
    return None


def norm_status(s, updated, now):
    s = (s or "").strip().lower()
    if s in ("done", "completed", "complete", "success", "succeeded", "ok"):
        return "SUCCEEDED"
    if s in ("failed", "error", "errored", "failure"):
        return "FAILED"
    if s in ("cancelled", "canceled", "aborted"):
        return "CANCELLED"
    if s in ("running", "queued", "pending", "started", "in_progress", "computing", "saving"):
        if updated and updated < now - timedelta(hours=STUCK_AFTER_H):
            return "STUCK"
        return "RUNNING"
    return s.upper() or "UNKNOWN"


def fetch(conn, now):
    errors = []

    def q(key, sql):
        try:
            return db.run(sql, conn=conn)
        except Exception:
            errors.append(GRANT % TABLE_OF[key])
            return []

    # ---- reports
    reports = []
    for fam, sql in REPORT_SQL.items():
        for r in q(fam, sql):
            slug = r["slug"] or ""
            f = FAMILY[fam]
            view = (r.get("view_name") if fam == "SCM" else
                    (f["view"] % slug.upper().replace("-", "_")) if f["view"] else None)
            if view and view.count(".") == 1:
                view = "V2RETAIL." + view
            reports.append({"family": fam, "family_name": f["name"], "id": r["report_id"], "slug": slug,
                            "name": r["name"] or slug, "description": r["description"] or "",
                            "created_by": r["created_by"], "created": _iso(_naive(r["created"])),
                            "updated_by": r["updated_by"], "updated": _iso(_naive(r["updated"])),
                            "shared": bool(r["is_shared"]), "sidebar": bool(r["show_in_sidebar"]),
                            "view": view.upper() if view else None})
    views = {}
    try:
        for v in db.run("select table_catalog||'.'||table_schema||'.'||table_name fqn, last_altered from V2RETAIL.information_schema.views "
                        "where table_schema in ('SILVER', 'GOLD', 'MIS_REPORTS')", conn=conn):
            views[v["fqn"]] = _naive(v["last_altered"])
    except Exception:
        pass
    src_names = sorted({s for f in FAMILY.values() for s in f["sources"]})
    sources = {}
    if src_names:
        ph = ",".join(["%s"] * len(src_names))
        try:
            for m in db.run("select table_catalog||'.'||table_schema||'.'||table_name fqn, last_altered, row_count "
                            "from V2RETAIL.information_schema.tables where table_catalog||'.'||table_schema||'.'||table_name in (%s)" % ph,
                            tuple(src_names), conn=conn):
                sources[m["fqn"]] = {"last_altered": _naive(m["last_altered"]), "rows": m["row_count"]}
        except Exception:
            pass
    fam_out = {}
    for fam, f in FAMILY.items():
        srcs = [{"table": s, "last_altered": _iso(sources.get(s, {}).get("last_altered")), "rows": sources.get(s, {}).get("rows")} for s in f["sources"]]
        olds = [sources[s]["last_altered"] for s in f["sources"] if s in sources and sources[s]["last_altered"]]
        oldest = min(olds) if olds else None
        age = (now - oldest).total_seconds() / 3600 if oldest else None
        fam_out[fam] = {"name": f["name"], "note": f["note"], "sources": srcs, "source_age_h": round(age, 1) if age is not None else None,
                        "max_age_h": f["max_age_h"], "stale": bool(age is not None and f["max_age_h"] and age > f["max_age_h"]),
                        "count": sum(1 for r in reports if r["family"] == fam)}
    by_name = defaultdict(list)
    for fq in views:
        by_name[fq.split(".")[-1]].append(fq)
    for r in reports:
        if r["view"] and "." not in r["view"] and len(by_name.get(r["view"], [])) == 1:
            r["view"] = by_name[r["view"]][0]
    for r in reports:
        fm = fam_out[r["family"]]
        if r["view"]:
            pub = views.get(r["view"])
            r["view_exists"] = r["view"] in views if views else None
            r["view_published"] = _iso(pub)
        else:
            r["view_exists"] = None
            r["view_published"] = None
        if r["view"] and views and not r["view_exists"]:
            r["status"], r["reason"] = "WARN", "Published view %s not found" % r["view"]
        elif fm["stale"]:
            r["status"], r["reason"] = "STALE", "Source feed last changed %.1f h ago (limit %d h)" % (fm["source_age_h"], fm["max_age_h"])
        elif fm["source_age_h"] is not None:
            r["status"], r["reason"] = "OK", "Source feed changed %.1f h ago" % fm["source_age_h"]
        else:
            r["status"], r["reason"] = "UNMONITORED", "On-demand report; nothing scheduled to check"
    reports.sort(key=lambda r: (r["family"], r["name"].lower()))

    # ---- jobs
    jobs = []
    for src, sql in JOB_SQL.items():
        for j in q(src, sql):
            st, up = _naive(j["started"]), _naive(j["updated"])
            status = norm_status(j["status"], up or st, now)
            jobs.append({"source": src, "id": j["id"], "kind": j["kind"], "raw_status": j["status"], "status": status,
                         "phase": j["phase"], "pct": float(j["pct"]) if j["pct"] is not None else None,
                         "done_rows": j["done_rows"], "total_rows": j["total_rows"], "message": j["msg"],
                         "by": j["by_user"], "started": _iso(st), "updated": _iso(up), "detail": j["detail"],
                         "duration_s": round((up - st).total_seconds()) if up and st and status != "RUNNING" else None})
    jobs.sort(key=lambda x: x["started"] or "", reverse=True)

    # ---- RFC scheduler
    rfc_jobs = q("rfc_jobs", RFC_JOBS_SQL)
    # The DFS RFC scheduler stopped closing its log rows (every run since 27-Sep stays 'Running'),
    # while the same LOAD_SAP_<T> loads keep landing daily via the rfc-api lane on .36. So the
    # truth is the BRONZE table each job loads: LOAD_SAP_ADRC -> BRONZE.ADRC.
    tgt = {}
    names = sorted({(j["rfc_code"] or "").upper().replace("LOAD_SAP_", "", 1) for j in rfc_jobs if j["rfc_code"]})
    if names:
        try:
            ph = ",".join(["%s"] * len(names))
            for m in db.run("select TABLE_NAME, LAST_ALTERED, ROW_COUNT from V2RETAIL.information_schema.tables "
                            "where TABLE_SCHEMA = 'BRONZE' and TABLE_NAME in (%s)" % ph, tuple(names), conn=conn):
                tgt[m["table_name"]] = {"last_altered": _naive(m["last_altered"]), "rows": m["row_count"]}
        except Exception:
            pass
    rfc_log = q("rfc_log", RFC_LOG_SQL)
    runs_by = defaultdict(list)
    for r in rfc_log:
        st, co = _naive(r["started"]), _naive(r["completed"])
        runs_by[r["job_id"]].append({"status": norm_status(r["status"], st if not co else co, now) if (r["status"] or "").lower() == "running" else norm_status(r["status"], co, now),
                                     "raw_status": r["status"], "started": _iso(st), "completed": _iso(co),
                                     "duration_s": r["duration_sec"], "rows": r["rows_written"], "fetched": r["rows_fetched"],
                                     "error": r["error_message"], "by": r["triggered_by"]})
    rfc = []
    for j in rfc_jobs:
        runs = sorted(runs_by.get(j["id"], []), key=lambda x: x["started"] or "", reverse=True)
        last = runs[0] if runs else None
        ok = next((x for x in runs if x["status"] == "SUCCEEDED"), None)
        nxt = _naive(j["next_run"])
        table = (j["rfc_code"] or "").upper().replace("LOAD_SAP_", "", 1)
        t = tgt.get(table, {})
        changed = t.get("last_altered")
        limit_h = (j["interval_minutes"] or 1440) / 60 * 1.25 + 6
        age_h = (now - changed).total_seconds() / 3600 if changed else None
        log_note = ""
        if last and last["status"] == "STUCK":
            log_note = " The DFS scheduler log still shows its %s run as Running (it is not closing its log rows)." % (last["started"] or "")[:10]
        if not j["is_active"]:
            status, reason = "SUSPENDED", "Inactive in GOLD.RFC_SYNC_JOB"
        elif age_h is not None and age_h <= limit_h:
            status, reason = "SUCCEEDED", "BRONZE.%s updated %s (%.1f h ago).%s" % (table, changed.strftime("%d %b %H:%M"), age_h, log_note)
        elif age_h is not None:
            status = last["status"] if last and last["status"] in ("STUCK", "FAILED") else "LATE"
            reason = "BRONZE.%s last changed %.1f h ago; expected every %s h.%s" % (table, age_h, round(limit_h - 6), log_note)
        elif last and last["status"] in ("STUCK", "FAILED"):
            status, reason = last["status"], ("Last run %s at %s" % (last["raw_status"], (last["started"] or "")[:16].replace("T", " "))
                                              + (": " + last["error"][:160] if last.get("error") else ""))
        elif last:
            status, reason = last["status"], ""
        else:
            status, reason = "UNMONITORED", "No runs logged in 30 days and no BRONZE.%s table found" % table
        rfc.append({"id": j["id"], "rfc": j["rfc_code"], "interval_min": j["interval_minutes"], "env": j["env"], "active": bool(j["is_active"]),
                    "last_run": _iso(_naive(j["last_run"])), "next_run": _iso(nxt), "status": status, "reason": reason,
                    "last_ok": ok["completed"] if ok else None, "runs": runs[:15], "runs_30d": len(runs),
                    "stuck_30d": sum(1 for x in runs if x["status"] == "STUCK"), "table": table,
                    "table_changed": _iso(changed), "table_rows": t.get("rows")})
    sev = {"FAILED": 6, "STUCK": 5, "LATE": 4, "RUNNING": 2, "SUCCEEDED": 1, "SUSPENDED": 0, "UNMONITORED": 0}
    rfc.sort(key=lambda x: (-sev.get(x["status"], 1), x["rfc"] or ""))
    return {"reports": reports, "families": fam_out, "jobs": jobs, "rfc": rfc,
            "error": ("Some DFS tables need a grant (run as ACCOUNTADMIN): " + " ".join(sorted(set(errors)))) if errors else None}
