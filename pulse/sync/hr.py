"""HR directory and org tree sync: Snowflake PULSE views -> iam.org_node, iam.users, rule grants.

Runs hourly from the worker. Everything happens in one transaction, so a failure leaves
yesterday's directory intact. Safety guard: if more than PULSE_HR_MAX_DEACTIVATE_PCT of the
currently active users would be marked as left, the run aborts without writing — the
HRMS active filter has drifted before (IsApplicant went NULL for everyone in Sep-2026).

Tree built:
  company V2
    zone > region > cluster > store        (V_STORE_HIERARCHY)
    site (HRMS locations that are not stores: HO, RDCs, hubs)
    dept_root DEPT
      dept (HR, SOURCING, WAREHOUSE, ...)  functional departments
        hr_dept (each HRMS department, under its mapped functional dept, or UNASSIGNED)
"""
import json
import re

from ..config import settings
from ..iam.roles import sync_roles

DEPARTMENTS = [  # functional departments; codes match the Report Portal's DEPARTMENTS
    ("HR", "Human Resources"), ("SOURCING", "Sourcing / SRM"), ("WAREHOUSE", "Warehouse & DC"),
    ("MERCH", "Merchandising & Planning"), ("FINANCE", "Finance & Inventory"), ("RETAIL_OPS", "Retail Operations"),
    ("CRM", "CRM & Customer"), ("MARKETING", "Marketing"), ("NSO", "NSO"), ("IT_ADMIN", "IT & Admin"),
    ("PLATFORM", "Data Platform"), ("UNASSIGNED", "Unassigned"),
]

# First-time suggestion for mapping HRMS departments to functional ones; admins correct it after.
DEPT_KEYWORDS = [
    ("HR", r"\bHR\b|HUMAN|PAYROLL|TRAINING|RECRUIT|TALENT"),
    ("FINANCE", r"FINANCE|ACCOUNT|AUDIT|TAX|TREASURY"),
    ("SOURCING", r"SOURCING|PURCHASE|PROCUREMENT|VENDOR|BUYING|QUALITY|QC\b|DESIGN|PRODUCTION"),
    ("WAREHOUSE", r"WAREHOUSE|\bRDC\b|\bDC\b|LOGISTIC|SCM|SUPPLY|DISPATCH|TRANSPORT|INWARD|PICK"),
    ("MERCH", r"MERCH|PLANNING|ALLOCATION|REPLENISH|\bVM\b|VISUAL"),
    ("CRM", r"CRM|MARKETING|CUSTOMER|LOYALTY|DIGITAL|E-?COM"),
    ("IT_ADMIN", r"\bIT\b|INFORMATION TECH|ADMIN|FACILIT|MAINTENANCE|SECURITY|LEGAL|PROJECT"),
    ("PLATFORM", r"\bMIS\b|DATA|ANALYTIC|BUSINESS INTELLIGENCE|\bBI\b"),
    ("RETAIL_OPS", r"RETAIL|STORE|OPERATION|SALES"),
]

DEFAULT_RULES = [  # (priority, role, match, note)
    (10, "HO_STAFF", {"location_codes": ["RH01", "RH02"]}, "Head office locations"),
    (20, "STORE_STAFF", {"location_kind": "store"}, "Works at a store"),
    (30, "WH_OPS", {"location_name_like": "%RDC%"}, "Works at an RDC"),
    (40, "STORE_MGR", {"location_kind": "store", "designation_in": ["SM", "STORE MANAGER"]}, "Store manager (HRMS designation SM)"),
    (50, "AREA_MGR", {"hierarchy_head": "cluster"}, "Named cluster head in the store hierarchy"),
    (60, "REGIONAL_HEAD", {"hierarchy_head": "region"}, "Named region head in the store hierarchy"),
    (70, "ZONAL_HEAD", {"hierarchy_head": "zone"}, "Named zone head in the store hierarchy"),
    (80, "HR", {"functional_dept": "HR", "location_codes": ["RH01", "RH02"]}, "HO staff in an HR department"),
]


