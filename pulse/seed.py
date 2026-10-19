"""Idempotent start-up seed: roles, rules, departments, portals, datasets, report queries, jobs."""
import json
import uuid

from .config import settings
from .modules import bi, hero_catalog, masters, portals
from .sync.hr import DEPARTMENTS, seed_static

SRC = None  # filled from settings at call time

JOBS = [  # code, name, schedule
    ("hr_sync", "HR directory and org tree from HRMS", "every:60m"),
    ("refresh:store_directory", "Store directory snapshot", "daily:07:40"),
    # One minute after the Report Portal's own refreshes (03:00, 07:30, 14:00, 20:00 IST), while
    # COMPUTE_WH is still awake: the Control Tower then costs no extra warehouse wake-ups.
    ("ct_refresh", "Control Tower snapshot (Report Portal collector)", "daily:03:01,07:31,14:01,20:01"),
    ("portal_health", "Portal up/down checks", "every:10m"),
    ("masters_profile", "Masters: row counts and columns from Snowflake", "daily:07:45"),
    ("bi_prewarm", "Dynamic reports: pre-run library reports so they open from cache (08:00-22:00 IST)", "every:25m"),
    ("audit_partitions", "Create next months' log partitions, drop expired ones", "daily:01:10"),
    ("housekeeping", "Expire caches, sign-in states and old sessions", "daily:01:20"),
    ("db_backup", "Nightly database backup (pg_dump, 14 days kept)", "daily:02:00"),
    ("dump_dispatch", "Dump Scheduler: start due and requested dumps (runs them in the background)", "every:1m"),
]


def contracts(sch: str):
    return [{
        "code": "store_directory", "name": "Store directory", "owner_dept": "PLATFORM",
        "description": "Every store with its zone, region and cluster and the named area heads.",
        "source_object": sch + ".V_STORE_HIERARCHY (Retail Hub STORE_HIERARCHY)",
        "source_sql": ("select store_code, store_name, state, zone_code, region_code, cluster_code, cluster_head_ecode,"
                       " cluster_head_name, region_head_ecode, zone_head_ecode, synced_at from %s.V_STORE_HIERARCHY" % sch),
        "freshness_column": "synced_at", "target_table": "snap.store_directory", "cadence_minutes": 1440,
        "min_rows": 300, "max_swing_pct": 20,
    }]


def query_defs(sch: str):
    return [{
        "code": "org.store_hierarchy", "name": "Store hierarchy (live)",
        "sql_template": ("select store_code, store_name, state, zone_code, region_code, cluster_code, cluster_head_ecode,"
                         " cluster_head_name, synced_at from %s.V_STORE_HIERARCHY where {scope}"
                         " and (%%(zone)s is null or zone_code = %%(zone)s) order by store_code" % sch),
        "params": {"zone": {"type": "str", "required": False, "max_len": 20}},
        "scope_column": "store_code", "permission": "reports.view", "cache_seconds": 1800, "max_rows": 5000,
    }]


def run(cur):
    s = settings()
    # Two web workers and the worker service all seed at start-up; serialise them.
    cur.execute("select pg_advisory_xact_lock(7140001)")
    seed_static(cur)
    company = _ensure_skeleton(cur)
    portals.seed(cur)
    for c in contracts(s.sf_source_schema):
        cols = list(c)
        cur.execute("insert into data.contract (%s) values (%s) on conflict (code) do update set %s, updated_at = now()"
                    % (", ".join(cols), ", ".join(["%s"] * len(cols)),
                       ", ".join("%s = excluded.%s" % (k, k) for k in cols if k != "code")), [c[k] for k in cols])
    for q in query_defs(s.sf_source_schema):
        cols = list(q)
        cur.execute("insert into data.query_def (%s) values (%s) on conflict (code) do update set %s"
                    % (", ".join(cols), ", ".join(["%s"] * len(cols)),
                       ", ".join("%s = excluded.%s" % (k, k) for k in cols if k != "code")),
                    [json.dumps(q[k]) if k == "params" else q[k] for k in cols])
    masters.seed(cur)
    hero_catalog.seed(cur)   # before bi.seed: the hero datasets carry their own cache and audience
    bi.seed(cur)
    for code, name, sched in JOBS:
        cur.execute("insert into ops.job (code, name, schedule, next_run_at) values (%s,%s,%s,now())"
                    " on conflict (code) do update set name = excluded.name", (code, name, sched))
    cur.execute("insert into rpt.report (code, name, description, layer, owner_dept, kind, query_code)"
                " values ('store-hierarchy', 'Store hierarchy', 'Every store you can see, with its zone, region, cluster and"
                " cluster head. Opens as a table.', 'central', 'PLATFORM', 'table', 'org.store_hierarchy')"
                " on conflict (code) do nothing returning id")
    new = cur.fetchone()
    if new:
        f = _root_folder(cur, "central", "Company-wide", "Reports certified for the whole company.")
        cur.execute("insert into nav.item (folder_id, kind, report_id) values (%s,'report',%s)", (f, new["id"]))


def _ensure_skeleton(cur):
    cur.execute("select id from iam.org_node where node_type = 'company'")
    row = cur.fetchone()
    if row:
        return row["id"]
    cid = uuid.uuid4()
    cur.execute("insert into iam.org_node (id, node_type, code, name, path, source) values (%s,'company','V2','V2 Retail',%s,'sync')",
                (cid, "/%s/" % cid))
    rid = uuid.uuid4()
    cur.execute("insert into iam.org_node (id, node_type, code, name, parent_id, path, source)"
                " values (%s,'dept_root','DEPT','Departments',%s,%s,'sync')", (rid, cid, "/%s/%s/" % (cid, rid)))
    for code, name in DEPARTMENTS:
        did = uuid.uuid4()
        cur.execute("insert into iam.org_node (id, node_type, code, name, parent_id, path, source)"
                    " values (%s,'dept',%s,%s,%s,%s,'sync')", (did, code, name, rid, "/%s/%s/%s/" % (cid, rid, did)))
    return cid


def _root_folder(cur, tree, name, desc):
    cur.execute("select id from nav.folder where section = 'reports' and tree = %s and parent_id is null"
                " and lower(name) = lower(%s) and not is_archived", (tree, name))
    row = cur.fetchone()
    if row:
        return row["id"]
    fid = uuid.uuid4()
    cur.execute("insert into nav.folder (id, tree, name, description, path, sort_order) values (%s,%s,%s,%s,%s,10)",
                (fid, tree, name, desc, "/%s/" % fid))
    return fid
