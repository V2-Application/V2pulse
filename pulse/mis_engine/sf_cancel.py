"""Cancel a Snowflake query when the HTTP client that asked for it goes away.

WHY THIS EXISTS
---------------
A FastAPI handler that runs a long Snowflake query keeps running it after the browser has
hung up. Nothing in the stack propagates the disconnect: uvicorn notices and Starlette
notices, but the worker thread is blocked inside ``cursor.execute()`` and the query itself
lives server-side in Snowflake, where the HTTP request was only ever the thing that
started it.

That stays invisible until a page RE-FIRES. ``SfMisReports.jsx`` reruns its report on every
change to [slug, from, to, page, sortCol, sortDir, conds, layout] and aborts the previous
request with an AbortController - which cancels the BROWSER request only. MEASURED
2026-09-14: 84 such runs from 59 separate sessions inside one hour, each leaving a
COUNT(*) + page + totals triple executing on AKS_WH_L against DAY_WISE_ST_VAR_SALE
(49-85 KB of SQL apiece) with nobody left to read the answer. The warehouse reached
7 running / 40 queued, and a 21-second COPY from an unrelated bulk upload sat in that queue
for 512 seconds. In the worst hour the warehouse spent 1,491 minutes queued against 119
minutes of execution - almost entirely doing work no one wanted.

HOW IT CANCELS
--------------
From a SEPARATE connection: ``SELECT SYSTEM$CANCEL_ALL_QUERIES(<session id>)``.

Not ``cursor.abort_query()`` and not the connector's private ``__cancel_query``: both want the
connection the query is running on, and that connection is blocked inside execute() in another
thread. A second short-lived session has no such problem.

BY SESSION, NOT BY QUERY ID - AND WHY THAT IS NOT A BIG HAMMER
--------------------------------------------------------------
The obvious form is SYSTEM$CANCEL_QUERY(cursor.sfqid), and it does not work. MEASURED on
snowflake-connector-python 3.7.1: ``cursor.sfqid`` stays None for the whole time a statement
is running and is only populated once it RETURNS. A cancel path that reads it therefore always
finds None and cancels nothing - a `SELECT SYSTEM$WAIT(120)` under test ran its full 120
seconds with the cancel firing at 12s and reporting "no query id yet". Polling for the id does
not help; the id is not late, it is absent until the thing you wanted to stop has stopped.

``connection.session_id`` is known the instant the connection opens, so cancelling by session
has no race at all. It is not over-broad either: every guarded handler here opens its OWN
connection per request (``base._conn()``) and closes it in a finally, so "all queries on this
session" is exactly "the queries this one abandoned request started", and nothing else. The
guard refuses to bind a second connection for this reason - if a body ever did use two, the
one tracked would be cancelled and the other silently left running, which is the failure this
module exists to prevent.

``track()`` additionally refuses to start a NEW statement once the client is known to be gone,
so a body that runs three statements in sequence cannot slip the second one past the
disconnect while the cancel is in flight.

WHAT THE CALLER SEES
--------------------
Nothing. The client has already disconnected, so the ClientGone raised here is never
delivered; it exists to unwind the handler and to leave one line in the log saying which
query was dropped and why.
"""
from __future__ import annotations

import asyncio
import logging
import threading

logger = logging.getLogger(__name__)

# How often to ask Starlette whether the client is still there. The check is a cheap poll of
# the receive channel, but there is no point doing it faster than a human can change a filter.
POLL_SECONDS = 1.0


class ClientGone(Exception):
    """The client disconnected before the work finished. Carries no message: nobody reads it."""


