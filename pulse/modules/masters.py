"""Masters library: a catalog of master-data sets, each read from one curated Snowflake view in
V2RETAIL.PULSE and browsed with server-side search, column filters, sorting and paging (masters can
have hundreds of thousands of rows, so nothing is paged in memory). Row scope is applied server-side
for scoped masters. Opening, exporting and every catalog change are logged.

Folder trees for masters (Central + one per department) are served by the shared folder engine in
reports.py under /api/v1/masters/trees, /tree, /folders, ...
"""
import csv
import io
import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import audit, db
from ..data import gateway
from ..iam.context import Ctx
from ..web import envelope, require
from . import masters_catalog as cat
from .reports import _create_folder, _roles_ok, can_see_tree

router = APIRouter(prefix="/api/v1")
VIEW = require("masters.view")

COL = re.compile(r"^[A-Z_][A-Z0-9_]*$")
OPS = {"eq", "ne", "contains", "starts", "in", "empty", "notempty", "gt", "gte", "lt", "lte"}
PAGE_SIZES = (25, 50, 100, 250, 500)
EXPORT_MAX_ROWS = 200_000
MASTER_RATE_PER_HOUR = 600   # browsing pages through a master is many small queries; cache hits are free
EXECUTOR = None              # tests inject a fake Snowflake here


# ---------------------------------------------------------------- access
def _visible(m: dict, ctx: Ctx) -> bool:
    if m["status"] == "deprecated" and not ctx.can("masters.manage"):
        return False
    if m["status"] == "draft" and not ctx.can("masters.manage"):
        return False
    if not _roles_ok(m["audience_roles"], ctx, "masters") and not ctx.can("masters.manage"):
        return False
    # Department masters: own department(s), or anyone allowed to see every department's masters.
    if m["layer"] == "department" and m["owner_dept"] and not ctx.can("masters.manage"):
        return can_see_tree(ctx, "dept:" + m["owner_dept"], "masters") or _filed_where_visible(m, ctx)
    return True


def _filed_where_visible(m, ctx) -> bool:
    """A department master filed into a tree the user can see (e.g. Central) is visible to them."""
    trees = m.get("_trees")
    if trees is None:
        with db.tx() as cur:
            cur.execute("select distinct f.tree from nav.item i join nav.folder f on f.id = i.folder_id"
                        " where i.master_id = %s and not f.is_archived", (m["id"],))
            trees = [r["tree"] for r in cur.fetchall()]
    return any(can_see_tree(ctx, t, "masters") for t in trees)


def _master(cur, code: str, ctx: Ctx) -> dict:
    cur.execute("select * from mst.master where code = %s", (code,))
    m = cur.fetchone()
    if not m or not _visible(m, ctx):
        raise HTTPException(404, "Master not found.")
    return m


def _shape(m: dict, ctx: Ctx, full: bool = False) -> dict:
    out = {k: m[k] for k in ("code", "name", "description", "layer", "owner_dept", "steward_name", "sensitivity",
                             "status", "tags", "row_count", "refresh_note")}
    out["id"] = str(m["id"])
    out["profiled_at"] = m["profiled_at"].isoformat() if m["profiled_at"] else None
    out["updated_at"] = m["updated_at"].isoformat() if m["updated_at"] else None
    out["scoped"] = bool(m["scope_column"])
    out["column_count"] = len([c for c in m["columns"] if not c.get("hidden")])
    if full:
        out.update({"columns": m["columns"], "default_sort": m["default_sort"], "scope_column": m["scope_column"],
                    "source_object": m["source_object"], "audience_roles": m["audience_roles"],
                    "cache_seconds": m["cache_seconds"], "profile_error": m["profile_error"],
                    "can_export": ctx.can("masters.export"), "can_manage": ctx.can("masters.manage")})
    return out


