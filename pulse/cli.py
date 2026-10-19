"""Server-side admin commands (run over RDP from D:\Mahesh\Python\v2pulse after `call env.bat`).

    venv\Scripts\python.exe -m pulse.cli set-password V23375     # prompts; break-glass password
    venv\Scripts\python.exe -m pulse.cli make-admin RTNR63 "NIKHIL VIG" RH01 HO-NEW "ASST HEAD"   # admin outside the HR sync
    venv\Scripts\python.exe -m pulse.cli run-job hr_sync           # run a job now, print the result
    venv\Scripts\python.exe -m pulse.cli masters-sql > deploy\snowflake_masters.sql   # Part C views
    venv\Scripts\python.exe -m pulse.cli bi-sql > deploy\snowflake_bi.sql             # Part D views
    venv\Scripts\python.exe -m pulse.cli views-sql > deploy\V2Pulse_views_ONE_STATEMENT.sql   # all views, one statement
"""
import getpass
import sys

from . import audit, db
from .auth import passwords, sessions


def set_password(code: str):
    pw = getpass.getpass("New password for %s: " % code)
    if pw != getpass.getpass("Again: "):
        sys.exit("Passwords differ.")
    msg = passwords.problem(pw)
    if msg:
        sys.exit(msg)
    with db.tx() as cur:
        cur.execute("select id from iam.users where emp_code = %s and status = 'active'", (code.upper(),))
        row = cur.fetchone()
        if not row:
            sys.exit("No active user %s (has the HR sync run?)." % code)
        cur.execute("insert into iam.local_credential (user_id, password_hash) values (%s,%s) on conflict (user_id)"
                    " do update set password_hash = excluded.password_hash, failed_count = 0, locked_until = null,"
                    " updated_at = now()", (row["id"], passwords.hash_password(pw)))
        sessions.revoke_user(cur, row["id"])
        audit.action(cur, None, "admin.password.set", "user", code.upper(), {"via": "server cli"})
    print("Password set for %s." % code.upper())


def make_admin(code: str, name: str, location_code: str = "RH01", location_name: str = "HO-NEW", designation: str = ""):
    """PORTAL_ADMIN with company scope for someone the HR sync does not bring in (e.g. HRMS has a stale DateOfLeft).
    The user is kept as source 'manual', so the HR sync never marks them as left."""
    code = code.strip().upper()
    with db.tx(writer=True) as cur:
        cur.execute("select id from iam.org_node where node_type = 'company' and code = 'V2'")
        company = cur.fetchone()
        if not company:
            sys.exit("No company node yet (has the HR sync run?).")
        cur.execute("select id from iam.org_node where node_type in ('store','site') and code = %s and is_active",
                    (location_code.upper(),))
        home = cur.fetchone()
        cur.execute("""
            insert into iam.users as u (emp_code, display_name, designation, location_code, location_name, home_node_id,
                status, source)
            values (%s,%s,%s,%s,%s,%s,'active','manual')
            on conflict (emp_code) do update set status = 'active', source = 'manual',
                display_name = coalesce(nullif(u.display_name, ''), excluded.display_name), updated_at = now()
            returning id""", (code, name, designation or None, location_code.upper(), location_name, home and home["id"]))
        uid = cur.fetchone()["id"]
        cur.execute("insert into iam.user_role (user_id, role_code, source, reason) values (%s,'PORTAL_ADMIN','manual',"
                    "'Granted from server cli') on conflict (user_id, role_code, source) do update set valid_to = null",
                    (uid,))
        cur.execute("insert into iam.scope_grant (user_id, node_id, source, reason) values (%s,%s,'manual',"
                    "'Portal administrator') on conflict (user_id, node_id, source) do update set valid_to = null",
                    (uid, company["id"]))
        sessions.revoke_user(cur, uid)
        audit.action(cur, None, "admin.role.grant", "user", code, {"role_code": "PORTAL_ADMIN", "via": "server cli"})
    print("%s is now a portal administrator. Set a sign-in password with: set-password %s" % (code, code))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "set-password":
        set_password(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "make-admin":
        make_admin(*sys.argv[2:7])
    elif len(sys.argv) == 2 and sys.argv[1] == "views-sql":
        from .modules.masters_catalog import snowflake_block
        sys.stdout.write(snowflake_block())
    elif len(sys.argv) == 2 and sys.argv[1] == "bi-sql":
        from .modules.bi_catalog import snowflake_sql as bi_sql
        sys.stdout.write(bi_sql())
    elif len(sys.argv) == 2 and sys.argv[1] == "masters-sql":
        from .modules.masters_catalog import snowflake_sql
        sys.stdout.write(snowflake_sql())
    elif len(sys.argv) == 3 and sys.argv[1] == "run-job":
        from .worker import run_job
        print(run_job(sys.argv[2], "cli"))
    else:
        print(__doc__)
