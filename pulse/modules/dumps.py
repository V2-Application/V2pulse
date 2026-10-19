"""Dump Scheduler API (ported from DFS /api/dump-scheduler): schedules that export a V2Pulse master or MIS
reports to CSV / Excel in a folder, on a cron. The page is /dumps (pulse/dumps_ui); runs are executed by the
worker (pulse.dumps.engine).

Who sees what (as in DFS): the owner, anyone holding dumps.admin, and holders of a role listed in the
schedule's allowed_roles. Everyone else gets 404, not 403, so schedules are not discoverable."""
import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import audit, db
from ..dumps import engine
from ..iam.context import Ctx
from ..web import envelope, require

router = APIRouter(prefix="/api/v1/dumps", tags=["dumps"])
VIEW, RUN, MANAGE, ADMIN = "dumps.view", "dumps.run", "dumps.manage", "dumps.admin"


def _visible_sql(ctx: Ctx, alias="s"):
    if ctx.can(ADMIN):
        return "true", []
    return f"({alias}.owner_user_id = %s or {alias}.allowed_roles && %s::text[])", [ctx.user_id, list(ctx.roles)]


def _get(cur, sid: int, ctx: Ctx):
    where, binds = _visible_sql(ctx)
    cur.execute(f"select s.* from ops.dump_schedule s where s.id = %s and {where}", [sid, *binds])
    sc = cur.fetchone()
    if not sc:
        raise HTTPException(404, "No such schedule.")
    return sc


def _may_change(sc, ctx: Ctx):
    if not (ctx.can(ADMIN) or (ctx.can(MANAGE) and str(sc["owner_user_id"]) == str(ctx.user_id))):
        raise HTTPException(403, "Only the owner (or a Dump Scheduler admin) can change this schedule.")


def _out(sc, live=None):
    d = dict(sc)
    d["owner_user_id"] = str(d["owner_user_id"])
    if live is not None:
        d["live_run"] = live
    return d


# ── page bootstrap ─────────────────────────────────────────────────────────────────────────────────
@router.get("/instance")
def instance(ctx: Ctx = Depends(require(VIEW))):
    from ..config import settings
    return {"host": engine.HOST, "timezone": "Asia/Kolkata", "roles": ctx.roles, "emp_code": ctx.emp_code,
            "name": ctx.display_name, "mail": bool(settings().smtp_host),
            "can": {"run": ctx.can(RUN), "manage": ctx.can(MANAGE), "admin": ctx.can(ADMIN)},
            "kinds": [{"code": "master", "name": "V2Pulse master"}, {"code": "mis_report", "name": "MIS report"},
                      {"code": "mis_book", "name": "MIS workbook (several reports)"},
                      {"code": "day_trend", "name": "Day Wise Trend (DFS)", "copied_only": True},
                      {"code": "inv_report", "name": "Inventory report (DFS)", "copied_only": True},
                      {"code": "bin_wise", "name": "Bin Wise (DFS)", "copied_only": True},
                      {"code": "tna", "name": "TNA report (DFS)", "copied_only": True},
                      {"code": "mis_day_load", "name": "MIS day-wise load (DFS)", "copied_only": True}],
            "date_rules": list(engine.MIS_DATE_RULES) + ["fixed"]}


@router.get("/targets")
def targets(kind: str, ctx: Ctx = Depends(require(VIEW))):
    """What a schedule can dump: masters the caller can open, or the MIS reports they can see."""
    if kind == "master":
        from . import masters
        return {"data": [{"code": m["code"], "name": m["name"]} for m in masters.catalog(ctx=ctx)["data"]]}
    if kind in ("mis_report", "mis_book"):
        if not ctx.can("mis.view"):
            raise HTTPException(403, "MIS reports need the mis.view permission.")
        from ..mis_engine import CURRENT_USER
        from ..mis_engine.routes import sf_mis_report as rep
        CURRENT_USER.set(ctx.emp_code)
        return {"data": [{"code": r["slug"], "name": r["name"]} for r in rep.mis_list(None)["reports"]]}
    raise HTTPException(422, "Unknown kind.")


