"""Freshness contracts and snapshot refresh.

A refresh runs the contract's Snowflake query, then validates before anything is replaced:
  * rows >= min_rows
  * row count moved no more than max_swing_pct from the last good snapshot
  * as_of (max of the freshness column, a BUSINESS date, never LAST_ALTERED) did not move
    backwards — the LTAP backward-roll trap — unless allow_backward
A failed check keeps the previous snapshot and records verdict 'rejected' with the reason.
The swap is DELETE + INSERT inside one transaction, so readers see the old rows until commit
and RLS policies / grants on the table are untouched.
"""
import re
from datetime import date, datetime, time, timezone

from .. import audit, db

TARGET_RX = re.compile(r"^snap\.[a-z_][a-z0-9_]*$")


def freshness(as_of: datetime | None, cadence_minutes: int, now: datetime | None = None) -> str:
    """ok within 1.5 cadences (+30 min), stale up to 3 cadences, late beyond."""
    if as_of is None:
        return "unknown"
    now = now or datetime.now(timezone.utc)
    age_min = (now - as_of).total_seconds() / 60
    if age_min <= cadence_minutes * 1.5 + 30:
        return "ok"
    if age_min <= cadence_minutes * 3:
        return "stale"
    return "late"


def _as_dt(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, date):
        return datetime.combine(v, time.min, tzinfo=timezone.utc)
    return None


def validate(contract: dict, rows: list[dict], prev_rows: int | None, prev_as_of: datetime | None):
    """Return (ok, as_of, reason)."""
    n = len(rows)
    col = contract["freshness_column"].lower()
    vals = [_as_dt(r.get(col)) for r in rows]
    vals = [v for v in vals if v is not None]
    as_of = max(vals) if vals else None
    if n < contract["min_rows"]:
        return False, as_of, "Only %d rows; the contract needs at least %d." % (n, contract["min_rows"])
    if as_of is None:
        return False, None, "Freshness column %s is empty in every row." % col
    if prev_rows:
        swing = abs(n - prev_rows) * 100.0 / prev_rows
        if swing > float(contract["max_swing_pct"]):
            return False, as_of, "Row count moved %.1f%% (%d -> %d); the limit is %s%%." % (
                swing, prev_rows, n, contract["max_swing_pct"])
    if prev_as_of and as_of < prev_as_of and not contract["allow_backward"]:
        return False, as_of, "Data date went backwards (%s -> %s)." % (prev_as_of.isoformat(), as_of.isoformat())
    return True, as_of, None


def refresh(code: str, triggered_by: str = "schedule", fetch=None) -> dict:
    """Refresh one dataset. `fetch(contract) -> (rows, sf_query_id)` is injectable for tests."""
    with db.tx(writer=True) as cur:
        cur.execute("select * from data.contract where code = %s", (code,))
        c = cur.fetchone()
        if not c:
            raise KeyError(code)
        if not TARGET_RX.match(c["target_table"]):
            raise ValueError("bad target table %r" % c["target_table"])
        cur.execute("select * from data.dataset_state where dataset = %s", (code,))
        st = cur.fetchone() or {}
        cur.execute("insert into data.refresh (dataset, triggered_by, prev_rows, prev_as_of) values (%s,%s,%s,%s) returning id",
                    (code, triggered_by, st.get("rows"), st.get("as_of")))
        rid = cur.fetchone()["id"]
    try:
        rows, qid = (fetch or _fetch_snowflake)(c)
    except Exception as e:  # noqa: BLE001
        _finish(rid, code, "failed", None, None, None, "Snowflake query failed: %s" % str(e)[:500])
        raise
    ok, as_of, reason = validate(c, rows, st.get("rows"), st.get("as_of"))
    if not ok:
        _finish(rid, code, "rejected", len(rows), as_of, qid, reason)
        return {"verdict": "rejected", "rows": len(rows), "reason": reason}
    try:
        with db.tx(writer=True) as cur:
            cur.execute("select column_name from information_schema.columns where table_schema = 'snap' and table_name = %s"
                        " order by ordinal_position", (c["target_table"].split(".")[1],))
            cols = [r["column_name"] for r in cur.fetchall()]
            cur.execute("delete from %s" % c["target_table"])
            # COPY is refused on tables with row-level security, so insert in batches instead.
            sql = "insert into %s (%s) values (%s)" % (c["target_table"], ", ".join(cols), ", ".join(["%s"] * len(cols)))
            for i in range(0, len(rows), 1000):
                cur.executemany(sql, [tuple(r.get(k) for k in cols) for r in rows[i:i + 1000]])
            cur.execute("insert into data.dataset_state (dataset, as_of, rows, last_ok_at, last_refresh_id) values (%s,%s,%s,now(),%s)"
                        " on conflict (dataset) do update set as_of = excluded.as_of, rows = excluded.rows,"
                        " last_ok_at = now(), last_refresh_id = excluded.last_refresh_id", (code, as_of, len(rows), rid))
            cur.execute("update data.refresh set finished_at = now(), verdict = 'ok', rows = %s, as_of = %s, sf_query_id = %s"
                        " where id = %s", (len(rows), as_of, qid, rid))
            audit.action(cur, None, "data.refresh", "dataset", code, {"rows": len(rows), "as_of": as_of, "by": triggered_by})
    except Exception as e:  # noqa: BLE001 - the old snapshot is intact (transaction rolled back); record why
        _finish(rid, code, "failed", len(rows), as_of, qid, "Load into %s failed: %s" % (c["target_table"], str(e)[:400]))
        raise
    return {"verdict": "ok", "rows": len(rows), "as_of": as_of}


def _finish(rid, code, verdict, rows, as_of, qid, reason):
    with db.tx() as cur:
        cur.execute("update data.refresh set finished_at = now(), verdict = %s, rows = %s, as_of = %s, sf_query_id = %s,"
                    " reason = %s where id = %s", (verdict, rows, as_of, qid, reason, rid))
        audit.action(cur, None, "data.refresh", "dataset", code, {"reason": reason}, outcome="failed")


def _fetch_snowflake(c):
    from . import snowflake as sf
    conn = sf.connect("pulse", {"dataset": c["code"]})
    try:
        return sf.query(conn, c["source_sql"])
    finally:
        conn.close()
