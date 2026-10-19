"""Background worker (second Windows service). Usage: python -m pulse.worker

Every PULSE_WORKER_POLL_SECONDS it runs jobs that are due (ops.job.next_run_at) or were
requested from the UI (ops.job_request). Each job holds a Postgres advisory lock while it
runs, so even two worker processes never run the same job at the same time. Every run is
recorded in ops.job_run and as an action in the audit log.

Schedules (IST): "every:15m" | "every:2h" | "daily:07:30,13:45" | "manual"."""
import socket
import sys
import time
import traceback
import zlib
from datetime import datetime, timedelta, timezone

import psycopg

from . import audit, db
from .config import settings

IST = timedelta(hours=5, minutes=30)
HOST = socket.gethostname()


def next_run(schedule: str, now: datetime) -> datetime | None:
    kind, _, spec = schedule.partition(":")
    if kind == "every":
        n, unit = int(spec[:-1]), spec[-1]
        return now + (timedelta(minutes=n) if unit == "m" else timedelta(hours=n))
    if kind == "daily":
        local = now + IST
        cands = []
        for t in spec.split(","):
            h, m = (int(x) for x in t.split(":"))
            c = local.replace(hour=h, minute=m, second=0, microsecond=0)
            if c <= local:
                c += timedelta(days=1)
            cands.append(c)
        return min(cands) - IST
    return None


def _jobs():
    from .modules import bi, control_tower, masters, portals
    from .sync import hr
    from .data import contracts
    from .dumps import engine as dumps

    def partitions():
        with psycopg.connect(settings().database_owner_url, autocommit=True) as c:
            made = c.execute("select audit.ensure_partitions(2)").fetchone()[0]
            dropped = c.execute("select audit.drop_expired(36, 13)").fetchone()[0]
        return {"created": made, "dropped": dropped}

    def housekeeping():
        with db.tx() as cur:
            cur.execute("delete from data.query_cache where expires_at < now()")
            a = cur.rowcount
            cur.execute("delete from iam.oidc_state where created_at < now() - interval '1 hour'")
            cur.execute("delete from data.rate_bucket where window_start < now() - interval '2 days'")
            cur.execute("delete from iam.session where (revoked_at is not null or absolute_expires_at < now())"
                        " and created_at < now() - interval '90 days'")
            b = cur.rowcount
        return {"cache_rows_expired": a, "old_sessions_deleted": b}

    def backup():
        """Nightly pg_dump (custom format) as the owner; keeps the newest N days. Snapshot rows (snap.*) are
        left out: refresh jobs rebuild them from Snowflake, and forced row-level security refuses to dump them."""
        import os
        import subprocess
        from urllib.parse import urlparse, unquote
        s = settings()
        u = urlparse(s.database_owner_url)
        os.makedirs(s.backup_dir, exist_ok=True)
        name = os.path.join(s.backup_dir, "pulse-%s.dump" % datetime.now().strftime("%Y%m%d-%H%M"))
        env = {**os.environ, "PGPASSWORD": unquote(u.password or "")}
        r = subprocess.run([os.path.join(s.pg_bin, "pg_dump.exe"), "-h", u.hostname, "-p", str(u.port), "-U",
                            unquote(u.username), "-Fc", "--exclude-table-data=snap.*", "-f", name,
                            u.path.lstrip("/")],
                           env=env, capture_output=True, text=True, timeout=3600, stdin=subprocess.DEVNULL)
        if r.returncode:
            raise RuntimeError("pg_dump failed: " + (r.stderr or r.stdout).strip()[:500])
        cutoff = time.time() - s.backup_keep_days * 86400
        old = [f for f in os.listdir(s.backup_dir) if f.startswith("pulse-") and f.endswith(".dump")
               and os.path.getmtime(os.path.join(s.backup_dir, f)) < cutoff]
        for f in old:
            os.remove(os.path.join(s.backup_dir, f))
        return {"file": name, "bytes": os.path.getsize(name), "removed": len(old)}

    return {
        "db_backup": backup,
        "hr_sync": hr.run,
        "ct_refresh": control_tower.refresh,
        "portal_health": portals.check_all,
        "masters_profile": masters.profile_all,
        "bi_prewarm": bi.prewarm,
        "audit_partitions": partitions,
        "housekeeping": housekeeping,
        "dump_dispatch": dumps.dispatch,   # starts dumps in background threads and returns at once
        "refresh:": lambda code: contracts.refresh(code.split(":", 1)[1]),
    }


def run_job(code: str, triggered_by: str = "schedule") -> dict | None:
    jobs = _jobs()
    fn = jobs.get(code)
    if fn is None and code.startswith("refresh:"):
        fn = lambda: jobs["refresh:"](code)  # noqa: E731
    if fn is None:
        raise KeyError(code)
    key = zlib.crc32(code.encode())
    with psycopg.connect(settings().database_url, autocommit=True) as lockconn:
        if not lockconn.execute("select pg_try_advisory_lock(%s)", (key,)).fetchone()[0]:
            return None  # already running elsewhere
        try:
            with db.tx() as cur:
                cur.execute("insert into ops.job_run (job_code, triggered_by, host) values (%s,%s,%s) returning id",
                            (code, triggered_by, HOST))
                rid = cur.fetchone()["id"]
            t0 = time.time()
            try:
                result = fn() or {}
                status, msg = "ok", str(result)[:2000]
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                status, msg, result = "failed", "%s: %s" % (type(e).__name__, str(e)[:1800]), None
            with db.tx() as cur:
                cur.execute("update ops.job_run set finished_at = now(), status = %s, message = %s where id = %s",
                            (status, msg, rid))
                audit.action(cur, None, "job.run", "job", code,
                             {"status": status, "seconds": round(time.time() - t0, 1), "by": triggered_by, "message": msg[:500]},
                             outcome="ok" if status == "ok" else "failed")
            print("%s job %s %s in %.1fs: %s" % (datetime.now().isoformat(timespec="seconds"), code, status,
                                                 time.time() - t0, msg[:200]), flush=True)
            return {"status": status, "message": msg}
        finally:
            lockconn.execute("select pg_advisory_unlock(%s)", (key,))


def tick():
    now = datetime.now(timezone.utc)
    with db.tx() as cur:
        cur.execute("update ops.job_request set claimed_at = now() where id in (select id from ops.job_request"
                    " where claimed_at is null order by id for update skip locked) returning job_code, requested_by")
        requested = cur.fetchall()
        cur.execute("select * from ops.job where enabled and next_run_at <= now() and schedule <> 'manual'")
        due = cur.fetchall()
        for j in due:
            cur.execute("update ops.job set next_run_at = %s where code = %s", (next_run(j["schedule"], now), j["code"]))
    for r in requested:
        run_job(r["job_code"], "request")
    for j in due:
        run_job(j["code"], "schedule")


def main():
    from . import seed
    audit.access_log.start()
    with db.tx() as cur:
        seed.run(cur)
    print("V2Pulse worker on %s, polling every %ds" % (HOST, settings().worker_poll_seconds), flush=True)
    while True:
        try:
            tick()
        except Exception:  # noqa: BLE001 - keep the service alive; the next tick retries
            traceback.print_exc()
        time.sleep(settings().worker_poll_seconds)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "run":
        print(run_job(sys.argv[2], "cli"))
    else:
        main()
