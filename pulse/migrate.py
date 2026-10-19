"""Apply db/migrations/*.sql in order, as the owner role. Usage: python -m pulse.migrate"""
import pathlib
import sys

import psycopg

from .config import settings

MIGRATIONS = pathlib.Path(__file__).resolve().parent.parent / "db" / "migrations"


def migrate(url: str | None = None, quiet: bool = False) -> list[str]:
    applied = []
    with psycopg.connect(url or settings().database_owner_url, autocommit=True) as conn:
        conn.execute("create schema if not exists core")
        conn.execute("create table if not exists core.schema_migration (version text primary key, applied_at timestamptz not null default now())")
        done = {r[0] for r in conn.execute("select version from core.schema_migration")}
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.stem in done:
                continue
            with conn.transaction():
                conn.execute(f.read_text(encoding="utf-8"))
                conn.execute("insert into core.schema_migration (version) values (%s)", (f.stem,))
            applied.append(f.stem)
            if not quiet:
                print("applied", f.stem)
    return applied


if __name__ == "__main__":
    migrate()
    sys.exit(0)
