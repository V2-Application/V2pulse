"""SAP -> BRONZE load monitor, read from V2RETAIL.BRONZE.SYNC_LOG.

Every SAP load writes one row per table run to BRONZE.SYNC_LOG. The main lane is
"dot24-cron": user SAP_SYNC_SVC on the .24 server, ~47 tables, the *_HR tables every ~45 min
and the rest daily. Its STARTED_AT/ENDED_AT are UTC (verified 2026-10-07: a row stamped 08:06
was inserted at 13:40 IST), as are rfc-api and the ars lanes; only "sf-task" stamps IST.

"Loaded or not" is judged per table against its own rhythm. A table is LATE when its last
successful load is older than the LONGEST gap between its loads in the last 7 days (+25%, +30
min). The median gap is wrong for this: BSIS and VBFA load in bursts (several runs minutes
apart, then a long pause), so their median is ~20 min and they would look late every night.
The longest normal gap holds a daily table, a 45-minute table and a bursty table each to
their own pattern.
"""
from collections import defaultdict
from datetime import datetime, timedelta

import db

IST_LANES = {"sf-task"}
MAIN_LANE = "dot24-cron"
# The HRMS sync (hrms_sync.py on DataCenter .27) writes one SYNC_LOG row per feed group, but its
# TRIGGERED_BY varies with how it was started (schedule name, manual-first-load, dashboard:<user>,
# a backfill label). Group those rows by feed into one lane instead. Its times are UTC (sysdate()).
HRMS_GROUPS = {"HRMS_MASTERS", "HRMS_ATTENDANCE", "HRMS_PAYROLL_TRAN"}
HRMS_LANE = "hrms-sync"
# Daily jobs whose own history cannot define "normal": the HRMS sync was dead 30 Sep - 7 Oct, so
# its longest recent gap is a week and the gap rule would never call it late.
FIXED_LATE_MIN = {HRMS_LANE: 26 * 60}
LANE_NAMES = {"dot24-cron": "SAP sync (.24 server)", HRMS_LANE: "HRMS sync (.27 server)", "rfc-api-batchc": "SAP RFC API", "sf-task": "Snowflake tasks",
              "ars-push": "ARS push", "ars-gate": "ARS gate", "hopc622-mc-recon": "MC reconciliation",
              "periodic-discount-sync": "Periodic discount sync", "manual-first-load": "Manual loads"}
SEV = {"FAILED": 6, "STUCK": 5, "LATE": 4, "PARTIAL": 3, "RUNNING": 2, "OK": 1, "RETIRED": 0}
SQL = """
select TRIGGERED_BY, TABLE_NAME, MODE, STATUS, STARTED_AT, ENDED_AT, DURATION_S, ROWS_PULLED, ROWS_INSERTED,
       ROWS_UPDATED, TARGET_COUNT, left(ERROR, 1500) ERROR, RUN_ID
from V2RETAIL.BRONZE.SYNC_LOG
where STARTED_AT > dateadd('day', -30, convert_timezone('UTC', current_timestamp())::timestamp_ntz)
"""


def _naive(v):
    if isinstance(v, datetime):
        return v.replace(tzinfo=None) if v.tzinfo is None else v.astimezone().replace(tzinfo=None)
    return None


def _iso(v):
    return v.isoformat(timespec="seconds") if isinstance(v, datetime) else v


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def fmt_dur(sec):
    sec = abs(sec)
    if sec < 3600:
        return "%d min" % round(sec / 60)
    if sec < 86400 * 2:
        return "%.1f h" % (sec / 3600)
    return "%d days" % round(sec / 86400)


