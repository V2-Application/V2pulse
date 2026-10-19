"""Dump Scheduler engine (ported from DFS planning-service/src/dumpScheduler): runs scheduled and requested
dumps of V2Pulse masters and MIS reports into <folder>\\YYYY_MM_DD\\<file>.csv|xlsx.

The worker job `dump_dispatch` (every minute) calls dispatch(): it queues the schedules that are due, then
starts queued runs in background threads of the worker process (at most PULSE_DUMP_MAX_CONCURRENCY at a
time), so a long dump never holds up the worker's other jobs. Each run holds a Postgres advisory lock on its
schedule, so one schedule never runs twice at once, even with two workers. A running dump writes a heartbeat
every 15 s; a RUNNING row whose heartbeat stops (worker restarted) is marked FAILED by the next dispatch.

Stop: the page sets stop_requested; the run cancels its Snowflake query and stops writing. Timeout: the
schedule's timeout_min (default PULSE_DUMP_DEFAULT_TIMEOUT_MIN) on the whole run. Files are written to a
.part file and renamed at the end, so a folder never holds half a file under the real name.
Failures e-mail the schedule owner (SMTP settings in env.bat) and are logged in ops.dump_mail_log."""
import os
import socket
import threading
import time
import traceback
import zlib
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

from .. import audit, db
from ..config import settings

HOST = os.environ.get("PULSE_DUMP_INSTANCE_NAME") or socket.gethostname()
IST = ZoneInfo("Asia/Kolkata")
KINDS = ("master", "mis_report", "mis_book", "day_trend", "inv_report", "bin_wise", "tna", "mis_day_load")
DFS_KINDS = ("day_trend", "inv_report", "bin_wise", "tna", "mis_day_load")   # pulse/dumps/dfs_kinds.py
MIS_DATE_RULES = ("yesterday", "last7", "last30", "mtd", "last_month")
STALE_AFTER = timedelta(minutes=3)

_threads: dict[int, threading.Thread] = {}
_lock = threading.Lock()


class Stopped(Exception):
    pass


# ── schedule maths ─────────────────────────────────────────────────────────────────────────────────
def next_fire(cron_expr: str, tz: str, after: datetime | None = None, start_at=None) -> datetime | None:
    """Next fire time (UTC) of a 5-field cron in `tz`, not before start_at."""
    from croniter import croniter
    zone = ZoneInfo(tz or "Asia/Kolkata")
    base = (after or datetime.now(timezone.utc)).astimezone(zone)
    if start_at and start_at > base:
        base = start_at.astimezone(zone) - timedelta(seconds=1)
    return croniter(cron_expr, base).get_next(datetime).astimezone(timezone.utc)


def next_runs(cron_expr, tz, n=5, start_at=None, end_at=None):
    out, t = [], None
    for _ in range(n):
        t = next_fire(cron_expr, tz, t, start_at)
        if t is None or (end_at and t > end_at):
            break
        out.append(t)
    return out


def date_range(rule: str, today: date | None = None) -> tuple[str, str]:
    """The MIS date rules of DFS: yesterday | last7 | last30 | mtd | last_month (IST, up to yesterday)."""
    today = today or datetime.now(IST).date()
    y = today - timedelta(days=1)
    if rule == "last7":
        a, b = y - timedelta(days=6), y
    elif rule == "last30":
        a, b = y - timedelta(days=29), y
    elif rule == "mtd":
        a, b = y.replace(day=1), y
    elif rule == "last_month":
        b = today.replace(day=1) - timedelta(days=1)
        a = b.replace(day=1)
    else:
        a = b = y
    return a.isoformat(), b.isoformat()


