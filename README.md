# V2Pulse

V2 Retail report portal: FastAPI + PostgreSQL + Snowflake.

- Backend: `pulse/` (Python). MIS module in `pulse/mis_engine` + `pulse/modules/mis.py`; MIS pages in `pulse/mis_ui` (plain JS, no build step).
- Frontend: `pulse/static` is the **built** React bundle only. The React source is not available.
- Database migrations: `db/migrations/NNNN_*.sql` (never edit an applied one).
- Deploy: `deploy/update.bat` on the DataCenter server (as Administrator).

Live copy: `\DataCenter\Python\v2pulse` (D:\Mahesh\Python\v2pulse). It is edited directly by several people;
diff the live files against this repo before copying anything to the server. Secrets live only in `env.bat` / `keys\`
on the server (see `env.bat.example`) and must never be committed.
