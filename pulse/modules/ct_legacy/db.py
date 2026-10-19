"""Shim: the Report Portal collector's db module, backed by V2Pulse's Snowflake connector."""
import threading

from pulse.data import snowflake as _sf

PORTAL_SCHEMA = "V2RETAIL.REPORT_PORTAL"
_conn = None
_lock = threading.Lock()


def connect():
    return _sf.connect("ct")


def _rows(cur):
    cols = [d[0].lower() for d in cur.description] if cur.description else []
    return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []


def run(sql, params=None, conn=None):
    global _conn
    if conn is None:
        with _lock:
            if _conn is None or _conn.is_closed():
                _conn = connect()
            conn = _conn
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        return _rows(cur)
    finally:
        cur.close()


def close_shared():
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001
                pass
            _conn = None


def quote_fqn(fqn):
    parts = fqn.split(".")
    if len(parts) != 3:
        raise ValueError("expected DB.SCHEMA.NAME, got %r" % fqn)
    return ".".join('"%s"' % p.replace('"', '""') for p in parts)