def build(rows, meta, now):
    """rows from SQL (NTZ values as stored); meta = {TABLE: row} for BRONZE; now = naive IST."""
    shift = timedelta(hours=5, minutes=30)
    by = defaultdict(list)
    for r in rows:
        lane = r["triggered_by"] or "unknown"
        if (r["table_name"] or "").upper() in HRMS_GROUPS:
            lane = HRMS_LANE
        st, en = _naive(r["started_at"]), _naive(r["ended_at"])
        if lane not in IST_LANES:
            st, en = (st + shift if st else st), (en + shift if en else en)
        by[(lane, r["table_name"] or "?")].append({
            "status": (r["status"] or "").upper(), "mode": r["mode"], "started": st, "ended": en,
            "duration_s": float(r["duration_s"]) if r["duration_s"] is not None else None,
            "pulled": r["rows_pulled"], "inserted": r["rows_inserted"], "updated": r["rows_updated"],
            "target": r["target_count"], "error": r["error"], "run_id": r["run_id"]})
    days = [(now - timedelta(days=i)).date().isoformat() for i in range(6, -1, -1)]
    daily = defaultdict(lambda: {d: {"ok": 0, "bad": 0, "rows": 0} for d in days})
    tables = []
    for (lane, tbl), runs in by.items():
        runs.sort(key=lambda x: x["started"] or datetime.min, reverse=True)
        last = runs[0]
        succ = [x for x in runs if x["status"] == "SUCCESS"]
        week = [x for x in runs if x["started"] and x["started"] > now - timedelta(days=7)]
        starts = sorted(x["started"] for x in week)
        gaps = [(b - a).total_seconds() / 60 for a, b in zip(starts, starts[1:]) if (b - a).total_seconds() > 300]
        cadence = _median(gaps)
        late_after = FIXED_LATE_MIN.get(lane) or (max(gaps) * 1.25 + 30 if gaps else None)
        med_dur = _median([x["duration_s"] for x in succ[:20] if x["duration_s"]])
        last_ok = (succ[0]["ended"] or succ[0]["started"]) if succ else None

        status, reason = "OK", "Loaded as expected"
        if last["started"] and last["started"] < now - timedelta(days=7) and lane not in FIXED_LATE_MIN:
            status, reason = "RETIRED", "No load for %d days" % (now - last["started"]).days
        elif last["status"] == "RUNNING":
            age = (now - last["started"]).total_seconds() if last["started"] else 0
            if age > max(7200, 3 * (med_dur or 0)):
                status, reason = "STUCK", "Marked RUNNING for %s with no end recorded" % fmt_dur(age)
            else:
                status, reason = "RUNNING", "Loading now (started %s IST)" % last["started"].strftime("%H:%M")
        elif last["status"] in ("FAILED", "ERROR"):
            status, reason = "FAILED", "Last load failed: %s" % (last["error"] or "no error text")[:240]
        elif last["status"] == "PARTIAL":
            status, reason = "PARTIAL", "Last load was partial: %s" % (last["error"] or "no detail")[:240]
        if status in ("OK", "RUNNING") and late_after and last_ok:
            behind = (now - last_ok).total_seconds() / 60
            if behind > late_after:
                status, reason = "LATE", ("No successful load for %s; it is expected daily" % fmt_dur(behind * 60)
                                          if lane in FIXED_LATE_MIN else
                                          "No successful load for %s; its longest normal gap this week was %s" % (
                                              fmt_dur(behind * 60), fmt_dur(max(gaps) * 60)))

        for x in week:
            if x["status"] == "RUNNING":
                continue
            d = daily[lane][x["started"].date().isoformat()] if x["started"].date().isoformat() in days else None
            if d is not None:
                d["ok" if x["status"] == "SUCCESS" else "bad"] += 1
                d["rows"] += (x["inserted"] or 0) + (x["updated"] or 0)
        n7 = [x for x in week if x["status"] != "RUNNING"]
        m = meta.get(tbl.upper(), {})
        end_or_start = lambda x: x["ended"] or x["started"]
        tables.append({
            "lane": lane, "lane_name": LANE_NAMES.get(lane, lane), "table": tbl, "status": status, "reason": reason,
            "mode": last["mode"], "last_status": last["status"], "last_start": _iso(last["started"]),
            "last_end": _iso(last["ended"]), "last_duration_s": last["duration_s"],
            "last_rows": None if last["status"] == "RUNNING" else (last["inserted"] or 0) + (last["updated"] or 0),
            "last_pulled": last["pulled"], "target_count": last["target"], "last_ok": _iso(last_ok),
            "loaded_24h": any(end_or_start(x) and end_or_start(x) > now - timedelta(hours=24) and x["status"] in ("SUCCESS", "PARTIAL") for x in runs),
            "rows_24h": sum((x["inserted"] or 0) + (x["updated"] or 0) for x in runs if x["started"] and x["started"] > now - timedelta(hours=24)),
            "runs_24h": sum(1 for x in runs if x["started"] and x["started"] > now - timedelta(hours=24)),
            "cadence_min": round(cadence) if cadence else None, "late_after_min": round(late_after) if late_after else None, "median_duration_s": round(med_dur) if med_dur else None,
            "late_by": _iso(last_ok + timedelta(minutes=late_after)) if late_after and last_ok and status != "RETIRED" else None,
            "success_rate_7d": round(100.0 * sum(1 for x in n7 if x["status"] == "SUCCESS") / len(n7), 1) if n7 else None,
            "runs_7d": len(n7), "bronze_last_altered": _iso(_naive(m.get("last_altered"))), "bronze_rows": m.get("row_count"),
            "runs": [{"status": x["status"], "mode": x["mode"], "started": _iso(x["started"]), "ended": _iso(x["ended"]),
                      "duration_s": x["duration_s"], "pulled": x["pulled"], "inserted": x["inserted"],
                      "updated": x["updated"], "target": x["target"], "error": x["error"], "run_id": x["run_id"]}
                     for x in runs[:40]],
        })
    tables.sort(key=lambda t: (t["lane"] not in (MAIN_LANE, HRMS_LANE), t["lane"] != MAIN_LANE, -SEV[t["status"]], t["table"]))
    lanes = []
    for lane in sorted({t["lane"] for t in tables}, key=lambda l: (l != MAIN_LANE, l != HRMS_LANE, l)):
        ts = [t for t in tables if t["lane"] == lane and t["status"] != "RETIRED"]
        cnt = defaultdict(int)
        for t in ts:
            cnt[t["status"]] += 1
        lanes.append({"lane": lane, "name": LANE_NAMES.get(lane, lane), "tables": len(ts),
                      "retired": sum(1 for t in tables if t["lane"] == lane and t["status"] == "RETIRED"),
                      "loaded_24h": sum(1 for t in ts if t["loaded_24h"]), "counts": dict(cnt),
                      "on_schedule": sum(1 for t in ts if t["status"] in ("OK", "RUNNING")),
                      "rows_24h": sum(t["rows_24h"] for t in ts),
                      "last_load": max((t["last_end"] or t["last_start"] or "" for t in ts), default=None) or None,
                      "daily": [{"d": d, **v} for d, v in sorted(daily[lane].items())]})
    return {"tables": tables, "lanes": lanes, "main_lane": MAIN_LANE, "error": None}


