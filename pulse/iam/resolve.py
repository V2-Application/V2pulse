"""Resolve a user's effective roles, permissions, data classes and scope.

Scope = the user's home node, their HR department node, and every live scope grant, each
expanded to its subtree. A grant on the company node means the whole company (scope.all).
Store / site codes in the subtree become the RLS list; functional departments come from
the dept nodes in the subtree (an HR department's functional department is its parent)."""
from ..db import Scope
from .context import Ctx


def load_ctx(cur, user_id: str, session_id: str | None = None, provider: str = "") -> Ctx | None:
    cur.execute("select * from iam.users where id = %s and status = 'active'", (user_id,))
    u = cur.fetchone()
    if not u:
        return None
    cur.execute(
        "select r.code, r.permissions from iam.user_role ur join iam.role r on r.code = ur.role_code"
        " where ur.user_id = %s and ur.valid_from <= now() and (ur.valid_to is null or ur.valid_to > now())", (user_id,))
    granted = cur.fetchall()
    cur.execute("select permissions from iam.role where code = 'EMPLOYEE'")
    emp = cur.fetchone()
    roles = sorted({"EMPLOYEE", *[g["code"] for g in granted]})
    perms = set(emp["permissions"] if emp else [])
    for g in granted:
        perms.update(g["permissions"])
    cur.execute("select data_class from iam.data_class_grant where user_id = %s and (valid_to is null or valid_to > now())",
                (user_id,))
    classes = {r["data_class"] for r in cur.fetchall()}

    cur.execute(
        "select n.* from iam.org_node n where n.is_active and n.id in ("
        "  select home_node_id from iam.users where id = %(u)s"
        "  union select dept_node_id from iam.users where id = %(u)s"
        "  union select node_id from iam.scope_grant where user_id = %(u)s and (valid_to is null or valid_to > now()))",
        {"u": user_id})
    roots = cur.fetchall()
    scope, label, store_count, depts = expand(cur, roots)
    return Ctx(user_id=str(u["id"]), emp_code=u["emp_code"], display_name=u["display_name"], session_id=session_id,
               provider=provider, roles=roles, permissions=perms, scope=scope,
               scope_nodes=[{"id": str(r["id"]), "type": r["node_type"], "code": r["code"], "name": r["name"]} for r in roots],
               scope_label=label, store_count=store_count, depts=depts, data_classes=classes, user=u)


def expand(cur, roots):
    """roots -> (Scope, label, store_count, functional departments)."""
    if any(r["node_type"] == "company" for r in roots):
        cur.execute("select count(*) n from iam.org_node where node_type = 'store' and is_active")
        n = cur.fetchone()["n"]
        cur.execute("select code from iam.org_node where node_type = 'dept' and is_active order by code")
        return Scope(all=True), "Company · %d stores" % n, n, [r["code"] for r in cur.fetchall()]
    if not roots:
        return Scope(), "No data scope", 0, []
    cur.execute(
        "select distinct n.node_type, n.code from iam.org_node n"
        " where n.is_active and n.node_type in ('store','site','dept') and n.path like any(%s)",
        ([r["path"] + "%" for r in roots],))
    found = cur.fetchall()
    depts = {f["code"] for f in found if f["node_type"] == "dept"}
    hr_parents = [r["parent_id"] for r in roots if r["node_type"] == "hr_dept" and r["parent_id"]]
    if hr_parents:
        cur.execute("select code from iam.org_node where node_type = 'dept' and id = any(%s)", (hr_parents,))
        depts.update(r["code"] for r in cur.fetchall())
    sites = sorted({f["code"] for f in found if f["node_type"] in ("store", "site")})
    stores = sum(1 for f in found if f["node_type"] == "store")
    data_roots = [r for r in roots if r["node_type"] not in ("dept_root", "dept", "hr_dept")]
    if len(data_roots) == 1:
        r = data_roots[0]
        kind = {"zone": "Zone", "region": "Region", "cluster": "Cluster", "store": "Store", "site": "Site"}.get(r["node_type"], "")
        label = "%s %s" % (kind, r["name"] if r["node_type"] in ("store", "site") else r["code"])
    elif data_roots:
        label = "%d areas" % len(data_roots)
    else:
        label = "Department only"
    if stores and not (len(data_roots) == 1 and data_roots[0]["node_type"] == "store"):
        label += " · %d store%s" % (stores, "" if stores == 1 else "s")
    return Scope(all=False, sites=sites), label, stores, sorted(depts)