@router.get("/roles")
def roles(ctx: Ctx = Depends(require(VIEW))):
    with db.tx() as cur:
        cur.execute("select code, name from iam.role order by name")
        return {"data": cur.fetchall()}


@router.get("/users")
def users(q: str = "", ctx: Ctx = Depends(require(ADMIN))):
    """Owner picker (admins only)."""
    with db.tx() as cur:
        cur.execute("select id::text id, emp_code, display_name from iam.users where status = 'active' and"
                    " (emp_code ilike %s or display_name ilike %s) order by display_name limit 30",
                    ("%" + q + "%", "%" + q + "%"))
        return {"data": cur.fetchall()}


# ── schedules ──────────────────────────────────────────────────────────────────────────────────────
@router.get("/schedules")
def schedules(owner: str = "visible", ctx: Ctx = Depends(require(VIEW))):
    """owner = visible | mine | <emp code> (admins)."""
    where, binds = _visible_sql(ctx)
    if owner == "mine":
        where += " and s.owner_user_id = %s"
        binds.append(ctx.user_id)
    elif owner not in ("visible", "all", ""):
        where += " and s.owner_emp_code = %s"
        binds.append(owner.upper())
    with db.tx() as cur:
        cur.execute(f"""select s.*, r.status live_status, r.id live_id, r.progress live_progress, r.rows_written live_rows,
                               r.started_at live_started
                          from ops.dump_schedule s
                          left join lateral (select * from ops.dump_run x where x.schedule_id = s.id
                                              and x.status in ('QUEUED','RUNNING') order by x.id desc limit 1) r on true
                         where {where} order by s.name""", binds)
        return envelope([_out(r) for r in cur.fetchall()])


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str
    target: str = Field(min_length=1, max_length=2000)
    config: dict = {}
    format: str = "xlsx"
    folder: str = Field(min_length=3, max_length=400)
    file_name: str | None = Field(default=None, max_length=120)
    cron_expr: str
    timezone: str = "Asia/Kolkata"
    enabled: bool = True
    description: str | None = Field(default=None, max_length=1000)
    start_at: datetime | None = None
    end_at: datetime | None = None
    run_once: bool = False
    timeout_min: int | None = Field(default=None, ge=1, le=600)
    allowed_roles: list[str] = []
    owner_emp_code: str | None = None     # admins only


