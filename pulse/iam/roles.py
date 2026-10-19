"""Permissions and the system roles. Synced into iam.role at start-up; admins can add
non-system roles later, but system roles are reset from this file on every deploy."""

PERMISSIONS = {
    "home.view": "See My Day",
    "portals.view": "Use the portal directory",
    "portals.manage": "Add and edit portals in the directory",
    "org.stores.view": "See the store directory (within scope)",
    "people.view": "Find colleagues in search (name, code, designation, location, department, work e-mail)",
    "reports.view": "Browse the report library and open reports",
    "reports.manage": "Edit the report catalog",
    "reports.tree.central": "Edit the Central report folder tree",
    "reports.tree.dept": "Edit the report folder tree of own department(s)",
    "reports.tree.all": "Edit every report folder tree",
    "reports.folders.create": "Create directories in the report library tree (portal admin only)",
    "reports.design": "Design dynamic library reports and datasets",
    "reports.personal": "Build personal dynamic reports in My reports",
    "analytics.view": "Open Analytics (hero articles, their stock, gaps and sales)",
    "simulation.view": "Open Simulation (what-if tools such as Store Revival)",
    "masters.view": "Browse the masters library and open masters",
    "masters.view_all": "See every department's masters, not just own department(s)",
    "masters.export": "Export master data to CSV",
    "masters.manage": "Edit the masters catalog (sources, columns, access)",
    "masters.tree.central": "Edit the Central masters folder tree",
    "masters.tree.dept": "Edit the masters folder tree of own department(s)",
    "masters.tree.all": "Edit every masters folder tree",
    "masters.folders.create": "Create directories in the masters library tree (portal admin only)",
    "ct.view": "Control Tower: reports of own department(s)",
    "ct.view_all": "Control Tower: everything, incl. SAP/HRMS loads and DFS",
    "data.view": "See datasets and their freshness",
    "data.refresh": "Trigger a dataset refresh",
    "admin.users.view": "See users, roles and grants",
    "admin.users.manage": "Grant roles and scope, set break-glass passwords",
    "admin.roles.view": "See role definitions",
    "admin.roles.manage": "Create roles, change role permissions and HR auto-assignment rules",
    "admin.announcements.manage": "Publish company announcements",
    "admin.audit.view": "See action and access logs",
    "admin.jobs.view": "See background jobs",
    "admin.jobs.run": "Run a background job now",
    "mis.view": "MIS: open MIS reports, sale / stock trend, day-wise trend, load monitor, dependencies, product master",
    "mis.export": "MIS: export reports (CSV, Excel, workbook, report template)",
    "mis.manage": "MIS: save and delete own MIS reports and day-trend versions, upload report templates",
    "mis.admin": "MIS: load missing days from the monitor, clear the monitor cache, drop orphan report views",
    "dumps.view": "Dump Scheduler: see own schedules, schedules shared with own roles, and their runs",
    "dumps.run": "Dump Scheduler: Run now / Stop",
    "dumps.manage": "Dump Scheduler: create, edit, enable / disable and delete own schedules",
    "dumps.admin": "Dump Scheduler: see and manage every schedule, set any owner",
}

# Permission prefix -> module name, for the admin permission grid.
MODULES = [("home", "Home"), ("people", "People"), ("admin.announcements", "Announcements"), ("portals", "Portals"), ("org", "Stores"), ("reports", "Reports"), ("analytics", "Analytics"), ("simulation", "Simulation"), ("masters", "Masters"),
           ("mis", "MIS"), ("dumps", "Dump Scheduler"),
           ("ct", "Control Tower"), ("data", "Data"), ("admin.users", "Users & access"), ("admin.roles", "Roles & access"),
           ("admin.audit", "Logs"), ("admin.jobs", "Jobs")]


def module_of(perm: str) -> str:
    return next((name for prefix, name in sorted(MODULES, key=lambda m: -len(m[0])) if perm.startswith(prefix + ".")),
                "Other")


ALL = sorted(PERMISSIONS)
BASE = ["home.view", "portals.view", "org.stores.view", "reports.view"]
HO = BASE + ["masters.view", "reports.personal", "people.view"]