class SyncAborted(Exception):
    pass


def fetch_sources() -> dict:
    """Read the four PULSE views from Snowflake (one warehouse session)."""
    from ..data import snowflake as sf
    sch = settings().sf_source_schema
    conn = sf.connect("pulse", {"job": "hr_sync"})
    try:
        out = {}
        for key, view in (("stores", "V_STORE_HIERARCHY"), ("locations", "V_HR_LOCATION"),
                          ("departments", "V_HR_DEPARTMENT"), ("employees", "V_HR_EMPLOYEE")):
            out[key], _ = sf.query(conn, "select * from %s.%s" % (sch, view))
        return out
    finally:
        conn.close()


def seed_static(cur):
    """Roles, default rules and the department skeleton; safe to run on every start."""
    sync_roles(cur)
    cur.execute("select count(*) n from iam.role_rule")
    if cur.fetchone()["n"] == 0:
        for pr, role, match, note in DEFAULT_RULES:
            cur.execute("insert into iam.role_rule (priority, role_code, match, note) values (%s,%s,%s,%s)",
                        (pr, role, json.dumps(match), note))


def _node(cur, node_type, code, name, parent_id, source="sync", attrs=None):
    cur.execute(
        "insert into iam.org_node (node_type, code, name, parent_id, path, source, attrs, is_active)"
        " values (%s,%s,%s,%s,'/',%s,%s,true)"
        " on conflict (node_type, code) do update set name = excluded.name, parent_id = excluded.parent_id,"
        " attrs = excluded.attrs, is_active = true, updated_at = now() returning id",
        (node_type, code, name, parent_id, source, json.dumps(attrs or {})))
    return cur.fetchone()["id"]


def _fix_paths(cur):
    cur.execute("""
        with recursive t as (
          select id, '/' || id::text || '/' as p from iam.org_node where parent_id is null
          union all
          select n.id, t.p || n.id::text || '/' from iam.org_node n join t on n.parent_id = t.id)
        update iam.org_node o set path = t.p from t where o.id = t.id and o.path <> t.p""")


