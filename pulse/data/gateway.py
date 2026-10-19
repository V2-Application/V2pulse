"""Query gateway for on-demand report tables (tier T2).

Only queries registered in data.query_def run, with typed parameters bound by the driver.
The caller's scope is injected server-side through the {scope} placeholder; a module cannot
widen it. Results are cached in Postgres per (query, params, scope) for cache_seconds, so
200 managers opening the same report cost one Snowflake query. Paging and sorting are served
from the cached result. Snowflake executions are rate-limited per user; cache hits are free.
"""
import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from .. import db
from ..db import Scope

RATE_PER_HOUR = 30
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$.\"]*$")


class GatewayError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


def bind_params(spec: dict, given: dict) -> dict:
    out = {}
    for name, p in spec.items():
        v = given.get(name)
        if v in (None, ""):
            if p.get("required"):
                raise GatewayError(422, "Parameter %s is required." % name)
            out[name] = p.get("default")
            continue
        t = p.get("type", "str")
        try:
            if t == "int":
                v = int(v)
            elif t == "date":
                v = date.fromisoformat(str(v))
            else:
                v = str(v)
                if len(v) > int(p.get("max_len", 100)):
                    raise GatewayError(422, "Parameter %s is too long." % name)
        except ValueError:
            raise GatewayError(422, "Parameter %s must be a %s." % (name, t))
        if "choices" in p and v not in p["choices"]:
            raise GatewayError(422, "Parameter %s must be one of %s." % (name, ", ".join(map(str, p["choices"]))))
        out[name] = v
    return out


def scope_sql(column: str | None, scope: Scope) -> tuple[str, dict]:
    """The predicate that replaces {scope}, with its own bind values."""
    if not column or scope.all:
        return "1=1", {}
    if not IDENT.match(column):
        raise GatewayError(500, "Bad scope column in query definition.")
    if not scope.sites:
        return "1=0", {}
    binds = {"__s%d" % i: s for i, s in enumerate(scope.sites)}
    return "upper(%s) in (%s)" % (column, ", ".join("%%(%s)s" % k for k in binds)), binds


def _jsonable(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, bytes):
        return v.hex()
    return v


def run(qdef: dict, params: dict, scope: Scope, user_id: str, executor=None) -> dict:
    """Return {"columns", "rows", "as_of", "cache_hit"}; executor(sql, binds, max_rows) is injectable for tests."""
    bound = bind_params(qdef.get("params") or {}, params)
    pred, sbind = scope_sql(qdef.get("scope_column"), scope)
    if "{scope}" not in qdef["sql_template"] and qdef.get("scope_column"):
        raise GatewayError(500, "Query definition is scoped but has no {scope} placeholder.")
    sql = qdef["sql_template"].replace("{scope}", pred)
    key_src = json.dumps([qdef["code"], {k: _jsonable(v) for k, v in bound.items()},
                          "ALL" if scope.all else scope.sites], sort_keys=True)
    key = hashlib.sha256(key_src.encode()).hexdigest()
    with db.tx() as cur:
        cur.execute("select result, created_at from data.query_cache where key = %s and expires_at > now()", (key,))
        hit = cur.fetchone()
    if hit:
        _log(user_id, qdef["code"], bound, len(hit["result"]["rows"]), 0, True, None, None)
        return {**hit["result"], "as_of": hit["created_at"].isoformat(), "cache_hit": True}
    _rate_limit(user_id)
    t0 = time.perf_counter()
    try:
        rows, qid = (executor or _execute)(sql, {**bound, **sbind}, int(qdef.get("max_rows") or 50000), qdef["code"])
    except Exception as e:  # noqa: BLE001
        _log(user_id, qdef["code"], bound, None, int((time.perf_counter() - t0) * 1000), False, None, str(e)[:500])
        raise GatewayError(502, "The report query failed. The data team has the details (trace in the query log).")
    ms = int((time.perf_counter() - t0) * 1000)
    columns = list(rows[0].keys()) if rows else []
    result = {"columns": columns, "rows": [[_jsonable(r[c]) for c in columns] for r in rows]}
    now = datetime.now(timezone.utc)
    with db.tx() as cur:
        cur.execute("insert into data.query_cache (key, result, created_at, expires_at) values (%s,%s,%s,%s)"
                    " on conflict (key) do update set result = excluded.result, created_at = excluded.created_at,"
                    " expires_at = excluded.expires_at",
                    (key, json.dumps(result), now, now + timedelta(seconds=int(qdef.get("cache_seconds") or 300))))
    _log(user_id, qdef["code"], bound, len(rows), ms, False, qid, None)
    return {**result, "as_of": now.isoformat(), "cache_hit": False}