# code: (name, description, permissions)
ROLES = {
    "EMPLOYEE": ("Employee", "Every active employee", BASE),
    "PORTAL_ADMIN": ("Portal administrator", "Full control of V2Pulse", ALL),
    "PLATFORM": ("Data platform", "Runs the data platform and the Control Tower",
                 HO + ["ct.view", "ct.view_all", "data.view", "data.refresh", "admin.jobs.view", "admin.jobs.run",
                       "admin.audit.view", "portals.manage", "reports.manage", "reports.tree.all",
                       "masters.view_all", "masters.export", "masters.manage", "masters.tree.all"]),
    "MIS_CENTRAL": ("Central MIS", "Owns the Central layer of MIS, reports and masters",
                    HO + ["ct.view_all", "data.view", "reports.manage", "reports.design", "reports.tree.central",
                          "reports.tree.dept", "masters.view_all", "masters.export", "masters.manage",
                          "masters.tree.central", "masters.tree.dept"]),
    "DEPT_MIS_OWNER": ("Department MIS owner", "Owns a department's reports, masters and folder trees",
                       HO + ["ct.view", "data.view", "reports.tree.dept", "masters.export", "masters.tree.dept"]),
    "EXEC": ("Management", "CMD and leadership: company-wide read",
             HO + ["ct.view_all", "data.view", "masters.view_all", "masters.export"]),
    "AUDITOR": ("Auditor", "Reads every log",
                HO + ["admin.audit.view", "admin.users.view", "admin.roles.view", "masters.view_all"]),
    "HO_STAFF": ("HO staff", "Head-office employees", HO + ["ct.view"]),
    "ZONAL_HEAD": ("Zonal head", "Heads a zone of stores", HO),
    "REGIONAL_HEAD": ("Regional head", "Heads a region of stores", HO),
    "AREA_MGR": ("Area / cluster manager", "Heads a cluster of stores", HO),
    "STORE_MGR": ("Store manager", "Runs a store", BASE + ["reports.personal", "people.view"]),
    "STORE_STAFF": ("Store staff", "Works in a store", BASE),
    "WH_OPS": ("RDC / hub operations", "Works in an RDC or hub", HO),
    "HR": ("HR", "Human resources", HO + ["ct.view", "admin.users.view"]),
    "FINANCE": ("Finance", "Finance and accounts", HO + ["ct.view"]),
    "MERCH": ("Merchandising", "Merchandising and planning", HO + ["ct.view"]),
    "SOURCING": ("Sourcing", "Buying and sourcing", HO + ["ct.view"]),
    "IT_ADMIN": ("IT / admin", "IT and facilities", HO + ["ct.view"]),
}

# Analytics module: the roles its dashboards are published to (hero_catalog.ALL_HERO, plus HR for Karma).
for _code in ("EXEC", "MIS_CENTRAL", "PLATFORM", "DEPT_MIS_OWNER", "HO_STAFF", "MERCH", "SOURCING", "FINANCE",
              "ZONAL_HEAD", "REGIONAL_HEAD", "AREA_MGR", "STORE_MGR", "WH_OPS", "HR"):
    _n, _d, _p = ROLES[_code]
    ROLES[_code] = (_n, _d, _p + ["analytics.view"])

# MIS module (ported from DFS) and the Dump Scheduler.
MIS_USE = ["mis.view", "mis.export", "mis.manage"]
DUMPS_USE = ["dumps.view", "dumps.run", "dumps.manage"]
for _code, _extra in (("PLATFORM", MIS_USE + ["mis.admin"] + DUMPS_USE + ["dumps.admin"]),
                      ("MIS_CENTRAL", MIS_USE + ["mis.admin"] + DUMPS_USE),
                      ("DEPT_MIS_OWNER", MIS_USE + DUMPS_USE),
                      ("MERCH", MIS_USE + DUMPS_USE),
                      ("EXEC", ["mis.view", "mis.export"])):
    _n, _d, _p = ROLES[_code]
    ROLES[_code] = (_n, _d, _p + _extra)

# Roles whose holders get short admin sessions.
ELEVATED = {"PORTAL_ADMIN", "PLATFORM", "AUDITOR"}


# The administrator role always holds every permission; nobody can edit it, so nobody can lock
# everyone out of Admin.
LOCKED = {"PORTAL_ADMIN"}


def sync_roles(cur):
    """Upsert the system roles. A system role an admin has customised keeps its permissions, name and
    description (only its default_permissions follow this file); the others are reset to this file."""
    for code, (name, desc, perms) in ROLES.items():
        perms = sorted(set(perms))
        cur.execute(
            "insert into iam.role (code, name, description, permissions, default_permissions, is_system)"
            " values (%s,%s,%s,%s,%s,true)"
            " on conflict (code) do update set default_permissions = excluded.default_permissions, is_system = true,"
            " name = case when iam.role.is_customized then iam.role.name else excluded.name end,"
            " description = case when iam.role.is_customized then iam.role.description else excluded.description end,"
            " permissions = case when iam.role.is_customized then iam.role.permissions else excluded.permissions end,"
            " updated_at = case when iam.role.is_customized then iam.role.updated_at else now() end",
            (code, name, desc, perms, perms))
    # Custom roles can only hold permissions that still exist.
    cur.execute("update iam.role set permissions = array(select p from unnest(permissions) p where p = any(%s))"
                " where not (permissions <@ %s)", (ALL, ALL))