def apply(cur, src: dict) -> dict:
    s = settings()
    seed_static(cur)
    stats = {}
    # ---------------------------------------------------------------- org tree
    company = _node(cur, "company", "V2", "V2 Retail", None)
    zones, regions, clusters, store_codes = {}, {}, {}, set()
    heads = []  # (emp_code, node_id, level)
    for r in src["stores"]:
        z, rg, cl, st = r["zone_code"], r["region_code"], r["cluster_code"], r["store_code"]
        if not st:
            continue
        if z and z not in zones:
            zones[z] = _node(cur, "zone", z, z, company)
        zp = zones.get(z, company)
        if rg and rg not in regions:
            regions[rg] = _node(cur, "region", rg, rg, zp)
        rp = regions.get(rg, zp)
        if cl and cl not in clusters:
            clusters[cl] = _node(cur, "cluster", cl, cl, rp)
        cp = clusters.get(cl, rp)
        _node(cur, "store", st, r["store_name"] or st, cp, attrs={"state": r.get("state")})
        store_codes.add(st)
        for level, key, nid in (("zone", "zone_head_ecode", zones.get(z)), ("region", "region_head_ecode", regions.get(rg)),
                                ("cluster", "cluster_head_ecode", clusters.get(cl))):
            if r.get(key) and nid:
                heads.append((r[key], nid, level))
    cur.execute("update iam.org_node set is_active = false where node_type = 'store' and source = 'sync'"
                " and not (code = any(%s))", (sorted(store_codes),))
    loc_kind = {}
    for loc in src["locations"]:
        code = (loc["location_code"] or "").strip().upper()
        if not code:
            continue
        if code in store_codes:
            loc_kind[code] = "store"
            continue
        loc_kind[code] = "site"
        _node(cur, "site", code, loc["location_name"] or code, company)
    root = _node(cur, "dept_root", "DEPT", "Departments", company)
    dept_ids = {code: _node(cur, "dept", code, name, root) for code, name in DEPARTMENTS}
    for d in src["departments"]:
        cur.execute("insert into iam.dept_map (hr_department_id, hr_department_name, portal_dept_code) values (%s,%s,%s)"
                    " on conflict (hr_department_id) do update set hr_department_name = excluded.hr_department_name",
                    (d["department_id"], d["department_name"] or str(d["department_id"]), _suggest(d["department_name"])))
    cur.execute("select * from iam.dept_map")
    hr_dept_node, hr_dept_func = {}, {}
    for m in cur.fetchall():
        func = m["portal_dept_code"] if m["portal_dept_code"] in dept_ids else "UNASSIGNED"
        hr_dept_node[m["hr_department_id"]] = _node(cur, "hr_dept", str(m["hr_department_id"]), m["hr_department_name"],
                                                    dept_ids[func])
        hr_dept_func[m["hr_department_id"]] = func
    _fix_paths(cur)
    cur.execute("select id, node_type, code from iam.org_node where node_type in ('store','site') and is_active")
    loc_node = {r["code"]: r["id"] for r in cur.fetchall()}
    stats["stores"], stats["sites"] = len(store_codes), sum(1 for v in loc_kind.values() if v == "site")

    # ---------------------------------------------------------------- users
    emps = [e for e in src["employees"] if e.get("emp_code")]
    active = {e["emp_code"]: e for e in emps if e["is_active"]}
    cur.execute("select emp_code from iam.users where source = 'hrms' and status = 'active'")
    current_active = {r["emp_code"] for r in cur.fetchall()}
    leaving = current_active - set(active)
    if current_active and len(leaving) > len(current_active) * s.hr_max_deactivate_pct / 100:
        raise SyncAborted("%d of %d active users would be marked as left (limit %.1f%%). Check the HRMS active filter."
                          % (len(leaving), len(current_active), s.hr_max_deactivate_pct))
    cur.execute("create temp table _emp (emp_code text primary key, display_name text, email text, mobile text,"
                " designation text, hr_department text, hr_department_id int, location_code text, location_name text,"
                " reports_to text, home_node_id uuid, dept_node_id uuid) on commit drop")
    with cur.copy("copy _emp from stdin") as cp:
        for e in active.values():
            loc = (e.get("location_code") or "").strip().upper() or None
            dep = e.get("department_id")
            cp.write_row((e["emp_code"], (e.get("display_name") or e["emp_code"]).strip()[:200], e.get("email"), e.get("mobile"),
                          e.get("designation"), e.get("department_name"), dep, loc, e.get("location_name"), e.get("reports_to"),
                          loc_node.get(loc), hr_dept_node.get(dep)))
    # An email shared by two employees (HRMS has placeholders like abc@gmail.com on many rows) cannot
    # identify anyone at sign-in: drop it. Same for an email an admin has locked onto someone else.
    cur.execute("update _emp set email = null where lower(email) in (select lower(email) from _emp where email is not null"
                " group by 1 having count(*) > 1)")
    stats["shared_emails_dropped"] = cur.rowcount
    cur.execute("update _emp e set email = null where e.email is not null and exists (select 1 from iam.users u"
                " where lower(u.email) = lower(e.email) and u.emp_code <> e.emp_code and u.status = 'active'"
                " and (u.email_locked or not exists (select 1 from _emp x where x.emp_code = u.emp_code)))")
    # Clear emails that are about to change first, so two people swapping addresses cannot collide mid-statement.
    cur.execute("update iam.users u set email = null from _emp e where e.emp_code = u.emp_code and not u.email_locked"
                " and lower(coalesce(u.email, '')) <> lower(coalesce(e.email, ''))")
    cur.execute("""
        insert into iam.users as u (emp_code, display_name, email, mobile, designation, hr_department, hr_department_id,
            location_code, location_name, reports_to_code, home_node_id, dept_node_id, status, source, last_hr_sync_at)
        select e.emp_code, e.display_name, e.email, e.mobile, e.designation, e.hr_department, e.hr_department_id,
               e.location_code, e.location_name, e.reports_to, e.home_node_id, e.dept_node_id, 'active', 'hrms', now()
        from _emp e
        on conflict (emp_code) do update set display_name = excluded.display_name,
            email = case when u.email_locked then u.email else excluded.email end,
            mobile = excluded.mobile, designation = excluded.designation, hr_department = excluded.hr_department,
            hr_department_id = excluded.hr_department_id, location_code = excluded.location_code,
            location_name = excluded.location_name, reports_to_code = excluded.reports_to_code,
            home_node_id = excluded.home_node_id, dept_node_id = excluded.dept_node_id,
            status = case when u.status = 'suspended' then 'suspended' else 'active' end,
            last_hr_sync_at = now(), updated_at = now()""")
    stats["active_users"] = len(active)
    cur.execute("update iam.users set status = 'left', updated_at = now() where source = 'hrms' and status <> 'left'"
                " and emp_code = any(%s) returning id", (sorted(leaving),))
    left_ids = [r["id"] for r in cur.fetchall()]
    if left_ids:
        cur.execute("update iam.session set revoked_at = now() where user_id = any(%s) and revoked_at is null", (left_ids,))
    stats["marked_left"] = len(left_ids)

    # ---------------------------------------------------------------- rule-based roles and scope
    cur.execute("select id, emp_code, designation, location_code, location_name, hr_department_id from iam.users"
                " where status = 'active' and source = 'hrms'")
    users = cur.fetchall()
    head_map = {}
    for code, nid, level in heads:
        head_map.setdefault(code, []).append((nid, level))
    cur.execute("select * from iam.role_rule where is_active order by priority")
    rules = cur.fetchall()
    want_roles, want_scopes = set(), set()
    for u in users:
        kind = loc_kind.get(u["location_code"] or "", None)
        func = hr_dept_func.get(u["hr_department_id"])
        levels = {lv for _, lv in head_map.get(u["emp_code"], [])}
        for r in rules:
            if _matches(r["match"], u, kind, func, levels):
                want_roles.add((u["id"], r["role_code"]))
        for nid, _ in head_map.get(u["emp_code"], []):
            want_scopes.add((u["id"], nid))
    cur.execute("create temp table _wr (user_id uuid, role_code text) on commit drop")
    with cur.copy("copy _wr from stdin") as cp:
        for row in want_roles:
            cp.write_row(row)
    cur.execute("delete from iam.user_role ur where ur.source = 'rule'"
                " and not exists (select 1 from _wr w where w.user_id = ur.user_id and w.role_code = ur.role_code)")
    stats["rule_roles_removed"] = cur.rowcount
    cur.execute("insert into iam.user_role (user_id, role_code, source, reason) select user_id, role_code, 'rule', 'HR rule'"
                " from _wr on conflict (user_id, role_code, source) do nothing")
    stats["rule_roles_added"] = cur.rowcount
    cur.execute("create temp table _ws (user_id uuid, node_id uuid) on commit drop")
    with cur.copy("copy _ws from stdin") as cp:
        for row in want_scopes:
            cp.write_row(row)
    cur.execute("delete from iam.scope_grant g where g.source = 'rule'"
                " and not exists (select 1 from _ws w where w.user_id = g.user_id and w.node_id = g.node_id)")
    cur.execute("insert into iam.scope_grant (user_id, node_id, source, reason) select user_id, node_id, 'rule',"
                " 'Named head in the store hierarchy' from _ws on conflict (user_id, node_id, source) do nothing")
    stats["hierarchy_heads"] = len({u for u, _ in want_scopes})

    # ---------------------------------------------------------------- bootstrap admins
    for code in [c.strip().upper() for c in s.bootstrap_admins.split(",") if c.strip()]:
        cur.execute("select id from iam.users where emp_code = %s and status = 'active'", (code,))
        row = cur.fetchone()
        if row:
            cur.execute("insert into iam.user_role (user_id, role_code, source, reason) values (%s,'PORTAL_ADMIN','manual',"
                        "'Bootstrap administrator (PULSE_BOOTSTRAP_ADMINS)') on conflict do nothing", (row["id"],))
            cur.execute("insert into iam.scope_grant (user_id, node_id, source, reason) values (%s,%s,'manual',"
                        "'Bootstrap administrator') on conflict do nothing", (row["id"], company))
    return stats