class QueryGuard:
    """Tracks the SESSION a worker thread is querying on so the event loop can abort it.

    One guard per request. The worker thread calls ``execute()``; the event loop calls
    ``cancel()``. The lock covers only the few instructions that swap the tracked session and
    the cancelled flag - never the query itself, which would defeat the point.
    """

    def __init__(self, label: str = "query"):
        self.label = label
        self._lock = threading.Lock()
        self._session_id = None
        self._cancelled = False
        self.cancelled_session = None

    # -- worker thread side --------------------------------------------------
    def track(self, cur):
        """Register the session that is about to run a statement.

        Raises ClientGone if the client has ALREADY left, which is what stops a
        three-statement body from starting statements two and three into the void.
        """
        sid = getattr(getattr(cur, "connection", None), "session_id", None)
        with self._lock:
            if self._cancelled:
                raise ClientGone()
            if sid and self._session_id and sid != self._session_id:
                # See the module docstring: a second connection would be left running.
                raise RuntimeError(
                    f"{self.label}: guarded body used two Snowflake sessions "
                    f"({self._session_id} then {sid}); only one can be cancelled")
            if sid:
                self._session_id = sid

    def execute(self, cur, sql, *args, **kwargs):
        """``cur.execute(sql)``, registered so it can be cancelled.

        Use this everywhere inside a guarded body - a plain ``cur.execute()`` still leaks,
        because nothing else knows which session to stop.
        """
        self.track(cur)
        return cur.execute(sql, *args, **kwargs)

    # -- event loop side -----------------------------------------------------
    def cancel(self, conn_factory):
        """Abort whatever this request has running. Returns the session id it cancelled, if any.

        Never raises: this runs on the disconnect path, where the request is already lost and
        a failure to cancel must not become a second failure.
        """
        with self._lock:
            already = self._cancelled
            self._cancelled = True
            sid = self._session_id
        if already or not sid:
            # No session yet means nothing has been submitted; the flag set above makes the
            # body raise ClientGone at its next execute() instead.
            return None

        try:
            conn = conn_factory()
        except Exception as e:                      # noqa: BLE001 - see the docstring
            logger.warning("[sf-cancel] %s: could not open a session to cancel %s: %s",
                           self.label, sid, e)
            return None
        try:
            c = conn.cursor()
            c.execute(f"SELECT SYSTEM$CANCEL_ALL_QUERIES({int(sid)})")
            logger.info("[sf-cancel] %s: client disconnected - cancelled session %s (%s)",
                        self.label, sid, (c.fetchone() or [""])[0])
            self.cancelled_session = sid
            return sid
        except Exception as e:                      # noqa: BLE001
            logger.warning("[sf-cancel] %s: SYSTEM$CANCEL_ALL_QUERIES(%s) failed: %s",
                           self.label, sid, e)
            return None
        finally:
            try:
                conn.close()
            except Exception:                       # noqa: BLE001
                pass


async def run_cancellable(request, fn, conn_factory, *, label: str = "query"):
    """Run the blocking ``fn(guard)`` in a worker thread, cancelling its Snowflake query if
    ``request`` disconnects first.

    ``fn`` must route every statement through ``guard.execute(cur, sql)`` - a body that calls
    ``cur.execute()`` directly still leaks, because nothing else knows which query to stop.
    """
    guard = QueryGuard(label)
    task = asyncio.create_task(asyncio.to_thread(fn, guard))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=POLL_SECONDS)
            if done:
                return task.result()
            if await request.is_disconnected():
                # Cancel on a thread: opening a session and waiting for sfqid both block, and
                # this event loop is serving every other request in the process.
                await asyncio.to_thread(guard.cancel, conn_factory)
                # Let the worker unwind - its `finally` is what returns the connection.
                try:
                    await task
                except Exception:                   # noqa: BLE001 - the client is gone
                    pass
                raise ClientGone()
    except asyncio.CancelledError:
        # The server is shutting the request down (reload, timeout). Same obligation.
        await asyncio.to_thread(guard.cancel, conn_factory)
        raise


async def guarded(request, fn, conn_factory, *, label: str = "query"):
    """``run_cancellable`` with the disconnect turned into a response instead of a traceback.

    499 is nginx's "client closed request" - the conventional code for this, and one no
    browser will ever see, since by definition the socket it would arrive on is closed. The
    point is to keep an abandoned request out of the error log, where it would otherwise look
    like a fault in the report.
    """
    from starlette.responses import Response
    try:
        return await run_cancellable(request, fn, conn_factory, label=label)
    except ClientGone:
        return Response(status_code=499)
