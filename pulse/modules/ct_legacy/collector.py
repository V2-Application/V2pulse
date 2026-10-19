"""Builds the portal's in-memory snapshot from Snowflake.

One refresh = one short burst on the warehouse: SHOW TASKS (metadata, no warehouse),
7 days of TASK_HISTORY, table LAST_ALTERED for freshness, the portal's own catalog, users
and external run log. Page views only ever read the snapshot, so viewers never wake a
warehouse (every resume bills a 60s minimum; see the cost notes in the README).

Status rules, in priority order (worst wins):
  FAILED     a linked task's latest finished run failed/cancelled, or the latest external
             heartbeat says FAIL
  STALE      the data is older than MAX_AGE_HOURS. This is checked independently of task
             state, because a task that SUCCEEDED does not prove data landed (Sep-2026 WM freeze)
  SUSPENDED  every linked task is suspended, so nothing will run on schedule
  RUNNING    a linked task is executing right now
  OK         everything above is clear
  UNMONITORED nothing to check (no task, no freshness rule)
"""
import re, threading, time, traceback
from collections import defaultdict
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import cronfmt, db, dfs, inventory, sapsync

IST = ZoneInfo("Asia/Kolkata")
P = db.PORTAL_SCHEMA
HIDE_TASKS = re.compile(r"^AI_EVALS_")          # Cortex eval plumbing, not reports
RUN_KEEP = 60                                     # runs per task kept in the snapshot
SEVERITY = {"FAILED": 5, "STALE": 4, "SUSPENDED": 3, "WARN": 3, "RUNNING": 2, "OK": 1, "UNMONITORED": 0}


def now_ist():
    return datetime.now(IST).replace(tzinfo=None)


def _ist(v):
    """Snowflake value -> naive IST datetime (session TZ is IST; NTZ values are stored IST)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.astimezone(IST).replace(tzinfo=None) if v.tzinfo else v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    return None


def _iso(v):
    return v.isoformat(timespec="seconds") if isinstance(v, datetime) else v


def _json_task_history(rows):
    out = defaultdict(list)
    for r in rows:
        fqn = "%s.%s.%s" % (r["database_name"], r["schema_name"], r["name"])
        st, sc, qs, co = r["state"], _ist(r["scheduled_time"]), _ist(r["query_start_time"]), _ist(r["completed_time"])
        out[fqn].append({
            "state": st, "scheduled": _iso(sc), "started": _iso(qs), "completed": _iso(co),
            "duration_s": round((co - qs).total_seconds()) if co and qs else None,
            "error_code": r.get("error_code"), "error": (r.get("error_message") or "")[:2000] or None,
            "query_id": r.get("query_id"), "trigger": (r.get("scheduled_from") or "").replace(" ", "_"),
            "attempt": r.get("attempt_number"), "run_id": r.get("run_id"),
            "return_value": r.get("return_value"),
        })
    for runs in out.values():
        runs.sort(key=lambda x: x["scheduled"] or "", reverse=True)
    return out


HISTORY_SQL = """
select database_name, schema_name, name, state, error_code, error_message, scheduled_time,
       query_start_time, completed_time, query_id, scheduled_from, attempt_number, run_id, return_value
from table(V2RETAIL.information_schema.task_history(
       scheduled_time_range_start => dateadd('hour', -167, current_timestamp()),
       result_limit => 10000 {extra}))