def _suggest(name: str | None) -> str | None:
    n = (name or "").upper()
    for code, pat in DEPT_KEYWORDS:
        if re.search(pat, n):
            return code
    return None


def _like(pattern: str, value: str | None) -> bool:
    if value is None:
        return False
    rx = "^" + re.escape(pattern.upper()).replace("%", ".*").replace("_", ".") + "$"
    return re.match(rx, value.upper()) is not None


def _matches(m: dict, u: dict, kind, func, head_levels) -> bool:
    """All conditions in a rule must hold."""
    if "location_codes" in m and (u["location_code"] or "") not in m["location_codes"]:
        return False
    if "location_kind" in m and kind != m["location_kind"]:
        return False
    if "location_name_like" in m and not _like(m["location_name_like"], u["location_name"]):
        return False
    if "designation_like" in m and not _like(m["designation_like"], u["designation"]):
        return False
    if "designation_in" in m and (u["designation"] or "").strip().upper() not in m["designation_in"]:
        return False
    if "functional_dept" in m and func != m["functional_dept"]:
        return False
    if "hierarchy_head" in m and m["hierarchy_head"] not in head_levels:
        return False
    return True


RULE_KEYS = {"location_codes", "location_kind", "location_name_like", "designation_like", "designation_in",
             "functional_dept", "hierarchy_head"}