# ---------------------------------------------------------------- catalog
@router.get("/masters/catalog")
def catalog(q: str = "", layer: str = "", owner_dept: str = "", ctx: Ctx = Depends(VIEW)):
    q = q.strip().lower()
    with db.tx() as cur:
        cur.execute("select m.*, coalesce((select array_agg(distinct f.tree) from nav.item i join nav.folder f"
                    " on f.id = i.folder_id where i.master_id = m.id and not f.is_archived), '{}') _trees"
                    " from mst.master m order by m.layer, m.owner_dept nulls first, m.name")
        rows = cur.fetchall()
    out = []
    for m in rows:
        if not _visible(m, ctx):
            continue
        if layer and m["layer"] != layer or owner_dept and m["owner_dept"] != owner_dept:
            continue
        hay = " ".join([m["code"], m["name"], m["description"], " ".join(m["tags"])]).lower()
        if q and q not in hay:
            continue
        out.append(_shape(m, ctx))
    return envelope(out)


@router.get("/masters/{code}")
def detail(code: str, ctx: Ctx = Depends(VIEW)):
    with db.tx() as cur:
        m = _master(cur, code, ctx)
        cur.execute("select f.tree, f.id, f.path from nav.item i join nav.folder f on f.id = i.folder_id"
                    " where i.master_id = %s and not f.is_archived", (m["id"],))
        places = []
        for p in cur.fetchall():
            if not can_see_tree(ctx, p["tree"], "masters"):
                continue
            ids = [x for x in p["path"].strip("/").split("/") if x]
            cur.execute("select id, name from nav.folder where id = any(%s::uuid[])", (ids,))
            nm = {str(x["id"]): x["name"] for x in cur.fetchall()}
            places.append({"tree": p["tree"], "folder_id": str(p["id"]), "path_names": [nm.get(i, "?") for i in ids]})
    out = _shape(m, ctx, full=True)
    out["placements"] = places
    out["scope_label"] = ctx.scope_label if m["scope_column"] else None
    return {"data": out}


# ---------------------------------------------------------------- query building
def _cols(m) -> list[dict]:
    return [c for c in m["columns"] if COL.match(c.get("name", ""))]


def _qi(name: str) -> str:
    if not COL.match(name):
        raise HTTPException(422, "Unknown column.")
    return '"%s"' % name


def parse_filters(raw: str, m: dict) -> list[dict]:
    if not raw:
        return []
    try:
        fl = json.loads(raw)
        assert isinstance(fl, list) and len(fl) <= 20
    except (ValueError, AssertionError):
        raise HTTPException(422, "Filters must be a JSON list of at most 20 conditions.")
    names = {c["name"]: c for c in _cols(m)}
    out = []
    for f in fl:
        if not isinstance(f, dict) or f.get("col") not in names or f.get("op") not in OPS:
            raise HTTPException(422, "Unknown filter column or operator.")
        v = f.get("value")
        if f["op"] in ("empty", "notempty"):
            v = None
        elif f["op"] == "in":
            if not isinstance(v, list) or not v or len(v) > 200:
                raise HTTPException(422, "An 'in' filter needs 1 to 200 values.")
            v = [str(x)[:200] for x in v]
        else:
            if v is None or str(v) == "":
                raise HTTPException(422, "Filter on %s needs a value." % names[f["col"]]["label"])
            v = str(v)[:200]
        out.append({"col": f["col"], "op": f["op"], "value": v, "type": names[f["col"]].get("type", "text")})
    return out