"""


def fetch_task_history(conn, task_name=None):
    extra = ", task_name => %s" if task_name else ""
    return db.run(HISTORY_SQL.format(extra=extra), (task_name,) if task_name else None, conn=conn)


def build_tasks(show_rows, hist):
    tasks = {}
    for t in show_rows:
        if t["database_name"] == "SNOWFLAKE" or HIDE_TASKS.match(t["name"]):
            continue
        fqn = "%s.%s.%s" % (t["database_name"], t["schema_name"], t["name"])
        preds = []
        try:
            import json
            preds = json.loads(t.get("predecessors") or "[]")
        except Exception:
            pass
        summary, detail = cronfmt.describe(t.get("schedule"), preds)
        tasks[fqn] = {
            "fqn": fqn, "database": t["database_name"], "schema": t["schema_name"], "name": t["name"],
            "state": (t.get("state") or "").upper(), "schedule": t.get("schedule"),
            "schedule_text": summary, "schedule_detail": detail,
            "warehouse": t.get("warehouse") or "Serverless", "owner": t.get("owner"),
            "comment": t.get("comment") or "", "definition": t.get("definition") or "",
            "predecessors": preds, "children": [], "condition": t.get("condition"),
            "last_suspended_reason": t.get("last_suspended_reason"),
            "last_suspended_on": _iso(_ist(t.get("last_suspended_on"))),
        }
    for fqn, t in tasks.items():
        for p in t["predecessors"]:
            if p in tasks:
                tasks[p]["children"].append(fqn)
    for fqn, t in tasks.items():
        root = fqn
        seen = set()
        while tasks.get(root, {}).get("predecessors") and root not in seen:
            seen.add(root)
            root = tasks[root]["predecessors"][0]
        t["root"] = root
        apply_history(t, hist.get(fqn, []))
    return tasks


def apply_history(t, runs):
    done = [r for r in runs if r["state"] not in ("SCHEDULED", "EXECUTING")]
    t["runs"] = runs[:RUN_KEEP]
    t["executing"] = any(r["state"] == "EXECUTING" for r in runs)
    sched = [r for r in runs if r["state"] == "SCHEDULED"]
    t["next_run"] = min((r["scheduled"] for r in sched), default=None)
    t["last_run"] = done[0] if done else None
    t["last_success"] = next((r["completed"] for r in done if r["state"] == "SUCCEEDED"), None)
    fails = [r for r in done if r["state"] in ("FAILED", "CANCELLED", "FAILED_AND_AUTO_SUSPENDED")]
    t["last_failure"] = fails[0] if fails else None
    ok = sum(1 for r in done if r["state"] == "SUCCEEDED")
    bad = len(fails)
    t["runs_ok"], t["runs_failed"], t["runs_skipped"] = ok, bad, sum(1 for r in done if r["state"] == "SKIPPED")
    t["success_rate"] = round(100.0 * ok / (ok + bad), 1) if ok + bad else None
    durs = [r["duration_s"] for r in done if r["duration_s"] is not None and r["state"] == "SUCCEEDED"]
    t["avg_duration_s"] = round(sum(durs) / len(durs)) if durs else None
    lr = t["last_run"]
    if t["executing"]:
        t["health"] = "RUNNING"
    elif lr and lr["state"] in ("FAILED", "CANCELLED", "FAILED_AND_AUTO_SUSPENDED"):
        t["health"] = "FAILED"
    elif t["state"] == "SUSPENDED":
        t["health"] = "SUSPENDED"
    elif lr:
        t["health"] = "OK"
    else:
        t["health"] = "UNMONITORED"


def fetch_freshness(conn, catalog):
    """Return {report_id: {ts, source, rows, error}}."""
    out = {}
    by_db = defaultdict(set)
    for r in catalog:
        ft = (r.get("freshness_table") or "").strip().upper()
        if ft and ft.count(".") == 2:
            by_db[ft.split(".")[0]].add(ft)
    meta = {}
    for dbn, names in by_db.items():
        if not re.fullmatch(r"[A-Z0-9_]+", dbn):
            continue
        keys = sorted(names)
        ph = ",".join(["%s"] * len(keys))
        try:
            rows = db.run(
                "select table_catalog||'.'||table_schema||'.'||table_name fqn, last_altered, row_count, table_type "
                "from %s.information_schema.tables where table_catalog||'.'||table_schema||'.'||table_name in (%s)" % (dbn, ph),
                tuple(keys), conn=conn)
            for m in rows:
                meta[m["fqn"]] = m
        except Exception as e:
            for k in keys:
                meta[k] = {"error": str(e)[:300]}
    for r in catalog:
        rid = r["report_id"]
        sql = (r.get("freshness_sql") or "").strip()
        if sql:
            try:
                rows = db.run(sql, conn=conn)
                v = list(rows[0].values())[0] if rows else None
                out[rid] = {"ts": _iso(_ist(v)), "source": "custom SQL", "rows": None, "error": None}
            except Exception as e:
                out[rid] = {"ts": None, "source": "custom SQL", "error": str(e)[:300]}
            continue
        ft = (r.get("freshness_table") or "").strip().upper()
        if not ft:
            continue
        m = meta.get(ft)
        if m is None:
            out[rid] = {"ts": None, "source": ft, "error":
                        "Not visible to the portal role. Either the table does not exist or the role needs: "
                        "GRANT REFERENCES ON TABLE %s TO ROLE <portal role>" % ft}
        elif m.get("error"):
            out[rid] = {"ts": None, "source": ft, "error": m["error"]}
        else:
            kind = "view definition change" if m.get("table_type") == "VIEW" else "last data change"
            out[rid] = {"ts": _iso(_ist(m.get("last_altered"))), "source": "%s (%s)" % (ft, kind),
                        "rows": m.get("row_count"), "error": None, "is_view": m.get("table_type") == "VIEW"}
    return out


def build_reports(catalog, links, tasks, fresh, ext_runs, now):
    by_report = defaultdict(list)
    for l in sorted(links, key=lambda x: (x["report_id"], x.get("step_order") or 0)):
        by_report[l["report_id"]].append(l["task_fqn"])
    out = []
    for r in catalog:
        rid = r["report_id"]
        fq = by_report.get(rid, [])
        found = [tasks[f] for f in fq if f in tasks]
        missing = [f for f in fq if f not in tasks]
        reasons, status = [], "OK"

        def worse(s, why):
            nonlocal status
            reasons.append(why)
            if SEVERITY[s] > SEVERITY[status]:
                status = s

        for t in found:
            if t["health"] == "FAILED":
                lr = t["last_run"] or {}
                worse("FAILED", "%s last run failed at %s: %s" % (t["name"], (lr.get("completed") or "")[:16].replace("T", " "),
                                                                    (lr.get("error") or "")[:160]))
        er = ext_runs.get(rid, [])
        if er and er[0]["status"] == "FAIL":
            worse("FAILED", "External job %s reported FAIL at %s: %s" % (er[0].get("job_name") or "", er[0]["ts"][:16].replace("T", " "), (er[0].get("message") or "")[:160]))
        f = fresh.get(rid)
        age_h = None
        if f and f.get("ts"):
            age_h = round((now - datetime.fromisoformat(f["ts"])).total_seconds() / 3600, 1)
            f["age_hours"] = age_h
            if r.get("max_age_hours") and not f.get("is_view") and age_h > float(r["max_age_hours"]):
                worse("STALE", "Data last changed %.1f h ago (limit %s h)" % (age_h, int(r["max_age_hours"])))
        elif f and f.get("error"):
            worse("WARN", "Freshness check failed: %s" % f["error"][:200])
        if found and r["runner"] != "MANUAL" and all(t["state"] == "SUSPENDED" for t in found):
            worse("SUSPENDED", "All linked tasks are suspended, so nothing runs on schedule")
        if missing:
            worse("WARN", "Linked task not found: %s" % ", ".join(m.split(".")[-1] for m in missing))
        if any(t["executing"] for t in found) and status in ("OK", "UNMONITORED"):
            status = "RUNNING"
            reasons.append("Running now")
        if not found and not (r.get("max_age_hours") and f) and not er and status == "OK":
            status = "UNMONITORED"
            reasons.append("Nothing automatic to check: no Snowflake task and no freshness limit")

        lasts = [t["last_run"] for t in found if t["last_run"]]
        last = max(lasts, key=lambda x: x["completed"] or "", default=None)
        nexts = [t["next_run"] for t in found if t["next_run"] and t["state"] == "STARTED"]
        out.append({
            "id": rid, "name": r["name"], "department": r["department"], "description": r.get("description") or "",
            "runner": r["runner"], "schedule_note": r.get("schedule_note") or "", "pipeline_note": r.get("pipeline_note") or "",
            "output_note": r.get("output_note") or "", "objects": r.get("snowflake_objects") or "", "owner": r.get("owner") or "",
            "freshness_table": r.get("freshness_table"), "freshness_sql": r.get("freshness_sql"),
            "max_age_hours": float(r["max_age_hours"]) if r.get("max_age_hours") is not None else None,
            "is_active": r.get("is_active", True),
            "tasks": fq, "missing_tasks": missing, "status": status, "reasons": reasons,
            "freshness": f, "last_run": last, "next_run": min(nexts) if nexts else None,
            "external_runs": er[:20], "updated_by": r.get("updated_by"), "updated_at": _iso(_ist(r.get("updated_at"))),
        })
    out.sort(key=lambda x: (-SEVERITY[x["status"]], x["department"], x["name"]))
    return out


class Collector:
    def __init__(self):
        self.snapshot = {"refreshed_at": None, "error": None, "tasks": {}, "reports": [], "departments": [],
                         "catalog": [], "links": [], "users": []}
        self.lock = threading.Lock()
        self.refreshing = False
        self.last_manual = 0.0

    # -- full refresh -------------------------------------------------------
    def refresh(self, reason="schedule"):
        if self.refreshing:
            return False
        self.refreshing = True
        t0 = time.time()
        try:
            conn = db.connect()
            try:
                show = db.run("show tasks in account", conn=conn)
                hist = _json_task_history(fetch_task_history(conn))
                tasks = build_tasks(show, hist)
                depts = db.run("select CODE, NAME, SORT_ORDER, DESCRIPTION from %s.DEPARTMENTS order by SORT_ORDER, CODE" % P, conn=conn)
                catalog = db.run("select * from %s.REPORTS" % P, conn=conn)
                links = db.run("select REPORT_ID, TASK_FQN, STEP_ORDER from %s.REPORT_TASKS" % P, conn=conn)
                users = db.run("select * from %s.PORTAL_USERS" % P, conn=conn)
                ext = db.run("select REPORT_ID, RUN_TS_IST, STATUS, ROWS_OUT, MESSAGE, HOST, JOB_NAME from %s.RUN_LOG "
                             "where RUN_TS_IST > dateadd('day', -14, convert_timezone('Asia/Kolkata', current_timestamp())::timestamp_ntz) "
                             "order by RUN_TS_IST desc" % P, conn=conn)
                active = [c for c in catalog if c.get("is_active") is not False]
                fresh = fetch_freshness(conn, active)
                sync = sapsync.fetch(conn, now_ist())
                try:
                    inv = inventory.fetch(conn, now_ist())
                except Exception as e:
                    traceback.print_exc()
                    inv = {"tables": [], "refreshed": None, "error": "Inventory read failed: %s" % str(e)[:300]}
                try:
                    dfsdata = dfs.fetch(conn, now_ist())
                except Exception as e:
                    traceback.print_exc()
                    dfsdata = {"reports": [], "families": {}, "jobs": [], "rfc": [], "error": "DFS read failed: %s" % str(e)[:300]}
            finally:
                conn.close()
            ext_runs = defaultdict(list)
            for e in ext:
                ext_runs[e["report_id"]].append({"ts": _iso(_ist(e["run_ts_ist"])), "status": (e["status"] or "").upper(),
                                                 "rows": e["rows_out"], "message": e["message"], "host": e["host"],
                                                 "job_name": e["job_name"]})
            now = now_ist()
            reports = build_reports(active, links, tasks, fresh, ext_runs, now)
            with self.lock:
                self.snapshot = {
                    "refreshed_at": _iso(now), "refresh_seconds": round(time.time() - t0, 1), "reason": reason,
                    "error": None, "tasks": tasks, "reports": reports, "departments": depts,
                    "catalog": catalog, "links": links, "users": users, "ext_runs": ext_runs,
                    "fresh": fresh, "sync": sync, "dfs": dfsdata, "inventory": inv,
                }
            print("refresh(%s): %d tasks, %d reports in %.1fs" % (reason, len(tasks), len(reports), time.time() - t0), flush=True)
            return True
        except Exception as e:
            traceback.print_exc()
            with self.lock:
                self.snapshot["error"] = "%s: %s" % (type(e).__name__, str(e)[:400])
                self.snapshot["error_at"] = _iso(now_ist())
            return False
        finally:
            self.refreshing = False

    # -- targeted refresh of one task after Run / Resume / Suspend ---------
    def refresh_task(self, fqn):
        conn = db.connect()
        try:
            dbn, sch, name = fqn.split(".")
            show = db.run("show tasks like %%s in schema %s" % db.quote_fqn(fqn).rsplit(".", 1)[0], (name,), conn=conn)
            rows = [r for r in fetch_task_history(conn, task_name=name)
                    if r["database_name"] == dbn and r["schema_name"] == sch]
        finally:
            conn.close()
        hist = _json_task_history(rows).get(fqn, [])
        with self.lock:
            t = self.snapshot["tasks"].get(fqn)
            if not t:
                return None
            for s in show:
                if s["name"] == name:
                    t["state"] = (s.get("state") or "").upper()
            apply_history(t, hist)
            snap = self.snapshot
            fresh = snap.get("fresh", {})
            active = [c for c in snap["catalog"] if c.get("is_active") is not False]
            snap["reports"] = build_reports(active, snap["links"], snap["tasks"], fresh, snap.get("ext_runs", {}), now_ist())
            return t

    def watch_task(self, fqn, minutes=20):
        """Poll one task after a manual run until it finishes, so the page updates live."""
        def loop():
            end = time.time() + minutes * 60
            time.sleep(8)
            while time.time() < end:
                try:
                    t = self.refresh_task(fqn)
                    if t and not t["executing"] and t["last_run"] and t["last_run"].get("trigger") == "EXECUTE_TASK" \
                            and t["last_run"]["completed"] and t["last_run"]["completed"] >= self._started.get(fqn, ""):
                        return
                except Exception:
                    traceback.print_exc()
                time.sleep(20)
        self._started[fqn] = _iso(now_ist() - timedelta(minutes=2))
        threading.Thread(target=loop, daemon=True).start()

    _started = {}

    def refresh_sync(self):
        conn = db.connect()
        try:
            sync = sapsync.fetch(conn, now_ist())
        finally:
            conn.close()
        with self.lock:
            self.snapshot["sync"] = sync

    def watch_sync(self, minutes=30, every=120):
        """After a rerun request, re-read the queue and SYNC_LOG every 2 min for a while."""
        def loop():
            end = time.time() + minutes * 60
            while time.time() < end:
                time.sleep(every)
                try:
                    self.refresh_sync()
                except Exception:
                    traceback.print_exc()
        threading.Thread(target=loop, daemon=True).start()