def rule_matches(cur, match: dict, limit: int = 20) -> dict:
    """Who a rule would match right now, from the last HR sync (for the admin rule editor)."""
    cur.execute("select code, node_type from iam.org_node where node_type in ('store','site') and is_active")
    loc_kind = {r["code"]: r["node_type"] for r in cur.fetchall()}
    cur.execute("select hr_department_id, coalesce(portal_dept_code, 'UNASSIGNED') f from iam.dept_map")
    func = {r["hr_department_id"]: r["f"] for r in cur.fetchall()}
    cur.execute("select g.user_id, n.node_type from iam.scope_grant g join iam.org_node n on n.id = g.node_id"
                " where g.source = 'rule' and n.node_type in ('zone','region','cluster')")
    levels = {}
    for r in cur.fetchall():
        levels.setdefault(r["user_id"], set()).add(r["node_type"])
    cur.execute("select id, emp_code, display_name, designation, location_code, location_name, hr_department_id"
                " from iam.users where status = 'active' and source = 'hrms' order by emp_code")
    hits = [u for u in cur.fetchall()
            if _matches(match, u, loc_kind.get(u["location_code"] or ""), func.get(u["hr_department_id"]),
                        levels.get(u["id"], set()))]
    return {"count": len(hits), "sample": [{"emp_code": u["emp_code"], "display_name": u["display_name"],
                                            "designation": u["designation"], "location_code": u["location_code"]}
                                           for u in hits[:limit]]}


def run(cur_factory=None) -> dict:
    """Entry point for the worker: fetch, then apply in one transaction, then log."""
    from .. import audit, db
    src = fetch_sources()
    with db.tx(writer=True) as cur:
        stats = apply(cur, src)
        audit.action(cur, None, "sync.hr", "job", "hr_sync", stats)
    return stats
