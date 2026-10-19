"""Snowflake connections (key-pair auth), ported from the Report Portal's db.py.

Two identities:
  profile "pulse" — PULSE_SVC: reads the curated V2RETAIL.PULSE views; session TIMEZONE=UTC.
  profile "ct"    — REPORT_PORTAL_SVC: reused by the Control Tower collector, which expects
                    an IST session exactly as the Report Portal does.

The JWT clock-skew patch is kept: hosts here have run 116–192 s fast, and key-pair tokens
are rejected beyond ~60 s of skew. On a synced host the measured skew is ~0 and it is a no-op.
Every query carries a QUERY_TAG so the Control Tower can attribute cost to V2Pulse."""
import email.utils
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from ..config import settings

_patch_lock = threading.Lock()
_patched_at = 0.0


def _measure_skew(host: str, attempts: int = 4) -> float:
    import requests
    last = None
    for i in range(attempts):
        try:
            t0 = datetime.now(timezone.utc)
            r = requests.head("https://%s/" % host, timeout=20, allow_redirects=False)
            t1 = datetime.now(timezone.utc)
            srv = email.utils.parsedate_to_datetime(r.headers["Date"])
            return ((t0 + (t1 - t0) / 2) - srv).total_seconds()
        except Exception as e:  # noqa: BLE001 - flaky TLS on this network, retry
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("could not reach %s to measure clock skew: %r" % (host, last))


def _patch_clock(host: str):
    global _patched_at
    with _patch_lock:
        if time.time() - _patched_at < 1800:
            return
        import snowflake.connector.auth.keypair as kp
        off = timedelta(seconds=_measure_skew(host) + 5.0)

        class Corrected(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) - off

        kp.datetime = Corrected
        _patched_at = time.time()


def _profile(name: str) -> dict:
    s = settings()
    if name == "mis":
        return {"user": s.sf_user, "key": s.sf_key_path, "role": s.mis_sf_role, "warehouse": s.mis_compute_warehouse,
                "tz": None, "tag": None, "timeout": s.mis_statement_timeout_s}
    if name == "ct":
        return {"user": s.ct_sf_user, "key": s.ct_sf_key_path, "role": s.ct_sf_role, "warehouse": s.ct_sf_warehouse,
                "tz": "Asia/Kolkata", "tag": "report-portal"}
    return {"user": s.sf_user, "key": s.sf_key_path, "role": s.sf_role, "warehouse": s.sf_warehouse,
            "tz": "UTC", "tag": None}


def connect(profile: str = "pulse", tag: dict | None = None, warehouse: str | None = None):
    """`warehouse`: a warehouse name, or for the "mis" profile 'read' / 'compute'."""
    s = settings()
    p = _profile(profile)
    if warehouse:
        named = {"read": s.mis_read_warehouse, "compute": s.mis_compute_warehouse} if profile == "mis" else {}
        p["warehouse"] = named.get(warehouse.lower(), warehouse)
    host = s.sf_account + ".snowflakecomputing.com"
    _patch_clock(host)
    from cryptography.hazmat.primitives import serialization
    import snowflake.connector
    with open(p["key"], "rb") as f:
        pk = serialization.load_pem_private_key(f.read(), password=None)
    der = pk.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    qtag = p["tag"] or json.dumps({"app": "v2pulse", **(tag or {})}, separators=(",", ":"))
    sp = {"QUERY_TAG": qtag, "STATEMENT_TIMEOUT_IN_SECONDS": p.get("timeout", 600)}
    if p["tz"]:   # the MIS profile keeps the account timezone, exactly as DFS does
        sp["TIMEZONE"] = p["tz"]
    return snowflake.connector.connect(
        account=s.sf_account, user=p["user"], private_key=der, role=p["role"], warehouse=p["warehouse"],
        database="V2RETAIL", login_timeout=60, session_parameters=sp,
        # MIS loads and exports run for many minutes: no network timeout, default schema as in DFS
        **({"schema": "BRONZE"} if profile == "mis" else {"network_timeout": 120}))


def query(conn, sql: str, params=None, max_rows: int | None = None) -> tuple[list[dict], str | None]:
    """Run one statement; return (rows as dicts with lower-case keys, Snowflake query id)."""
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        cols = [d[0].lower() for d in cur.description] if cur.description else []
        data = cur.fetchmany(max_rows) if max_rows else cur.fetchall()
        return [dict(zip(cols, r)) for r in data], cur.sfqid
    finally:
        cur.close()
