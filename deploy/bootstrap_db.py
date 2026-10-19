"""Create the two roles and the database on the V2Pulse Postgres instance (idempotent).
Reads passwords from the environment set by env.bat; never prints them."""
import os

import psycopg
from psycopg import sql

PORT = int(os.environ.get("PULSE_PG_PORT", "5435"))


def su(db):
    return psycopg.connect(host="127.0.0.1", port=PORT, user="postgres", password=os.environ["PULSE_PG_SUPERPASS"],
                           dbname=db, autocommit=True)


with su("postgres") as c:
    for role, pw, extra in (("pulse_owner", os.environ["PULSE_PG_OWNERPASS"], ""),
                            ("pulse_app", os.environ["PULSE_PG_APPPASS"], " nobypassrls")):
        exists = c.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone()
        verb = "alter" if exists else "create"
        c.execute(sql.SQL(verb + " role {} login password {}" + extra).format(sql.Identifier(role), sql.Literal(pw)))
        print("%s role %s" % ("updated" if exists else "created", role))
    if not c.execute("select 1 from pg_database where datname = 'pulse'").fetchone():
        c.execute("create database pulse owner pulse_owner encoding 'UTF8' template template0")
        print("created database pulse")
with su("pulse") as c:
    c.execute("revoke all on schema public from public")
    c.execute("grant connect on database pulse to pulse_app")
print("bootstrap ok")
