"""Postgres access. Every request runs in one transaction whose scope is set with
SET LOCAL, so row-level security sees it and a pooled connection never carries a
previous caller's scope."""
from contextlib import contextmanager
from dataclasses import dataclass, field

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        # A stale or wedged connection must fail fast instead of stalling a request: check each connection
        # before handing it out, recycle idle and old ones, wait at most 20 s for one, and let Postgres end any
        # statement after 2 minutes and any transaction left idle for 5 minutes.
        _pool = ConnectionPool(settings().database_url, min_size=2, max_size=settings().db_pool_max,
                               kwargs={"row_factory": dict_row, "keepalives": 1, "keepalives_idle": 60,
                                       "options": "-c statement_timeout=120000 -c idle_in_transaction_session_timeout=300000"},
                               check=ConnectionPool.check_connection, timeout=20, max_idle=300, max_lifetime=3600,
                               open=True)
    return _pool


def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@dataclass
class Scope:
    all: bool = False
    sites: list[str] = field(default_factory=list)


@contextmanager
def tx(scope: Scope | None = None, writer: bool = False):
    """Yield a cursor inside one transaction with the caller's scope applied."""
    with pool().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                s = scope or Scope()
                cur.execute("select set_config('pulse.scope_all', %s, true), set_config('pulse.scope_sites', %s, true),"
                            " set_config('pulse.writer', %s, true)",
                            ("on" if s.all else "off", ",".join(s.sites), "on" if writer else "off"))
                yield cur


def one(cur, sql, params=None):
    cur.execute(sql, params)
    return cur.fetchone()


def rows(cur, sql, params=None):
    cur.execute(sql, params)
    return cur.fetchall()