def _validate(b: ScheduleIn, ctx: Ctx, check_folder=True):
    if b.kind not in engine.KINDS:
        raise HTTPException(422, "Kind must be one of: " + ", ".join(engine.KINDS))
    if b.kind in engine.DFS_KINDS:
        _validate_dfs_kind(b, ctx, check_folder)
        return
    if b.format not in ("csv", "xlsx") or (b.kind == "mis_book" and b.format != "xlsx"):
        raise HTTPException(422, "Format must be csv or xlsx (a workbook is always xlsx).")
    if b.kind == "master" and not ctx.can("masters.export"):
        raise HTTPException(403, "Dumping a master needs the masters.export permission.")
    if b.kind != "master":
        if not ctx.can("mis.export"):
            raise HTTPException(403, "Dumping MIS reports needs the mis.export permission.")
        rule = (b.config or {}).get("dateRule") or "yesterday"
        if rule not in engine.MIS_DATE_RULES + ("fixed",):
            raise HTTPException(422, "Date range must be one of " + ", ".join(engine.MIS_DATE_RULES + ("fixed",)))
        if rule == "fixed" and not all(re.match(r"^\d{4}-\d{2}-\d{2}$", str((b.config or {}).get(k) or ""))
                                       for k in ("from_date", "to_date")):
            raise HTTPException(422, "A fixed date range needs from_date and to_date (YYYY-MM-DD).")
        slugs = [x.strip() for x in b.target.split(",") if x.strip()]
        if not slugs or (b.kind == "mis_report" and len(slugs) != 1) or len(slugs) > 30 or \
                not all(re.match(r"^[a-z0-9][a-z0-9-]{1,60}$", x) for x in slugs):
            raise HTTPException(422, "Pick one MIS report (or 1 to 30 for a workbook).")
    try:
        nxt = engine.next_runs(b.cron_expr, b.timezone, 1, b.start_at)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Frequency (cron) is not valid: {e}")
    if not nxt and not b.run_once:
        raise HTTPException(422, "This frequency never fires.")
    if b.end_at and b.start_at and b.end_at <= b.start_at:
        raise HTTPException(422, "The end must be after the start.")
    if b.file_name and re.search(r'[\\/:*?"<>|]', b.file_name):
        raise HTTPException(422, 'File name cannot contain \\ / : * ? " < > |')
    if not re.match(r"^(\\\\[^\\]+\\[^\\]+|[A-Za-z]:\\)", b.folder.strip()):
        raise HTTPException(422, r"Folder must be a share path (\\server\share\...) or a drive path on the V2Pulse server.")
    if check_folder and not os.path.isdir(b.folder.strip()):
        raise HTTPException(422, "The V2Pulse server cannot open that folder. Check the path, and that the V2Pulse "
                                 "service account may write there.")
    if b.owner_emp_code and not ctx.can(ADMIN):
        raise HTTPException(403, "Only a Dump Scheduler admin can set another owner.")


def _validate_dfs_kind(b: ScheduleIn, ctx: Ctx, check_folder=True):
    """DFS kinds (day trend, inventory report, bin wise, TNA, MIS day load): the config is DFS's own, copied
    unchanged (see pulse/dumps/dfs_kinds.py); only what V2Pulse itself depends on is checked here."""
    perm = "mis.admin" if b.kind == "mis_day_load" else "mis.export"
    if not ctx.can(perm):
        raise HTTPException(403, f"This kind needs the {perm} permission.")
    if b.format not in ("csv", "xlsx"):
        raise HTTPException(422, "Format must be csv or xlsx.")
    try:
        nxt = engine.next_runs(b.cron_expr, b.timezone, 1, b.start_at)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Frequency (cron) is not valid: {e}")
    if not nxt and not b.run_once:
        raise HTTPException(422, "This frequency never fires.")
    if b.owner_emp_code and not ctx.can(ADMIN):
        raise HTTPException(403, "Only a Dump Scheduler admin can set another owner.")
    if b.kind == "mis_day_load":
        return
    if not re.match(r"^(\\\\[^\\]+\\[^\\]+|[A-Za-z]:\\)", b.folder.strip()):
        raise HTTPException(422, r"Folder must be a share path (\\server\share\...) or a drive path on the V2Pulse server.")
    if check_folder and not os.path.isdir(b.folder.strip()):
        raise HTTPException(422, "The V2Pulse server cannot open that folder.")


def _owner(cur, b: ScheduleIn, ctx: Ctx):
    if not b.owner_emp_code or b.owner_emp_code.upper() == ctx.emp_code.upper():
        return ctx.user_id, ctx.emp_code
    cur.execute("select id::text id, emp_code from iam.users where upper(emp_code) = %s and status = 'active'",
                (b.owner_emp_code.upper(),))
    u = cur.fetchone()
    if not u:
        raise HTTPException(422, "No active V2Pulse user with that employee code.")
    return u["id"], u["emp_code"]


def _next(b):
    if not b.enabled:
        return None
    n = engine.next_runs(b.cron_expr, b.timezone, 1, b.start_at, b.end_at)
    return n[0] if n else None


COLS = ("name", "kind", "target", "config", "format", "folder", "file_name", "cron_expr", "timezone", "enabled",
        "description", "start_at", "end_at", "run_once", "timeout_min", "allowed_roles")