def build_where(m: dict, ctx: Ctx, q: str, filters: list[dict]) -> tuple[str, dict]:
    parts, binds = [], {}
    pred, sbind = gateway.scope_sql(m["scope_column"], ctx.scope)
    if m["scope_column"]:
        parts.append(pred.replace("upper(%s)" % m["scope_column"], "upper(%s)" % _qi(m["scope_column"])))
        binds.update(sbind)
    q = (q or "").strip()
    if q:
        search = [c for c in _cols(m) if c.get("searchable")]
        if search:
            binds["__q"] = "%" + q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            parts.append("(" + " or ".join("lower(to_varchar(%s)) like %%(__q)s escape '\\\\'" % _qi(c["name"])
                                           for c in search) + ")")
    for i, f in enumerate(filters):
        col, k = _qi(f["col"]), "__f%d" % i
        num = f["type"] == "number"
        if f["op"] == "empty":
            parts.append("(%s is null or to_varchar(%s) = '')" % (col, col))
        elif f["op"] == "notempty":
            parts.append("(%s is not null and to_varchar(%s) <> '')" % (col, col))
        elif f["op"] == "in":
            keys = []
            for j, v in enumerate(f["value"]):
                binds["%s_%d" % (k, j)] = v
                keys.append("%%(%s_%d)s" % (k, j))
            parts.append("to_varchar(%s) in (%s)" % (col, ", ".join(keys)))
        elif f["op"] in ("contains", "starts"):
            v = f["value"].lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            binds[k] = ("%" if f["op"] == "contains" else "") + v + "%"
            parts.append("lower(to_varchar(%s)) like %%(%s)s escape '\\\\'" % (col, k))
        else:
            sym = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[f["op"]]
            if num:
                try:
                    binds[k] = float(f["value"])
                except ValueError:
                    raise HTTPException(422, "Filter value must be a number.")
                parts.append("%s %s %%(%s)s" % (col, sym, k))
            elif f["type"] in ("date", "datetime") and f["op"] in ("gt", "gte", "lt", "lte"):
                binds[k] = f["value"]
                parts.append("%s %s try_to_timestamp(%%(%s)s)" % (col, sym, k))
            else:
                binds[k] = f["value"] if f["op"] not in ("eq", "ne") else f["value"].lower()
                parts.append("lower(to_varchar(%s)) %s %%(%s)s" % (col, sym, k) if f["op"] in ("eq", "ne")
                             else "to_varchar(%s) %s %%(%s)s" % (col, sym, k))
    return (" and ".join(parts) or "1=1"), binds


def _order(m, sort: str, dir_: str) -> str:
    names = {c["name"] for c in _cols(m)}
    keys = [c["name"] for c in _cols(m) if c.get("key")]
    col = sort if sort in names else (m["default_sort"] if m["default_sort"] in names else (keys[0] if keys else None))
    if not col:
        return ""
    d = "desc" if dir_ == "desc" else "asc"
    tie = [k for k in keys if k != col]
    return " order by %s %s nulls last%s" % (_qi(col), d, "".join(", %s" % _qi(k) for k in tie))


def _select_list(m) -> str:
    return ", ".join(_qi(c["name"]) for c in _cols(m))


def _key(m, ctx, *rest):
    return [m["source_object"], m["updated_at"].isoformat() if m["updated_at"] else "", "ALL" if ctx.scope.all
            else sorted(ctx.scope.sites) if m["scope_column"] else "-", *rest]