# ── dispatch (worker job) ──────────────────────────────────────────────────────────────────────────
def dispatch() -> dict:
    s = settings()
    now = datetime.now(timezone.utc)
    queued = skipped = started = reaped = 0
    with db.tx() as cur:
        # Runs that stopped beating (the worker restarted mid-run) or this host's runs that no thread owns.
        cur.execute("select id, host from ops.dump_run where status = 'RUNNING' and (heartbeat_at is null"
                    " or heartbeat_at < now() - %s)", (STALE_AFTER,))
        for r in cur.fetchall():
            if r["host"] == HOST and r["id"] in _threads and _threads[r["id"]].is_alive():
                continue
            _finish(cur, r["id"], "FAILED", error="The run stopped answering (the V2Pulse worker was restarted?).")
            reaped += 1
        # Due schedules -> one queued run each; a schedule with a live run gets a SKIPPED row instead.
        cur.execute("select * from ops.dump_schedule where enabled and next_run_at is not null and next_run_at <= now()"
                    " for update skip locked")
        for sc in cur.fetchall():
            nxt = None if sc["run_once"] else next_fire(sc["cron_expr"], sc["timezone"], now, sc["start_at"])
            ended = sc["end_at"] is not None and (nxt is None or nxt > sc["end_at"])
            cur.execute("update ops.dump_schedule set next_run_at = %s, enabled = %s where id = %s",
                        (None if (ended or sc["run_once"]) else nxt, not (ended or sc["run_once"]), sc["id"]))
            if sc["end_at"] is not None and sc["next_run_at"] > sc["end_at"]:
                continue
            cur.execute("select 1 from ops.dump_run where schedule_id = %s and status in ('QUEUED','RUNNING')", (sc["id"],))
            if cur.fetchone():
                cur.execute("insert into ops.dump_run (schedule_id, trigger_type, status, host, completed_at, error_msg)"
                            " values (%s, 'schedule', 'SKIPPED', %s, now(), 'The previous run was still going.')",
                            (sc["id"], HOST))
                skipped += 1
            else:
                cur.execute("insert into ops.dump_run (schedule_id, trigger_type) values (%s, 'schedule')", (sc["id"],))
                queued += 1
    # Start queued runs while there is room.
    with _lock:
        for rid in [k for k, t in _threads.items() if not t.is_alive()]:
            _threads.pop(rid, None)
        room = s.dump_max_concurrency - len(_threads)
    while room > 0:
        with db.tx() as cur:
            cur.execute("update ops.dump_run set status = 'RUNNING', host = %s, started_at = now(), heartbeat_at = now()"
                        " where id = (select id from ops.dump_run where status = 'QUEUED' order by id limit 1"
                        " for update skip locked) returning id, schedule_id", (HOST,))
            r = cur.fetchone()
        if not r:
            break
        t = threading.Thread(target=_run_safe, args=(r["id"], r["schedule_id"]), name="dump-%s" % r["id"], daemon=True)
        with _lock:
            _threads[r["id"]] = t
        t.start()
        started += 1
        room -= 1
    _purge_once_a_day()
    return {"queued": queued, "skipped": skipped, "started": started, "reaped": reaped, "running": len(_threads)}


_last_purge = [0.0]


def _purge_once_a_day():
    if time.time() - _last_purge[0] < 86400:
        return
    _last_purge[0] = time.time()
    keep = timedelta(days=settings().dump_keep_days)
    with db.tx() as cur:
        cur.execute("delete from ops.dump_run where status not in ('QUEUED','RUNNING') and queued_at < now() - %s", (keep,))
        cur.execute("delete from ops.dump_mail_log where created_at < now() - %s", (keep,))


# ── one run ────────────────────────────────────────────────────────────────────────────────────────
def _finish(cur, run_id, status, *, files=0, rows=0, paths=(), error=None):
    cur.execute("update ops.dump_run set status = %s, completed_at = now(), files = %s, rows_written = %s,"
                " file_paths = %s, error_msg = %s, progress = null,"
                " duration_ms = (extract(epoch from now() - coalesce(started_at, queued_at)) * 1000)::bigint"
                " where id = %s returning schedule_id", (status, files, rows, list(paths), error, run_id))
    r = cur.fetchone()
    if r:
        cur.execute("update ops.dump_schedule set last_run_at = now(), last_status = %s, last_run_id = %s where id = %s",
                    (status, run_id, r["schedule_id"]))


class _Run:
    """Heartbeat, progress, stop and timeout for one running dump."""

    def __init__(self, run_id, timeout_min):
        self.id = run_id
        self.deadline = time.time() + 60 * timeout_min
        self.stop = False
        self.rows = 0
        self.note = ""
        self._done = threading.Event()
        self._qids = []
        threading.Thread(target=self._beat, daemon=True).start()

    def _beat(self):
        while not self._done.wait(15):
            try:
                with db.tx() as cur:
                    cur.execute("update ops.dump_run set heartbeat_at = now(), rows_written = %s, progress = %s"
                                " where id = %s returning stop_requested", (self.rows, self.note[:300], self.id))
                    r = cur.fetchone()
                if r and r["stop_requested"]:
                    self.stop = True
            except Exception:  # noqa: BLE001 - a missed beat is not fatal
                traceback.print_exc()
            if self.stop or time.time() > self.deadline:
                self._cancel_queries()

    def _cancel_queries(self):
        for conn, qid in list(self._qids):
            try:
                c2 = conn.cursor()
                c2.execute("select system$cancel_query(%s)", (qid,))
                c2.close()
            except Exception:  # noqa: BLE001
                pass

    def check(self, n=None):
        if n is not None:
            self.rows = self.base_rows + n
        if self.stop:
            raise Stopped("Stopped from the Dump Scheduler page.")
        if time.time() > self.deadline:
            raise TimeoutError("The run took longer than its time limit and was stopped.")

    base_rows = 0

    def execute(self, conn, cur, sql, params=None):
        """Run a statement asynchronously so Stop / timeout can cancel it, then attach its result to `cur`."""
        cur.execute_async(sql, params)
        qid = cur.sfqid
        self._qids.append((conn, qid))
        try:
            while conn.is_still_running(conn.get_query_status(qid)):
                if self.stop or time.time() > self.deadline:
                    self._cancel_queries()
                    self.check()
                time.sleep(1)
            conn.get_query_status_throw_if_error(qid)
            # Re-read the finished result through RESULT_SCAN: it comes from the result cache (no warehouse
            # work) and, unlike get_results_from_sfqid, fills cursor.description before the first fetch.
            cur.execute("select * from table(result_scan(%s))", (qid,))
        finally:
            self._qids.remove((conn, qid))
        self.check()

    def close(self):
        self._done.set()