def _rate_limit(user_id, bucket="t2", limit=RATE_PER_HOUR):
    window = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    with db.tx() as cur:
        cur.execute("insert into data.rate_bucket (user_id, bucket, window_start, count) values (%s,%s,%s,1)"
                    " on conflict (user_id, bucket, window_start) do update set count = data.rate_bucket.count + 1"
                    " returning count", (user_id, bucket, window))
        if cur.fetchone()["count"] > limit:
            raise GatewayError(429, "You have run %d live queries this hour. Try again later; cached results "
                                    "still open." % limit)


def run_sql(code: str, sql: str, binds: dict, key_parts, user_id: str, cache_seconds: int = 300, max_rows: int = 50000,
            bucket: str = "t2", limit: int = RATE_PER_HOUR, executor=None) -> dict:
    """Run a statement built by a module (identifiers already validated, values in binds) through the same
    cache, rate limit and query log as registered queries. key_parts must capture everything that changes
    the result, including the caller's scope."""
    key = hashlib.sha256(json.dumps([code, key_parts], sort_keys=True, default=str).encode()).hexdigest()
    with db.tx() as cur:
        cur.execute("select result, created_at from data.query_cache where key = %s and expires_at > now()", (key,))
        hit = cur.fetchone()
    if hit:
        _log(user_id, code, {"key": key_parts}, len(hit["result"]["rows"]), 0, True, None, None)
        return {**hit["result"], "as_of": hit["created_at"].isoformat(), "cache_hit": True}
    if user_id is not None:   # background jobs (pre-warming) are not rate-limited
        _rate_limit(user_id, bucket, limit)
    t0 = time.perf_counter()
    try:
        rows, qid = (executor or _execute)(sql, binds, max_rows, code)
    except Exception as e:  # noqa: BLE001
        _log(user_id, code, {"key": key_parts}, None, int((time.perf_counter() - t0) * 1000), False, None, str(e)[:500])
        raise GatewayError(502, _friendly(e))
    ms = int((time.perf_counter() - t0) * 1000)
    columns = list(rows[0].keys()) if rows else []
    result = {"columns": columns, "rows": [[_jsonable(r[c]) for c in columns] for r in rows]}
    now = datetime.now(timezone.utc)
    if cache_seconds > 0:
        with db.tx() as cur:
            cur.execute("insert into data.query_cache (key, result, created_at, expires_at) values (%s,%s,%s,%s)"
                        " on conflict (key) do update set result = excluded.result, created_at = excluded.created_at,"
                        " expires_at = excluded.expires_at", (key, json.dumps(result), now, now + timedelta(seconds=cache_seconds)))
    _log(user_id, code, {"key": key_parts}, len(rows), ms, False, qid, None)
    return {**result, "as_of": now.isoformat(), "cache_hit": False}


def _friendly(e: Exception) -> str:
    msg = str(e)
    if "does not exist or not authorized" in msg:
        return "This data set is not set up in Snowflake yet (its view is missing). The data team has been told."
    return "The query failed. The data team has the details (trace in the query log)."


def _log(user_id, code, params, rows, ms, hit, qid, error):
    with db.tx() as cur:
        cur.execute("insert into data.query_log (user_id, query_code, params, rows, ms, cache_hit, sf_query_id, error)"
                    " values (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (user_id, code, json.dumps({k: _jsonable(v) for k, v in params.items()}), rows, ms, hit, qid, error))


class _SnowflakePool:
    """Reusable PULSE_SVC connections, at most SIZE in use per process. Reusing a connection skips the
    key-pair sign-in on every query, and the cap stops one report page (dozens of visuals) from flooding
    the shared warehouse queue; extra queries wait here briefly instead of queueing in Snowflake."""
    SIZE = 6
    MAX_AGE = 45 * 60

    def __init__(self):
        import threading
        self._sem = threading.BoundedSemaphore(self.SIZE)
        self._lock = threading.Lock()
        self._idle: list[tuple[float, object]] = []

    def run(self, fn):
        from . import snowflake as sf
        if not self._sem.acquire(timeout=40):
            raise RuntimeError("Too many report queries are running right now; try again in a moment.")
        conn = None
        try:
            with self._lock:
                while self._idle and conn is None:
                    born, c = self._idle.pop()
                    if time.time() - born < self.MAX_AGE and not c.is_closed():
                        conn, conn_born = c, born
                    else:
                        _close(c)
            if conn is None:
                conn, conn_born = sf.connect("pulse", {"pool": True}), time.time()
            try:
                out = fn(conn)
            except Exception:
                _close(conn)
                conn = None
                raise
            return out
        finally:
            if conn is not None:
                with self._lock:
                    self._idle.append((conn_born, conn))
            self._sem.release()


def _close(c):
    try:
        c.close()
    except Exception:  # noqa: BLE001
        pass


SF_POOL = _SnowflakePool()


def _execute(sql, binds, max_rows, code):
    from . import snowflake as sf
    return SF_POOL.run(lambda conn: sf.query(conn, sql, binds, max_rows=max_rows))
