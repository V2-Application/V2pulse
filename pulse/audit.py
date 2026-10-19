"""Log management: every action and every view.

Two append-only logs (see db/migrations/0001_core.sql):

* audit.event  — ACTIONS. Written inside the request's own transaction with `action()`, so a
  change and its log row commit together or not at all. Refusals and failures, whose
  transaction rolls back, are written with `action_now()` in a separate transaction.
* audit.access — VIEWS. Every API request (middleware) and every SPA page view (beacon).
  Buffered in memory and flushed in batches by a background thread, so logging never
  adds a database round-trip to a request. If the buffer is full the row is written
  synchronously instead of dropped: completeness matters more than latency here.
"""
import json
import queue
import threading
import time
from datetime import datetime, timezone

from . import db

SENSITIVE_KEYS = {"password", "new_password", "token", "secret", "client_secret", "code_verifier", "csrf"}


def _clean(details: dict | None) -> dict:
    """Never let a secret reach the log."""
    if not details:
        return {}
    out = {}
    for k, v in details.items():
        if k.lower() in SENSITIVE_KEYS:
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = _clean(v)
        else:
            out[k] = v
    return out


def action(cur, ctx, action_name: str, object_type: str | None = None, object_id=None,
           details: dict | None = None, outcome: str = "ok", req=None):
    """Record an action inside the caller's transaction."""
    actor_id = getattr(ctx, "user_id", None) if ctx else None
    actor_code = getattr(ctx, "emp_code", None) if ctx else None
    cur.execute(
        "insert into audit.event (request_id, actor_id, actor_emp_code, action, outcome, object_type, object_id, ip, user_agent, details)"
        " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (_req_id(req), actor_id, actor_code, action_name, outcome, object_type,
         None if object_id is None else str(object_id), _ip(req), _ua(req), json.dumps(_clean(details), default=str)))


def action_now(ctx, action_name: str, object_type=None, object_id=None, details=None, outcome="ok", req=None,
               actor_emp_code: str | None = None):
    """Record an action in its own transaction (refusals, failures, background jobs)."""
    class _Anon:
        user_id = None
        emp_code = actor_emp_code
    with db.tx() as cur:
        action(cur, ctx or _Anon(), action_name, object_type, object_id, details, outcome, req)


def _req_id(req):
    return getattr(getattr(req, "state", None), "request_id", None) if req is not None else None


def _ip(req):
    return getattr(getattr(req, "state", None), "client_ip", None) if req is not None else None


def _ua(req):
    if req is None:
        return None
    return (req.headers.get("user-agent") or "")[:400] or None


# ------------------------------------------------------------------ access log writer
_COLS = ("ts", "request_id", "actor_id", "actor_emp_code", "kind", "method", "path", "query", "status", "ms",
         "ip", "user_agent", "session_id")
_SQL = "insert into audit.access (%s) values (%s)" % (", ".join(_COLS), ", ".join(["%s"] * len(_COLS)))


class AccessLog:
    def __init__(self, max_buffer: int = 20000, flush_every: float = 1.0):
        self.q: queue.Queue = queue.Queue(maxsize=max_buffer)
        self.flush_every = flush_every
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.written = 0
        self.sync_writes = 0

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="access-log", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.flush()

    def record(self, *, kind, path, method=None, query=None, status=None, ms=None, request_id=None,
               actor_id=None, actor_emp_code=None, ip=None, user_agent=None, session_id=None):
        row = (datetime.now(timezone.utc), request_id, actor_id, actor_emp_code, kind, method, path[:500],
               (query or None) and query[:1000], status, ms, ip, (user_agent or "")[:400] or None, session_id)
        try:
            self.q.put_nowait(row)
        except queue.Full:
            self._write([row])
            self.sync_writes += 1

    def flush(self):
        batch = []
        while True:
            try:
                batch.append(self.q.get_nowait())
            except queue.Empty:
                break
            if len(batch) >= 2000:
                self._write(batch)
                batch = []
        if batch:
            self._write(batch)

    def _write(self, batch):
        try:
            with db.pool().connection() as conn:
                with conn.cursor() as cur:
                    cur.executemany(_SQL, batch)
            self.written += len(batch)
        except Exception as e:  # noqa: BLE001 - logging must never take the app down
            print("access log write failed (%d rows): %s" % (len(batch), e), flush=True)

    def _run(self):
        while not self._stop.is_set():
            time.sleep(self.flush_every)
            self.flush()


access_log = AccessLog()