def _run_safe(run_id, schedule_id):
    key = zlib.crc32(("dump:%s" % schedule_id).encode())
    try:
        with psycopg.connect(settings().database_url, autocommit=True) as lockconn:
            if not lockconn.execute("select pg_try_advisory_lock(%s)", (key,)).fetchone()[0]:
                with db.tx() as cur:
                    _finish(cur, run_id, "SKIPPED", error="The same schedule is already running.")
                return
            try:
                _run(run_id, schedule_id)
            finally:
                lockconn.execute("select pg_advisory_unlock(%s)", (key,))
    except Exception:  # noqa: BLE001 - never kill the worker
        traceback.print_exc()
    finally:
        with _lock:
            _threads.pop(run_id, None)


def _run(run_id, schedule_id):
    s = settings()
    with db.tx() as cur:
        cur.execute("select * from ops.dump_schedule where id = %s", (schedule_id,))
        sc = cur.fetchone()
    if not sc:
        with db.tx() as cur:
            _finish(cur, run_id, "FAILED", error="The schedule was deleted.")
        return
    run = _Run(run_id, sc["timeout_min"] or s.dump_default_timeout_min)
    t0 = time.time()
    paths, total = [], 0
    try:
        from . import dfs_kinds
        if sc["kind"] == "mis_day_load":       # a load, not a dump: no folder, no files
            _mis_owner(sc, "mis.admin")
            total = dfs_kinds.run_mis_day_load(sc, run)
        else:
            out_dir = _out_dir(sc["folder"])
            name = _file_base(sc)
            if sc["kind"] == "master":
                total = _dump_master(sc, out_dir, name, run, paths)
            elif sc["kind"] == "mis_report":
                total = _dump_mis(sc, out_dir, name, run, paths)
            elif sc["kind"] in dfs_kinds.KIND_RUNNERS:
                _mis_owner(sc)
                total = dfs_kinds.KIND_RUNNERS[sc["kind"]](sc, out_dir, name, run, paths)
            else:
                total = _dump_book(sc, out_dir, name, run, paths)
        with db.tx() as cur:
            _finish(cur, run_id, "DONE", files=len(paths), rows=total, paths=paths)
            audit.action(cur, None, "dump.run", "dump_schedule", str(schedule_id),
                         {"run": run_id, "status": "DONE", "files": paths, "rows": total,
                          "seconds": round(time.time() - t0, 1)})
    except Stopped as e:
        with db.tx() as cur:
            _finish(cur, run_id, "STOPPED", files=len(paths), rows=total, paths=paths, error=str(e))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        msg = "%s: %s" % (type(e).__name__, str(e)[:1800]) if not isinstance(e, (TimeoutError, OSError)) else str(e)[:1800]
        with db.tx() as cur:
            _finish(cur, run_id, "FAILED", files=len(paths), rows=total, paths=paths, error=msg)
            audit.action(cur, None, "dump.run", "dump_schedule", str(schedule_id),
                         {"run": run_id, "status": "FAILED", "error": msg[:500]}, outcome="failed")
        notify_failure(sc, run_id, msg)
    finally:
        run.close()


def _out_dir(folder):
    folder = (folder or "").strip()
    if not folder or not os.path.isdir(folder):
        raise OSError(f"The dump folder {folder!r} does not exist or the V2Pulse worker cannot reach it.")
    d = os.path.join(folder, datetime.now(IST).strftime("%Y_%m_%d"))
    os.makedirs(d, exist_ok=True)
    return d


