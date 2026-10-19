"""Where scheduled reports are filed: the Scheduled Reports library section (its own sidebar tab), one directory per
department and one folder per report, instead of a 'Scheduled reports' folder inside each department's tree.

A department directory is found by its description, which holds the department tree code (e.g. 'dept:SOURCING'),
so renaming the directory does not break filing. A report and its '· dashboard' companion share one folder."""
import re
import uuid

SCHED_TREE = "custom:scheduledreports"


def _dept_dir(cur, dept_tree):
    cur.execute("select * from nav.folder where section = 'reports' and tree = %s and parent_id is null"
                " and description = %s and not is_archived", (SCHED_TREE, dept_tree))
    f = cur.fetchone()
    if f:
        return f
    name = dept_tree.split(":", 1)[-1]
    cur.execute("savepoint sched_tree_name")
    try:
        cur.execute("select name from nav.tree_name where tree = %s and name is not null limit 1", (dept_tree,))
        r = cur.fetchone()
        if r and r["name"]:
            name = r["name"]
        cur.execute("release savepoint sched_tree_name")
    except Exception:
        cur.execute("rollback to savepoint sched_tree_name")
    fid = uuid.uuid4()
    cur.execute("insert into nav.folder (id, tree, name, description, path, sort_order) values (%s,%s,%s,%s,%s,"
                " (select coalesce(max(sort_order), 0) + 10 from nav.folder where tree = %s and parent_id is null))"
                " returning *", (fid, SCHED_TREE, name, dept_tree, "/%s/" % fid, SCHED_TREE))
    return cur.fetchone()


def scheduled_folder(cur, dept_tree, title):
    """Folder id for a scheduled report: Scheduled Reports > department > report."""
    d = _dept_dir(cur, dept_tree)
    base = re.sub(r"\s*·\s*dashboard\s*$", "", title or "", flags=re.I).strip() or "Report"
    cur.execute("select id from nav.folder where parent_id = %s and lower(name) = lower(%s) and not is_archived limit 1",
                (d["id"], base))
    f = cur.fetchone()
    if f:
        return f["id"]
    fid = uuid.uuid4()
    cur.execute("insert into nav.folder (id, tree, parent_id, name, path, sort_order) values (%s,%s,%s,%s,%s,"
                " (select coalesce(max(sort_order), 0) + 10 from nav.folder where parent_id = %s)) returning id",
                (fid, SCHED_TREE, d["id"], base, (d["path"] or "/%s/" % d["id"]) + "%s/" % fid, d["id"]))
    return cur.fetchone()["id"]