REQ_SQL = """
select REQUEST_ID, TABLE_NAME, MODE, START_DATE, END_DATE, REQUESTED_BY,
       convert_timezone('America/Los_Angeles', 'Asia/Kolkata', REQUESTED_AT)::timestamp_ntz REQUESTED_IST, STATUS, NOTE
from V2RETAIL.BRONZE.SYNC_REQUEST
where REQUESTED_AT > dateadd('day', -14, convert_timezone('America/Los_Angeles', current_timestamp())::timestamp_ntz)
order by REQUESTED_AT desc
"""
REG_SQL = "select TABLE_NAME, TARGET, SERVICE, MODE, STATUS, NOTE from V2RETAIL.BRONZE.SYNC_TABLES"
# HRMS_SYNC_LOG.LOG_TS is IST (the sync stamps it in IST). Latest load event per BRONZE target.
HRMS_SQL = """
select TARGET_TABLE, SOURCE_TABLE, ACTION, ROW_COUNT, STATUS, DURATION_MS, left(MESSAGE, 300) MESSAGE,
       left(ERROR_DETAIL, 600) ERROR_DETAIL, LOG_TS, MACHINE_NAME, RUN_ID
from V2RETAIL.BRONZE.HRMS_SYNC_LOG
where EVENT_TYPE in ('MASTER_LOAD', 'TRANSACTION_LOAD') and LOG_TS > dateadd('day', -30, current_timestamp())
qualify row_number() over (partition by TARGET_TABLE order by LOG_TS desc) = 1
"""
NON_QUEUE_MODES = {"external", "push", "view"}


def rerun_rule(reg):
    """How (and whether) a table can be re-run through BRONZE.SYNC_REQUEST.

    The .24 SAP runner polls STATUS='PENDING' (4-6 times a day as of 2026-10-07); the HRMS
    sync on .27 polls 'PENDING_HRMS' hourly. Tables loaded by rfc-api, ars push or other
    external jobs have no queue consumer, so a request for them would never run."""
    if not reg:
        return {"allowed": False, "why": "Not registered in BRONZE.SYNC_TABLES, so no runner would pick up a request."}
    mode, st, svc = (reg.get("mode") or "").lower(), (reg.get("status") or "").upper(), reg.get("service") or ""
    if st != "ACTIVE":
        return {"allowed": False, "why": "Marked %s in BRONZE.SYNC_TABLES." % st}
    if mode in NON_QUEUE_MODES:
        return {"allowed": False, "why": "Loaded by %s, which does not take requests from the queue." % svc}
    hrms = reg["table_name"].upper().startswith("HRMS")
    return {"allowed": True, "queue_status": "PENDING_HRMS" if hrms else "PENDING", "mode": reg.get("mode"),
            "service": svc, "runner": "The HRMS sync on DataCenter .27 (checks hourly)" if hrms
            else "The SAP runner on .24 (checks the queue a few times a day, every 3-6 h)",
            "dates": mode in ("delta", "delins", "delins_hr", "daterange")}


