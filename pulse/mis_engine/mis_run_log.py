"""
mis_run_log — writes BRONZE.MIS_LOAD_RUN, the durable record of every day-wise MIS load.

WHY THIS IS A MODULE AND NOT FOUR LINES IN THE LOAD ENDPOINT
────────────────────────────────────────────────────────────
Two rules have to hold at every call site, and both are easy to forget once and never notice:

  1. A row is written BEFORE the CALL, not after. The stock proc over a wide range is the one that
     dies without returning, and a log that only records finished work records exactly nothing
     about the runs worth investigating. RUNNING rows are what V_MIS_LOAD_RUN_STALE reads.

  2. Failing to LOG must never fail the LOAD. A Snowflake hiccup on an INSERT into a log table
     would otherwise abort a load that was about to succeed - trading the thing that matters for
     the thing that describes it. Every function here swallows its own errors and warns, the same
     contract planning-service/src/db/aksOpsLog.js holds for AKS_OPS.

TIMESTAMPS ARE TZ-AWARE, WRITTEN FROM PYTHON
────────────────────────────────────────────
Never CURRENT_TIMESTAMP(). This account's session timezone is America/Los_Angeles while the
business runs on IST, so a proc-side default would store 03:14 for a 15:44 IST load and every
reader would be half a day wrong - the defect AKS_OPS.PROMOTE_LOG has and that
frontend/src/components/LastLoaded.jsx has to compensate for by hand. The column is TIMESTAMP_TZ
and the value carries its offset, so no reader has to know a convention. See db-scripts/305.

THE OWN-CONNECTION RULE
───────────────────────
Each write opens and closes its own connection rather than borrowing the caller's. The caller's
cursor is mid-CALL on a proc that can run for minutes; issuing an INSERT on that same connection
would either serialise behind it or, on the finish path after an exception, run on a connection
whose state is exactly what just failed.
"""
import logging
import re
import socket
import uuid
from datetime import datetime, timezone

from .worker.auto_gandola_sf import get_sf_connection

logger = logging.getLogger(__name__)

RUN_TBL = "V2RETAIL.BRONZE.MIS_LOAD_RUN"
HOST = socket.gethostname()

# The procs end with "...: 12345 deleted, 67890 inserted". Parsed into columns because "inserted 0"
# is the single most important fact this log records and it has to be filterable, not buried in
# prose. The prose is kept anyway in PROC_MSG - the parse is best-effort and must never be the
# only copy.
#
# THE INSERT COUNT IS A SUM, because not every proc reports one number. The STOCK loader breaks its
# inserts down by source:
#
#   "23200090 deleted, 19879478 LGORT + 150772 PRD + 1814250 LQ(LGTYP) + 1333910 LQL(LGORT)
#    + 21677 LQB(bin group) inserted over 1 reported day(s), 116 tag(s)"
#
# The old pattern demanded "<digits> deleted, <digits> inserted" with nothing between, so STOCK has
# logged NULL deleted/inserted ever since db-scripts/302 split the message that way - the monitor
# showed a dash for every stock load while sale showed numbers. 313/314 added more components and
# made it more obvious, but did not cause it.
#
# So: take the digits before "deleted", then SUM every number between "deleted," and "inserted".
# Anything after "inserted" is excluded on purpose - the sale message ends "(incl. 9467 return
# lines)", which is a subset of what was inserted, not an addition to it.
_DEL_RE = re.compile(r"(\d+)\s+deleted", re.I)
_INS_SEG_RE = re.compile(r"deleted,\s*(.*?)\binserted\b", re.I | re.S)
_NUM_RE = re.compile(r"\d+")


def new_id():
    return uuid.uuid4().hex[:32]


def _q(v):
    return "'" + str(v).replace("'", "''") + "'"


def _lit(v):
    """SQL literal for a value that may be absent. NULL, never '' - the two mean different things
    in STORES, where NULL is the procs' own 'every store'."""
    return "NULL" if v is None else _q(v)


def _num(v):
    return "NULL" if v is None else str(int(v))


def parse_counts(msg):
    """('12345 deleted, 678 inserted') -> (12345, 678).

    Handles the multi-source insert shape too: '9 deleted, 4 A + 5 B inserted' -> (9, 9). Either
    half returns None on its own if that part of the message is missing, so a proc that reports one
    and not the other still logs what it did say.
    """
    s = str(msg or "")
    m_del = _DEL_RE.search(s)
    n_del = int(m_del.group(1)) if m_del else None

    n_ins = None
    seg = _INS_SEG_RE.search(s)
    if seg:
        nums = _NUM_RE.findall(seg.group(1))
        if nums:
            n_ins = sum(int(x) for x in nums)
    return n_del, n_ins


def _exec(label, sql):
    """Run one statement on its own connection. Never raises - see the module docstring."""
    conn = None
    try:
        conn = get_sf_connection()
        conn.cursor().execute(sql)
        return True
    except Exception as e:                                    # noqa: BLE001 - deliberate
        logger.warning("[mis-run-log] %s failed: %s", label, e)
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                                 # noqa: BLE001
                pass


def start(*, run_group, kind, from_dt, to_dt, stores, triggered_by, operator=None):
    """Write the RUNNING row and return its RUN_ID (or None if the write failed).

    `stores` is the list the caller resolved; an EMPTY list means every store, which is what the
    procs' P_STORE=NULL means, and is stored as NULL rather than '' so the two cannot be confused.
    """
    run_id = new_id()
    n_days = (datetime.fromisoformat(to_dt) - datetime.fromisoformat(from_dt)).days + 1
    st_list = ",".join(stores) if stores else None
    started = datetime.now(timezone.utc).isoformat()
    ok = _exec("start", f"""
        INSERT INTO {RUN_TBL}
          (RUN_ID, RUN_GROUP, KIND, FROM_DT, TO_DT, N_DAYS, STORES, N_STORES,
           TRIGGERED_BY, OPERATOR, STATUS, STARTED_AT, HOST_NAME)
        SELECT {_q(run_id)}, {_q(run_group)}, {_q(kind)},
               {_q(from_dt)}::DATE, {_q(to_dt)}::DATE, {n_days},
               {_lit(st_list)}, {len(stores or [])},
               {_q(triggered_by)}, {_lit(operator)}, 'RUNNING',
               {_q(started)}::TIMESTAMP_TZ, {_q(HOST)}""")
    return run_id if ok else None


def finish(run_id, *, status, proc_msg=None, error=None, started_ms=None):
    """Close a RUNNING row. A no-op when `run_id` is None - i.e. when start() could not write, in
    which case the load still went ahead and simply has no log row, which is the correct trade."""
    if not run_id:
        return
    n_del, n_ins = parse_counts(proc_msg)
    ms = None if started_ms is None else int(started_ms)
    _exec("finish", f"""
        UPDATE {RUN_TBL}
           SET STATUS = {_q(status)},
               ROWS_DELETED = {_num(n_del)},
               ROWS_INSERTED = {_num(n_ins)},
               PROC_MSG = {_lit((str(proc_msg)[:1000]) if proc_msg else None)},
               ERROR_MSG = {_lit((str(error)[:4000]) if error else None)},
               FINISHED_AT = {_q(datetime.now(timezone.utc).isoformat())}::TIMESTAMP_TZ,
               DURATION_MS = {_num(ms)}
         WHERE RUN_ID = {_q(run_id)}""")