def _file_base(sc):
    from ..mis_engine.export import file_safe
    return file_safe(sc["file_name"] or sc["name"])


def _publish(tmp, final, paths):
    os.replace(tmp, final)
    paths.append(final)


def _write(sc, out_dir, name, hdr, cur, run, paths, sheet=None):
    """One result set into one CSV or single-sheet workbook. Returns the row count."""
    from ..mis_engine import export as mx
    final = os.path.join(out_dir, f"{name}.{sc['format']}")
    tmp = final + ".part"
    try:
        if sc["format"] == "csv":
            with open(tmp, "w", newline="", encoding="utf-8-sig" if sc.get("kind") == "master" else "utf-8") as fh:
                n = mx.write_csv(fh, hdr, cur, tick=run.check)
        else:
            wb, fmts = mx.new_workbook(tmp)
            try:
                n = mx.add_sheet(wb, mx.sheet_safe(sheet or name), hdr, cur, fmts, tick=run.check)
            finally:
                wb.close()
        _publish(tmp, final, paths)
        return n
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _owner_ctx(sc):
    from ..iam.resolve import load_ctx
    with db.tx() as cur:
        ctx = load_ctx(cur, str(sc["owner_user_id"]), None, "dump")
    if ctx is None:
        raise PermissionError(f"The schedule owner {sc['owner_emp_code']} is no longer an active V2Pulse user.")
    return ctx


def _dump_master(sc, out_dir, name, run, paths):
    """A V2Pulse master, as its owner sees it (masters.view + store scope), with the schedule's filters."""
    from fastapi import HTTPException
    from ..data.snowflake import connect
    from ..modules import masters
    ctx = _owner_ctx(sc)
    if not (ctx.can("masters.view") and ctx.can("masters.export")):
        raise PermissionError(f"The owner {sc['owner_emp_code']} may not export masters (masters.export).")
    cfg = sc["config"] or {}
    try:
        with db.tx() as cur:
            m = masters._master(cur, sc["target"], ctx)
        fl = masters.parse_filters(_json(cfg.get("filters")), m)
        where, binds = masters.build_where(m, ctx, cfg.get("q") or "", fl)
    except HTTPException as e:
        raise PermissionError(f"Master {sc['target']}: {e.detail}")
    cols = [c for c in masters._cols(m) if not c.get("hidden")] or masters._cols(m)
    sql = "select %s from %s where %s%s" % (", ".join(masters._qi(c["name"]) for c in cols), m["source_object"], where,
                                            masters._order(m, cfg.get("sort") or "", cfg.get("dir") or "asc"))
    run.note = "Reading master %s" % sc["target"]
    conn = connect("pulse", tag={"module": "dumps", "schedule": sc["id"]})
    try:
        cur = conn.cursor()
        run.execute(conn, cur, sql, binds)
        run.note = "Writing %s" % sc["target"]
        return _write(sc, out_dir, name, [c["label"] for c in cols], cur, run, paths, sheet=m["name"])
    finally:
        conn.close()


def _json(v):
    import json
    return v if isinstance(v, str) else json.dumps(v or [])


def _mis_req(slug, cfg):
    from ..mis_engine.routes import sf_mis_report as rep
    a, b = (cfg.get("from_date"), cfg.get("to_date")) if cfg.get("dateRule") == "fixed" else \
        date_range(cfg.get("dateRule") or "yesterday")
    return rep.RunReq(slug=slug, from_date=a, to_date=b, filters=cfg.get("filters") or None,
                      col_filters=cfg.get("col_filters") or None, layout=cfg.get("layout") or None)


def _mis_owner(sc, perm="mis.export"):
    from ..mis_engine import CURRENT_USER
    ctx = _owner_ctx(sc)
    if not ctx.can(perm):
        raise PermissionError(f"The owner {sc['owner_emp_code']} may not run this ({perm}).")
    CURRENT_USER.set(ctx.emp_code)


def _mis_safe(v):
    """DFS _misSafe: a level value as a file-name part."""
    import re
    s = re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", "BLANK" if v in (None, "") else str(v))).strip()[:60]
    return s or "BLANK"


class _PlainGuard:
    @staticmethod
    def execute(cur, sql, *a, **k):
        return cur.execute(sql, *a, **k)


