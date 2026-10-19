"""Control Tower: the Report Portal's status engine, moved into V2Pulse.

The worker runs the vendored collector on a schedule and stores each snapshot in
ops.ct_snapshot (last 20 kept). Pages read the latest snapshot — they never wake Snowflake.
`ct.view` sees reports of the caller's own departments; `ct.view_all` sees everything,
including SAP / HRMS loads and DFS jobs. Task actions (run, suspend) stay on the Report
Portal in Phase 0 and are linked from here."""
import json
import os
import sys
import threading

from fastapi import APIRouter, Depends, HTTPException

from .. import audit, db
from ..iam.context import Ctx
from ..web import envelope, require

router = APIRouter(prefix="/api/v1/control-tower")
_LEGACY = os.path.join(os.path.dirname(__file__), "ct_legacy")
_cache = {"id": None, "payload": None, "taken_at": None}
_cache_lock = threading.Lock()
SEVERITY = {"FAILED": 5, "STALE": 4, "SUSPENDED": 3, "WARN": 3, "RUNNING": 2, "OK": 1, "UNMONITORED": 0}


def _collector():
    if _LEGACY not in sys.path:
        sys.path.insert(0, _LEGACY)
    import collector  # noqa: E402  (vendored, imports its siblings by bare name)
    return collector


def refresh() -> dict:
    """Worker job: collect, store, register reports in the library."""
    col = _collector()
    c = col.Collector()
    ok = c.refresh(reason="v2pulse")
    try:
        import db as legacy_db  # the shim
        legacy_db.close_shared()
    except Exception:  # noqa: BLE001
        pass
    snap = c.snapshot
    payload = {k: snap.get(k) for k in ("refreshed_at", "refresh_seconds", "reports", "tasks", "departments", "sync", "dfs",
                                        "error")}
    with db.tx() as cur:
        cur.execute("insert into ops.ct_snapshot (payload, refresh_seconds, error) values (%s,%s,%s) returning id",
                    (json.dumps(payload, default=str), snap.get("refresh_seconds"), None if ok else snap.get("error")))
        sid = cur.fetchone()["id"]
        cur.execute("delete from ops.ct_snapshot where id < %s - 20", (sid,))
        made = 0
        if ok:
            from .reports import sync_ct_reports
            made = sync_ct_reports(cur, payload["reports"] or [])
    if not ok:
        raise RuntimeError(snap.get("error") or "collector failed")
    return {"reports": len(payload["reports"] or []), "tasks": len(payload["tasks"] or {}), "new_library_entries": made}


def latest():
    with db.tx() as cur:
        cur.execute("select id, taken_at from ops.ct_snapshot where error is null order by id desc limit 1")
        head = cur.fetchone()
        if not head:
            return None, None
        with _cache_lock:
            if _cache["id"] == head["id"]:
                return _cache["payload"], _cache["taken_at"]
        cur.execute("select payload from ops.ct_snapshot where id = %s", (head["id"],))
        payload = cur.fetchone()["payload"]
    with _cache_lock:
        _cache.update(id=head["id"], payload=payload, taken_at=head["taken_at"])
    return payload, head["taken_at"]


def _need():
    p, t = latest()
    if p is None:
        raise HTTPException(503, "The Control Tower has not collected its first snapshot yet.")
    return p, t


def _visible_reports(p, ctx: Ctx):
    reps = p.get("reports") or []
    if ctx.can("ct.view_all"):
        return reps
    return [r for r in reps if (r.get("department") or "").upper() in ctx.depts]


def _meta(t, ctx):
    from ..data.contracts import freshness
    return {"as_of": t.isoformat(), "freshness": freshness(t, 30), "dataset": "control_tower", "scope": ctx.scope_label}


def _counts(reps):
    out = {}
    for r in reps:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


@router.get("/summary")
def summary(ctx: Ctx = Depends(require("ct.view", "ct.view_all"))):
    p, t = _need()
    reps = _visible_reports(p, ctx)
    depts = {d["code"]: d["name"] for d in p.get("departments") or []}
    by = {}
    for r in reps:
        by.setdefault(r["department"], []).append(r)
    data = {"counts_by_status": _counts(reps), "refreshed_at": p.get("refreshed_at"), "error": p.get("error"),
            "by_department": [{"code": k, "name": depts.get(k, k), "counts": _counts(v)} for k, v in sorted(by.items())],
            "loads": {"lanes": (p.get("sync") or {}).get("lanes", [])} if ctx.can("ct.view_all") else None}
    return envelope(data, **_meta(t, ctx))


@router.get("/reports")
def reports(department: str = "", status: str = "", ctx: Ctx = Depends(require("ct.view", "ct.view_all"))):
    p, t = _need()
    reps = [r for r in _visible_reports(p, ctx)
            if (not department or r["department"] == department) and (not status or r["status"] == status)]
    keep = ("id", "name", "department", "status", "reasons", "owner", "runner", "schedule_note", "freshness",
            "max_age_hours", "last_run", "next_run", "tasks", "description")
    reps = sorted(reps, key=lambda r: (-SEVERITY.get(r["status"], 0), r["department"], r["name"]))
    return envelope([{k: r.get(k) for k in keep} for r in reps], **_meta(t, ctx))


@router.get("/reports/{rid}")
def report(rid: str, ctx: Ctx = Depends(require("ct.view", "ct.view_all"))):
    p, t = _need()
    r = next((x for x in _visible_reports(p, ctx) if x["id"] == rid), None)
    if not r:
        raise HTTPException(404, "Report not found.")
    tasks = p.get("tasks") or {}
    details = []
    for fqn in r.get("tasks") or []:
        tk = tasks.get(fqn)
        if tk:
            details.append({k: tk.get(k) for k in ("fqn", "name", "state", "health", "schedule_text", "last_run", "next_run",
                                                    "success_rate", "avg_duration_s")} | {"runs": (tk.get("runs") or [])[:30]})
    return envelope({**r, "task_details": details}, **_meta(t, ctx))


@router.get("/loads")
def loads(ctx: Ctx = Depends(require("ct.view_all"))):
    p, t = _need()
    s = p.get("sync") or {}
    tables = [{k: x.get(k) for k in ("lane", "lane_name", "table", "status", "reason", "last_start", "last_end", "last_ok",
                                     "rows_24h", "success_rate_7d", "mode", "late_by")} for x in s.get("tables") or []]
    return envelope({"lanes": s.get("lanes") or [], "tables": tables, "error": s.get("error")}, **_meta(t, ctx))


@router.get("/dfs")
def dfs(ctx: Ctx = Depends(require("ct.view_all"))):
    p, t = _need()
    d = p.get("dfs") or {}
    return envelope({"reports": d.get("reports") or [], "jobs": d.get("jobs") or [], "error": d.get("error")}, **_meta(t, ctx))