def _vals(b: ScheduleIn):
    from psycopg.types.json import Jsonb
    d = b.model_dump()
    d["config"] = Jsonb(d["config"] or {})
    d["folder"] = d["folder"].strip()
    d["target"] = ",".join(x.strip() for x in d["target"].split(",") if x.strip())
    return [d[c] for c in COLS]


@router.post("/schedules")
def create(b: ScheduleIn, request: Request, ctx: Ctx = Depends(require(MANAGE))):
    _validate(b, ctx)
    with db.tx() as cur:
        uid, emp = _owner(cur, b, ctx)
        cur.execute("insert into ops.dump_schedule (%s, owner_user_id, owner_emp_code, created_by, next_run_at)"
                    " values (%s, %%s, %%s, %%s, %%s) returning *" % (", ".join(COLS), ", ".join(["%s"] * len(COLS))),
                    _vals(b) + [uid, emp, ctx.emp_code, _next(b)])
        sc = cur.fetchone()
        audit.action(cur, ctx, "dump.schedule.create", "dump_schedule", str(sc["id"]), b.model_dump(mode="json"), req=request)
    return _out(sc)


@router.put("/schedules/{sid}")
def update(sid: int, b: ScheduleIn, request: Request, ctx: Ctx = Depends(require(MANAGE))):
    with db.tx() as cur:
        sc = _get(cur, sid, ctx)
        _may_change(sc, ctx)
    _validate(b, ctx, check_folder=b.folder.strip() != sc["folder"])
    with db.tx() as cur:
        uid, emp = (sc["owner_user_id"], sc["owner_emp_code"]) if not b.owner_emp_code else _owner(cur, b, ctx)
        cur.execute("update ops.dump_schedule set %s, owner_user_id = %%s, owner_emp_code = %%s, updated_at = now(),"
                    " next_run_at = %%s where id = %%s returning *" % ", ".join("%s = %%s" % c for c in COLS),
                    _vals(b) + [uid, emp, _next(b), sid])
        sc = cur.fetchone()
        audit.action(cur, ctx, "dump.schedule.update", "dump_schedule", str(sid), b.model_dump(mode="json"), req=request)
    return _out(sc)


@router.delete("/schedules/{sid}")
def delete(sid: int, request: Request, ctx: Ctx = Depends(require(MANAGE))):
    with db.tx() as cur:
        sc = _get(cur, sid, ctx)
        _may_change(sc, ctx)
        cur.execute("update ops.dump_run set stop_requested = true where schedule_id = %s and status = 'RUNNING'", (sid,))
        cur.execute("delete from ops.dump_schedule where id = %s", (sid,))
        audit.action(cur, ctx, "dump.schedule.delete", "dump_schedule", str(sid), {"name": sc["name"]}, req=request)
    return {"ok": True}


@router.post("/schedules/{sid}/toggle")
def toggle(sid: int, request: Request, ctx: Ctx = Depends(require(MANAGE))):
    with db.tx() as cur:
        sc = _get(cur, sid, ctx)
        _may_change(sc, ctx)
        on = not sc["enabled"]
        nxt = None
        if on:
            n = engine.next_runs(sc["cron_expr"], sc["timezone"], 1, sc["start_at"], sc["end_at"])
            nxt = n[0] if n else None
        cur.execute("update ops.dump_schedule set enabled = %s, next_run_at = %s, updated_at = now() where id = %s returning *",
                    (on, nxt, sid))
        sc = cur.fetchone()
        audit.action(cur, ctx, "dump.schedule.toggle", "dump_schedule", str(sid), {"enabled": on}, req=request)
    return _out(sc)


