Vendored unchanged from Projects\report-portal (identical to the live copy on DataCenter :8874,
md5-checked 2026-10-13). Only `db.py` here is new: a shim that gives the collector the same
`connect / run / quote_fqn / PORTAL_SCHEMA` it had, backed by pulse.data.snowflake profile "ct".
Change status rules in the Report Portal first, then re-copy, so both portals agree.