# ---------------------------------------------------------------- data
@router.get("/masters/{code}/rows")
def rows(code: str, request: Request, q: str = "", filters: str = "", sort: str = "", dir: str = "asc", page: int = 1,
         size: int = 100, ctx: Ctx = Depends(VIEW)):
    with db.tx() as cur:
        m = _master(cur, code, ctx)
    size = size if size in PAGE_SIZES else 100
    page = max(1, min(page, 100_000))
    fl = parse_filters(filters, m)
    where, binds = build_where(m, ctx, q, fl)
    sql = ('select %s, count(*) over () as "__TOTAL" from %s where %s%s limit %d offset %d'
           % (_select_list(m), m["source_object"], where, _order(m, sort, dir), size, (page - 1) * size))
    try:
        res = gateway.run_sql("master:" + code, sql, binds, _key(m, ctx, q.strip().lower(), fl, sort, dir, page, size),
                              ctx.user_id, cache_seconds=m["cache_seconds"], max_rows=size, bucket="mst",
                              limit=MASTER_RATE_PER_HOUR, executor=EXECUTOR)
    except gateway.GatewayError as e:
        raise HTTPException(e.status, str(e))
    cols = [c.lower() for c in res["columns"]]
    # count(*) over () rides on every row; a page past the end has no rows and reports 0.
    total = res["rows"][0][cols.index("__total")] if res["rows"] and "__total" in cols else 0
    names = [c["name"] for c in _cols(m)]
    out = []
    for r in res["rows"]:
        d = dict(zip(cols, r))
        out.append({n: d.get(n.lower()) for n in names})
    if page == 1 and not q and not fl:
        audit.action_now(ctx, "master.open", "master", code, {"rows": total}, req=request)
    return envelope(out, as_of=res["as_of"], cache_hit=res["cache_hit"], total=int(total or 0), page=page, size=size,
                    pages=max(1, -(-int(total or 0) // size)), scope=ctx.scope_label if m["scope_column"] else None)


@router.get("/masters/{code}/values")
def values(code: str, column: str, q: str = "", ctx: Ctx = Depends(VIEW)):
    """Up to 100 most common values of one column (within the caller's scope), for filter pickers."""
    with db.tx() as cur:
        m = _master(cur, code, ctx)
    col = next((c for c in _cols(m) if c["name"] == column and c.get("filterable", True)), None)
    if not col:
        raise HTTPException(422, "Unknown column.")
    where, binds = build_where(m, ctx, "", [])
    if q.strip():
        binds["__vq"] = "%" + q.strip().lower().replace("%", "\\%").replace("_", "\\_") + "%"
        where += " and lower(to_varchar(%s)) like %%(__vq)s escape '\\\\'" % _qi(column)
    sql = ('select to_varchar(%s) as "VALUE", count(*) as "N" from %s where %s and %s is not null group by 1'
           ' order by 2 desc, 1 limit 100' % (_qi(column), m["source_object"], where, _qi(column)))
    try:
        res = gateway.run_sql("master:%s:values" % code, sql, binds, _key(m, ctx, "values", column, q.strip().lower()),
                              ctx.user_id, cache_seconds=max(m["cache_seconds"], 1800), max_rows=100, bucket="mst",
                              limit=MASTER_RATE_PER_HOUR, executor=EXECUTOR)
    except gateway.GatewayError as e:
        raise HTTPException(e.status, str(e))
    cols = [c.lower() for c in res["columns"]]
    return {"data": [{"value": dict(zip(cols, r)).get("value"), "count": dict(zip(cols, r)).get("n")} for r in res["rows"]]}


@router.get("/masters/{code}/export")
def export(code: str, request: Request, q: str = "", filters: str = "", sort: str = "", dir: str = "asc",
           columns: str = "", ctx: Ctx = Depends(require("masters.export"))):
    with db.tx() as cur:
        m = _master(cur, code, ctx)
    fl = parse_filters(filters, m)
    where, binds = build_where(m, ctx, q, fl)
    wanted = [c for c in columns.split(",") if c] if columns else [c["name"] for c in _cols(m) if not c.get("hidden")]
    cols = [c for c in _cols(m) if c["name"] in wanted] or _cols(m)
    sql = "select %s from %s where %s%s limit %d" % (", ".join(_qi(c["name"]) for c in cols), m["source_object"], where,
                                                      _order(m, sort, dir), EXPORT_MAX_ROWS)
    try:
        res = gateway.run_sql("master:%s:export" % code, sql, binds, _key(m, ctx, "export", q, fl, sort, dir, wanted),
                              ctx.user_id, cache_seconds=0, max_rows=EXPORT_MAX_ROWS, bucket="mst",
                              limit=MASTER_RATE_PER_HOUR, executor=EXECUTOR)
    except gateway.GatewayError as e:
        raise HTTPException(e.status, str(e))
    audit.action_now(ctx, "master.export", "master", code,
                     {"rows": len(res["rows"]), "q": q, "filters": fl, "columns": [c["name"] for c in cols],
                      "truncated": len(res["rows"]) >= EXPORT_MAX_ROWS}, req=request)
    idx = {c.lower(): i for i, c in enumerate(res["columns"])}

    def gen():
        buf = io.StringIO()
        buf.write("﻿")   # so Excel opens UTF-8 (Hindi labels) correctly
        w = csv.writer(buf)
        w.writerow([c["label"] for c in cols])
        for r in res["rows"]:
            w.writerow([r[idx[c["name"].lower()]] if c["name"].lower() in idx else "" for c in cols])
            if buf.tell() > 65536:
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()
        yield buf.getvalue()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return StreamingResponse(gen(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="%s-%s.csv"' % (code, stamp)})


# ---------------------------------------------------------------- admin: catalog
class ColumnIn(BaseModel):
    name: str = Field(pattern=r"^[A-Z_][A-Z0-9_]*$")
    label: str = Field(min_length=1, max_length=80)
    type: str = Field("text", pattern=r"^(text|number|date|datetime|bool)$")
    key: bool = False
    searchable: bool = False
    filterable: bool = True
    hidden: bool = False


class MasterIn(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{1,60}$")
    name: str = Field(min_length=2, max_length=120)
    description: str = ""
    layer: str = Field("department", pattern=r"^(central|department)$")
    owner_dept: str | None = None
    steward_name: str | None = None
    source_object: str = Field(pattern=r"^V2RETAIL\.PULSE\.[A-Z0-9_]+$")
    columns: list[ColumnIn] = []
    default_sort: str | None = None
    scope_column: str | None = None
    sensitivity: str = Field("internal", pattern=r"^(internal|restricted|confidential)$")
    refresh_note: str = ""
    status: str = Field("live", pattern=r"^(draft|live|deprecated)$")
    audience_roles: list[str] = []
    tags: list[str] = []
    cache_seconds: int = Field(900, ge=0, le=86400)


FIELDS = [f for f in MasterIn.model_fields]


def _validate(b: MasterIn):
    names = [c.name for c in b.columns]
    if len(set(names)) != len(names):
        raise HTTPException(422, "Column names must be unique.")
    for f in ("default_sort", "scope_column"):
        v = getattr(b, f)
        if v and v not in names:
            raise HTTPException(422, "%s must be one of the columns." % f.replace("_", " ").capitalize())


def _row(b: MasterIn):
    d = b.model_dump()
    d["columns"] = json.dumps(d["columns"])
    return d


@router.get("/admin/masters")
def admin_list(ctx: Ctx = Depends(require("masters.manage"))):
    with db.tx() as cur:
        cur.execute("select m.*, (select count(*) from nav.item i where i.master_id = m.id) placements from mst.master m"
                    " order by m.layer, m.owner_dept nulls first, m.name")
        rows = cur.fetchall()
    return {"data": [{**_shape(m, ctx, full=True), "placements_count": m["placements"]} for m in rows]}


@router.post("/admin/masters")
def admin_create(body: MasterIn, request: Request, ctx: Ctx = Depends(require("masters.manage"))):
    _validate(body)
    with db.tx() as cur:
        cur.execute("select 1 from mst.master where code = %s", (body.code,))
        if cur.fetchone():
            raise HTTPException(409, "Master code %s already exists." % body.code)
        d = _row(body)
        cur.execute("insert into mst.master (%s, updated_by) values (%s, %%s)" % (", ".join(FIELDS), ", ".join(["%s"] * len(FIELDS))),
                    [d[f] for f in FIELDS] + [ctx.user_id])
        audit.action(cur, ctx, "master.create", "master", body.code, body.model_dump(), req=request)
    return {"ok": True}


@router.put("/admin/masters/{code}")
def admin_update(code: str, body: MasterIn, request: Request, ctx: Ctx = Depends(require("masters.manage"))):
    _validate(body)
    if body.code != code:
        raise HTTPException(422, "The code cannot change.")
    with db.tx() as cur:
        cur.execute("select * from mst.master where code = %s for update", (code,))
        before = cur.fetchone()
        if not before:
            raise HTTPException(404, "Master not found.")
        d = _row(body)
        cur.execute("update mst.master set %s, updated_at = now(), updated_by = %%s where code = %%s"
                    % ", ".join("%s = %%s" % f for f in FIELDS), [d[f] for f in FIELDS] + [ctx.user_id, code])
        new = body.model_dump()
        audit.action(cur, ctx, "master.update", "master", code,
                     {"changes": {k: {"from": before[k], "to": v} for k, v in new.items() if before.get(k) != v}}, req=request)
    return {"ok": True}


TYPE_MAP = {"TEXT": "text", "VARCHAR": "text", "NUMBER": "number", "FLOAT": "number", "DATE": "date",
            "TIMESTAMP_NTZ": "datetime", "TIMESTAMP_LTZ": "datetime", "TIMESTAMP_TZ": "datetime", "BOOLEAN": "bool"}


def profile(cur, m: dict, executor=None) -> dict:
    """Read the view's columns and row count from Snowflake; keep admin labels/flags for known columns."""
    obj = m["source_object"].split(".")
    sql_cols = ("select column_name, data_type from V2RETAIL.information_schema.columns"
                " where table_schema = %(s)s and table_name = %(t)s order by ordinal_position")
    run = executor or gateway._execute
    try:
        found, _ = run(sql_cols, {"s": obj[1], "t": obj[2]}, 2000, "master-profile")
        if not found:
            raise RuntimeError("View %s does not exist or not authorized." % m["source_object"])
        counted, _ = run("select count(*) as n from %s" % m["source_object"], {}, 1, "master-profile")
    except Exception as e:  # noqa: BLE001
        cur.execute("update mst.master set profile_error = %s, profiled_at = now() where id = %s", (str(e)[:500], m["id"]))
        return {"ok": False, "error": gateway._friendly(e)}
    known = {c["name"]: c for c in m["columns"]}
    cols = []
    for r in found:
        name, typ = r["column_name"], TYPE_MAP.get((r["data_type"] or "").upper(), "text")
        c = known.get(name) or {"name": name, "label": cat.label(name), "key": False, "searchable": typ == "text",
                                "filterable": True, "hidden": False}
        cols.append({**c, "type": typ})
    n = list(counted[0].values())[0] if counted else None
    cur.execute("update mst.master set columns = %s, row_count = %s, profiled_at = now(), profile_error = null where id = %s",
                (json.dumps(cols), n, m["id"]))
    return {"ok": True, "columns": len(cols), "row_count": n,
            "added": [c["name"] for c in cols if c["name"] not in known],
            "removed": [k for k in known if k not in {c["name"] for c in cols}]}


@router.post("/admin/masters/{code}/profile")
def admin_profile(code: str, request: Request, ctx: Ctx = Depends(require("masters.manage"))):
    with db.tx() as cur:
        cur.execute("select * from mst.master where code = %s for update", (code,))
        m = cur.fetchone()
        if not m:
            raise HTTPException(404, "Master not found.")
        res = profile(cur, m, EXECUTOR)
        audit.action(cur, ctx, "master.profile", "master", code, res, req=request)
    return {"data": res}


def profile_all() -> dict:
    """Worker job: refresh row counts and columns of every live master."""
    done, failed = 0, []
    with db.tx() as cur:
        cur.execute("select * from mst.master where status <> 'deprecated' order by code")
        ms = cur.fetchall()
    for m in ms:
        with db.tx() as cur:
            r = profile(cur, m, EXECUTOR)
        if r["ok"]:
            done += 1
        else:
            failed.append(m["code"])
    return {"profiled": done, "failed": failed}


# ---------------------------------------------------------------- seed
def seed(cur):
    """Add catalog masters that do not exist yet and, the first time, file them into their folders.
    Existing masters belong to the admins and are left alone."""
    for spec in cat.MASTERS:
        cur.execute("insert into mst.master (code, name, description, layer, owner_dept, source_object, columns, default_sort,"
                    " scope_column, sensitivity, refresh_note, tags, cache_seconds) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                    " on conflict (code) do nothing returning id",
                    (spec["code"], spec["name"], spec["description"], spec["layer"], spec["owner_dept"], cat.source_object(spec),
                     json.dumps(cat.catalog_columns(spec)), spec.get("sort"), spec.get("scope"),
                     spec.get("sensitivity", "internal"), spec.get("refresh", ""), spec.get("tags", []),
                     spec.get("cache", 900)))
        row = cur.fetchone()
        if not row:
            continue
        for tree, path in spec["placements"]:
            if tree != "central":
                cur.execute("select 1 from iam.org_node where node_type = 'dept' and code = %s", (tree[5:],))
                if not cur.fetchone():
                    continue
            parent = None
            for name in path:
                cur.execute("select * from nav.folder where section = 'masters' and tree = %s and parent_id is not distinct"
                            " from %s and lower(name) = lower(%s) and not is_archived",
                            (tree, parent["id"] if parent else None, name))
                f = cur.fetchone()
                parent = f or _create_folder(cur, tree, parent, name, None, "masters", description=cat.FOLDER_DESC.get(name, ""))
            cur.execute("select coalesce(max(sort_order), 0) + 10 n from nav.item where folder_id = %s", (parent["id"],))
            cur.execute("insert into nav.item (id, folder_id, kind, master_id, sort_order) values (%s,%s,'master',%s,%s)",
                        (uuid.uuid4(), parent["id"], row["id"], cur.fetchone()["n"]))