@router.post("/schedules/{sid}/run-now")
def run_now(sid: int, request: Request, ctx: Ctx = Depends(require(RUN))):
    """Queue a run; the worker starts it within about 20 seconds."""
    with db.tx() as cur:
        sc = _get(cur, sid, ctx)
        cur.execute("select id, status from ops.dump_run where schedule_id = %s and status in ('QUEUED','RUNNING')", (sid,))
        live = cur.fetchone()
        if live:
            raise HTTPException(409, "This schedule is already %s (run %s)." % (live["status"].lower(), live["id"]))
        cur.execute("insert into ops.dump_run (schedule_id, trigger_type, triggered_by) values (%s, 'manual', %s)"
                    " returning id", (sid, ctx.emp_code))
        rid = cur.fetchone()["id"]
        audit.action(cur, ctx, "dump.run_now", "dump_schedule", str(sid), {"run": rid, "name": sc["name"]}, req=request)
    return {"run_id": rid, "status": "QUEUED"}


@router.post("/schedules/{sid}/stop")
def stop(sid: int, request: Request, ctx: Ctx = Depends(require(RUN))):
    with db.tx() as cur:
        _get(cur, sid, ctx)
        cur.execute("update ops.dump_run set status = case when status = 'QUEUED' then 'STOPPED' else status end,"
                    " completed_at = case when status = 'QUEUED' then now() else completed_at end,"
                    " error_msg = case when status = 'QUEUED' then 'Stopped before it started.' else error_msg end,"
                    " stop_requested = true where schedule_id = %s and status in ('QUEUED','RUNNING') returning id", (sid,))
        ids = [r["id"] for r in cur.fetchall()]
        audit.action(cur, ctx, "dump.stop", "dump_schedule", str(sid), {"runs": ids}, req=request)
    if not ids:
        raise HTTPException(409, "Nothing is running for this schedule.")
    return {"stopping": ids}


# ── runs ───────────────────────────────────────────────────────────────────────────────────────────
RUN_COLS = ("r.id, r.schedule_id, s.name schedule_name, r.trigger_type, r.triggered_by, r.status, r.host, r.queued_at,"
            " r.started_at, r.completed_at, r.duration_ms, r.files, r.rows_written, r.file_paths, r.progress, r.error_msg,"
            " (r.status = 'RUNNING' and r.heartbeat_at < now() - interval '3 minutes') stale")


@router.get("/schedules/{sid}/runs")
def schedule_runs(sid: int, ctx: Ctx = Depends(require(VIEW))):
    with db.tx() as cur:
        _get(cur, sid, ctx)
        cur.execute(f"select {RUN_COLS} from ops.dump_run r join ops.dump_schedule s on s.id = r.schedule_id"
                    " where r.schedule_id = %s order by r.id desc limit 50", (sid,))
        runs = cur.fetchall()
        cur.execute("select run_id, status, mail_to, error_msg, created_at from ops.dump_mail_log where schedule_id = %s"
                    " order by id desc limit 20", (sid,))
        return {"data": runs, "mail": cur.fetchall()}


@router.get("/runs")
def runs(limit: int = 50, owner: str = "visible", ctx: Ctx = Depends(require(VIEW))):
    where, binds = _visible_sql(ctx)
    if owner == "mine":
        where += " and s.owner_user_id = %s"
        binds.append(ctx.user_id)
    with db.tx() as cur:
        cur.execute(f"select {RUN_COLS} from ops.dump_run r join ops.dump_schedule s on s.id = r.schedule_id"
                    f" where {where} order by r.id desc limit %s", [*binds, max(1, min(limit, 200))])
        return {"data": cur.fetchall()}


class Preview(BaseModel):
    cron_expr: str
    timezone: str = "Asia/Kolkata"
    start_at: datetime | None = None
    end_at: datetime | None = None


@router.post("/preview")
def preview(b: Preview, ctx: Ctx = Depends(require(VIEW))):
    try:
        return {"next": [t.isoformat() for t in engine.next_runs(b.cron_expr, b.timezone, 5, b.start_at, b.end_at)],
                "now": datetime.now(timezone.utc).isoformat()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Frequency (cron) is not valid: {e}")