def fetch(conn, now):
    reg, reqs, extra_err = {}, [], []
    try:
        reg = {r["table_name"].upper(): r for r in db.run(REG_SQL, conn=conn)}
    except Exception:
        extra_err.append("grant select on table V2RETAIL.BRONZE.SYNC_TABLES to role REPORT_PORTAL_SVC_ROLE;")
    try:
        reqs = db.run(REQ_SQL, conn=conn)
    except Exception:
        extra_err.append("grant select, insert on table V2RETAIL.BRONZE.SYNC_REQUEST to role REPORT_PORTAL_SVC_ROLE;")
    hrms_rows, hrms_err = [], None
    try:
        hrms_rows = db.run(HRMS_SQL, conn=conn)
    except Exception:
        hrms_err = "grant select on table V2RETAIL.BRONZE.HRMS_SYNC_LOG to role REPORT_PORTAL_SVC_ROLE;"
    try:
        rows = db.run(SQL, conn=conn)
    except Exception as e:
        return {"tables": [], "lanes": [], "main_lane": MAIN_LANE,
                "error": "Cannot read V2RETAIL.BRONZE.SYNC_LOG (%s). An ACCOUNTADMIN must run: "
                         "grant select on table V2RETAIL.BRONZE.SYNC_LOG to role REPORT_PORTAL_SVC_ROLE;" % str(e)[:160]}
    names = sorted({(r["table_name"] or "").upper() for r in rows if r["table_name"]})
    meta = {}
    if names:
        try:
            ph = ",".join(["%s"] * len(names))
            for m in db.run("select TABLE_NAME, LAST_ALTERED, ROW_COUNT from V2RETAIL.information_schema.tables "
                            "where TABLE_SCHEMA = 'BRONZE' and TABLE_NAME in (%s)" % ph, tuple(names), conn=conn):
                meta[m["table_name"]] = m
        except Exception:
            pass
    out = build(rows, meta, now)
    by_tbl = defaultdict(list)
    for q in reqs:
        by_tbl[(q["table_name"] or "").upper()].append({
            "id": q["request_id"], "mode": q["mode"], "start": q["start_date"], "end": q["end_date"],
            "by": q["requested_by"], "at": _iso(_naive(q["requested_ist"])), "status": (q["status"] or "").upper(),
            "note": q["note"]})
    for t in out["tables"]:
        t["rerun"] = rerun_rule(reg.get(t["table"].upper()))
        t["requests"] = by_tbl.get(t["table"].upper(), [])[:10]
        t["open_request"] = next((r for r in t["requests"] if r["status"] in ("PENDING", "PENDING_HRMS", "RUNNING")), None)
    out["queue_error"] = ("Rerun needs these grants (run as ACCOUNTADMIN): " + " ".join(extra_err)) if extra_err else None
    members = defaultdict(list)
    for h in hrms_rows:
        tgt = (h["target_table"] or "").upper()
        grp = tgt if tgt in ("HRMS_ATTENDANCE", "HRMS_PAYROLL_TRAN") else "HRMS_MASTERS"
        members[grp].append({"target": tgt, "source": h["source_table"], "action": h["action"], "rows": h["row_count"],
                             "status": (h["status"] or "").upper(), "duration_s": round((h["duration_ms"] or 0) / 1000, 1),
                             "message": h["message"], "error": h["error_detail"], "at": _iso(_naive(h["log_ts"])),
                             "machine": h["machine_name"], "run_id": h["run_id"]})
    for t in out["tables"]:
        if t["lane"] == HRMS_LANE:
            t["members"] = sorted(members.get(t["table"].upper(), []), key=lambda m: m["target"])
    out["hrms_detail_error"] = ("Per-table HRMS detail needs: " + hrms_err) if hrms_err else None
    return out