def _dump_mis(sc, out_dir, name, run, paths):
    """One saved MIS report, named as DFS names it: <file name>_<from>_to_<to>.csv; with levels (config.levels,
    e.g. ["ZONE"]) one file per value that occurs: <file name>_<from>_to_<to>__ZONE_ZONE-1.csv."""
    from ..mis_engine import export as mx
    from ..mis_engine.routes import sf_mis_report as rep
    _mis_owner(sc)
    cfg = sc["config"] or {}
    req = _mis_req(sc["target"], cfg)
    stem = "%s_%s_to_%s" % (name, req.from_date, req.to_date)
    levels = [str(x).strip().upper() for x in (cfg.get("levels") or []) if str(x).strip()]
    if not levels:
        run.note = "Running MIS report %s (%s to %s)" % (sc["target"], req.from_date, req.to_date)
        conn, cur, hdr, meta = mx.report_cursor(req, runner=run.execute)
        try:
            run.note = "Writing %s" % meta["name"]
            return _write(sc, out_dir, stem, hdr, cur, run, paths, sheet=meta["name"])
        finally:
            conn.close()
    run.note = "Listing %s values of %s" % (" + ".join(levels), sc["target"])
    plan = rep._mis_values_body(rep.RunReq(slug=req.slug, columns=levels, filters=req.filters,
                                           from_date=req.from_date, to_date=req.to_date), _PlainGuard).get("combos") or []
    if not plan:
        raise ValueError("%s: no values in %s to %s - nothing written" % (" + ".join(levels), req.from_date, req.to_date))
    total = 0
    for k, combo in enumerate(plan, 1):
        run.check()
        filters = dict(req.filters or {})
        for lv, v in zip(levels, combo):
            filters[lv] = [v]
        part = req.model_copy(update={"filters": filters})
        suffix = "__".join("%s_%s" % (_mis_safe(lv), _mis_safe(v)) for lv, v in zip(levels, combo))
        run.note = "File %d of %d: %s" % (k, len(plan), suffix)
        run.base_rows = total
        conn, cur, hdr, meta = mx.report_cursor(part, runner=run.execute)
        try:
            total += _write(sc, out_dir, "%s__%s" % (stem, suffix), hdr, cur, run, paths, sheet=meta["name"])
        finally:
            conn.close()
    return total


def _dump_book(sc, out_dir, name, run, paths):
    """Several MIS reports, one sheet each, in one workbook (always .xlsx)."""
    from ..mis_engine import export as mx
    _mis_owner(sc)
    cfg = sc["config"] or {}
    sheets = cfg.get("sheets") or {}
    reqs = [(sheets.get(slug), _mis_req(slug, cfg)) for slug in [x.strip() for x in sc["target"].split(",") if x.strip()]]
    final = os.path.join(out_dir, f"{name}.xlsx")
    tmp = final + ".part"
    total = 0

    def tick(n):
        run.check(n)

    try:
        run.note = "Building workbook of %d reports" % len(reqs)
        out = mx.write_xlsx(tmp, reqs, runner=run.execute, tick=tick)
        total = sum(x["rows"] for x in out)
        _publish(tmp, final, paths)
        return total
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ── failure e-mail ─────────────────────────────────────────────────────────────────────────────────
def notify_failure(sc, run_id, error):
    s = settings()
    t0 = time.time()
    with db.tx() as cur:
        cur.execute("select email from iam.users where id = %s", (sc["owner_user_id"],))
        u = cur.fetchone()
    to = (u or {}).get("email") or ""
    cc = ", ".join(x.strip() for x in (s.dump_fail_mail_cc or "").split(",") if x.strip())
    subject = "V2Pulse dump failed: %s" % sc["name"]
    status, err = "SENT", None
    if not s.smtp_host:
        status = "NOT_CONFIGURED"
    elif not to:
        status = "NO_RECIPIENT"
    else:
        try:
            import smtplib
            from email.message import EmailMessage
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = subject, s.smtp_from or s.smtp_user, to
            if cc:
                msg["Cc"] = cc
            link = s.base_url.rstrip("/") + "/dumps"
            msg.set_content("The scheduled dump '%s' failed (run %s, %s).\n\n%s\n\nFolder: %s\nOpen the Dump Scheduler: %s\n"
                            % (sc["name"], run_id, datetime.now(IST).strftime("%d-%m-%Y %H:%M IST"), error, sc["folder"], link))
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
                if s.smtp_starttls:
                    smtp.starttls()
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_pass)
                smtp.send_message(msg)
        except Exception as e:  # noqa: BLE001
            status, err = "FAILED", str(e)[:1000]
    with db.tx() as cur:
        cur.execute("insert into ops.dump_mail_log (run_id, schedule_id, schedule_name, status, mail_to, mail_cc, subject,"
                    " error_msg, host, duration_ms) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (run_id, sc["id"], sc["name"], status, to or None, cc or None, subject, err, HOST,
                     int((time.time() - t0) * 1000)))
