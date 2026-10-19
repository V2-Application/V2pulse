"""MIS engine: a copy of the DFS (sale-plan) ml-engine MIS routes, mounted in V2Pulse by pulse.modules.mis.

The signed-in V2Pulse user (employee code) is the report owner: pulse.modules.mis sets CURRENT_USER for
each request, and _who() in the copied routes reads it instead of the `user` value the browser sends."""
import contextvars

CURRENT_USER: contextvars.ContextVar = contextvars.ContextVar("mis_current_user", default=None)


def who(u=None):
    me = CURRENT_USER.get()
    return (str(me or u or "").strip() or "unknown")[:120]
