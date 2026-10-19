"""Dynamic (Power BI-style) reports: governed datasets, an aggregate query engine, and report definitions.

Queries name dimensions and measures of a dataset; SQL is built only from the dataset definition (column
identifiers are validated, values are bound), aggregated in Snowflake, limited to the caller's store scope
and cached. A snapshot dataset (each date is a full as-of picture, e.g. MTD sales) defaults to its latest
date, so figures are never summed across snapshots by accident.

Library reports are designed by admins (reports.design) and published to everyone who may see their
dataset; personal reports (reports.personal) belong to one user and nobody else can open them.
"""
import json
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import audit, db
from ..data import gateway
from ..iam.context import Ctx
from ..web import envelope, require
from . import bi_catalog as cat

router = APIRouter(prefix="/api/v1")
VIEW = require("reports.view")

COL = re.compile(r"^[A-Z_][A-Z0-9_]*$")
KEY = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
AGGS = {"sum", "avg", "min", "max", "count", "count_distinct", "ratio", "growth"}
VISUALS = {"kpi", "column", "bar", "line", "area", "donut", "table", "matrix"}
OPS = {"in", "not_in", "between", "gte", "lte", "last_days"}
MAX_ROWS = 5000
BI_RATE_PER_HOUR = 900       # a report page fires several small queries; cache hits are free
EXECUTOR = None              # tests inject a fake Snowflake here


# ---------------------------------------------------------------- datasets
def _dataset(cur, code: str, ctx: Ctx) -> dict:
    cur.execute("select * from bi.dataset where code = %s", (code,))
    d = cur.fetchone()
    if not d or not _dataset_visible(d, ctx):
        raise HTTPException(404, "Dataset not found.")
    return d


def _dataset_visible(d: dict, ctx: Ctx) -> bool:
    if ctx.can("reports.design"):
        return True
    if d["status"] != "live":
        return False
    return not d["audience_roles"] or bool(set(d["audience_roles"]) & set(ctx.roles))


def _shape_dataset(d: dict) -> dict:
    return {"code": d["code"], "name": d["name"], "description": d["description"], "dimensions": d["dimensions"],
            "measures": [{k: m.get(k) for k in ("key", "label", "agg", "format", "time_agg")} for m in d["measures"]],
            "snapshot_dim": d["snapshot_dim"], "scoped": bool(d["scope_column"]), "refresh_note": d["refresh_note"],
            "status": d["status"]}


@router.get("/bi/datasets")
def datasets(ctx: Ctx = Depends(VIEW)):
    with db.tx() as cur:
        cur.execute("select * from bi.dataset order by name")
        return {"data": [_shape_dataset(d) for d in cur.fetchall() if _dataset_visible(d, ctx)]}


@router.get("/bi/datasets/{code}")
def dataset(code: str, ctx: Ctx = Depends(VIEW)):
    with db.tx() as cur:
        d = _dataset(cur, code, ctx)
    out = _shape_dataset(d)
    out["scope_label"] = ctx.scope_label if d["scope_column"] else None
    return {"data": out}


# ---------------------------------------------------------------- query engine
class Filter(BaseModel):
    dim: str
    op: str = "in"
    values: list = Field(default_factory=list, max_length=500)


class Query(BaseModel):
    dataset: str
    dims: list[str] = Field(default_factory=list, max_length=25)   # detail tables group by many columns
    measures: list[str] = Field(default_factory=list, max_length=20)   # Excel-style tables carry ~16 columns
    filters: list[Filter] = Field(default_factory=list, max_length=30)
    sort: dict | None = None            # {"by": key, "dir": "asc"|"desc"}
    limit: int = Field(500, ge=1, le=MAX_ROWS)


def _qi(c: str) -> str:
    if not COL.match(c or ""):
        raise HTTPException(500, "Bad column in dataset definition.")
    return '"%s"' % c


# Time grains a date field can be rolled up to. Every grain returns a date (the period start), except
# day of week (1 = Monday). The fiscal year runs April to March.
GRAINS = {
    "day": "%s",
    "week": "date_trunc('week', %s)",
    "month": "date_trunc('month', %s)",
    "quarter": "date_trunc('quarter', %s)",
    "year": "date_trunc('year', %s)",
    "fq": "dateadd(month, 3, date_trunc('quarter', dateadd(month, -3, %s)))",
    "fy": "dateadd(month, 3, date_trunc('year', dateadd(month, -3, %s)))",
    "dow": "dayofweekiso(%s)",
}
# How far back a trend reaches by default when the report does not narrow the dates (days).
DEFAULT_WINDOW = {"day": 30, "week": 91, "dow": 91, "month": 365, "quarter": 730, "fq": 730, "year": None, "fy": None}


def dim_sql(dim: dict) -> str:
    return GRAINS[dim.get("grain") or "day"] % _qi(dim["col"])


def _is_time(dim: dict, d: dict) -> bool:
    return bool(dim.get("grain")) or dim["key"] == d["snapshot_dim"]


def _measure_sql(m: dict, bykey: dict, snap_col: str | None = None) -> str:
    """snap_col set = the query rolls snapshots up into periods: a measure with time_agg 'last' (an as-of
    figure such as MTD or a month target) takes only the period's last date; flows (daily sales) add up."""
    agg = m["agg"]
    if agg in ("ratio", "growth"):
        num, den = bykey.get(m.get("num")), bykey.get(m.get("den"))
        if not num or not den or num["agg"] in ("ratio", "growth") or den["agg"] in ("ratio", "growth"):
            raise HTTPException(500, "Bad ratio measure %s." % m["key"])
        expr = "(%s) / nullif(%s, 0)" % (_measure_sql(num, bykey, snap_col), _measure_sql(den, bykey, snap_col))
        return expr + (" - 1" if agg == "growth" else "")
    col = _qi(m["col"])
    if snap_col and m.get("time_agg") == "last":
        col = 'case when %s = "__pend" then %s end' % (snap_col, col)
    return {"sum": "sum(%s)", "avg": "avg(%s)", "min": "min(%s)", "max": "max(%s)", "count": "count(%s)",
            "count_distinct": "count(distinct %s)"}[agg] % col


def build_query(d: dict, q: Query, ctx: Ctx) -> tuple[str, dict, list[dict]]:
    dims = {x["key"]: x for x in d["dimensions"]}
    meas = {x["key"]: x for x in d["measures"]}
    for k in q.dims:
        if k not in dims:
            raise HTTPException(422, "Unknown field %s." % k)
    for k in q.measures:
        if k not in meas:
            raise HTTPException(422, "Unknown measure %s." % k)
    if not q.dims and not q.measures:
        raise HTTPException(422, "Pick at least one field or measure.")
    src = d["source_object"]
    binds, where = {}, []
    if d["scope_column"]:
        pred, sb = gateway.scope_sql(d["scope_column"], ctx.scope)
        where.append(pred.replace("upper(%s)" % d["scope_column"], "upper(%s)" % _qi(d["scope_column"])))
        binds.update(sb)
    snap = d["snapshot_dim"]
    snap_col = _qi(dims[snap]["col"]) if snap else None
    time_filtered = False
    for i, f in enumerate(q.filters):
        if f.dim not in dims:
            raise HTTPException(422, "Unknown filter field %s." % f.dim)
        if f.op not in OPS:
            raise HTTPException(422, "Unknown filter operator %s." % f.op)
        col, k = dim_sql(dims[f.dim]), "__f%d" % i
        vals = [str(v)[:200] for v in f.values if v is not None and str(v) != ""]
        if _is_time(dims[f.dim], d):
            time_filtered = True
        if f.op in ("in", "not_in"):
            if not vals:
                continue
            keys = []
            for j, v in enumerate(vals):
                binds["%s_%d" % (k, j)] = v
                keys.append("%%(%s_%d)s" % (k, j))
            where.append("to_varchar(%s) %s (%s)" % (col, "in" if f.op == "in" else "not in", ", ".join(keys)))
        elif f.op == "between":
            if len(vals) != 2:
                raise HTTPException(422, "A range needs two values.")
            binds[k + "_a"], binds[k + "_b"] = vals
            where.append("%s between %%(%s_a)s and %%(%s_b)s" % (col, k, k))
        elif f.op in ("gte", "lte"):
            if len(vals) != 1:
                raise HTTPException(422, "Give one value.")
            binds[k] = vals[0]
            where.append("%s %s %%(%s)s" % (col, ">=" if f.op == "gte" else "<=", k))
        elif f.op == "last_days":
            try:
                n = max(1, min(int(vals[0]), 1500))
            except (ValueError, IndexError):
                raise HTTPException(422, "last_days needs a number of days.")
            base = _qi(dims[f.dim]["col"])
            where.append("%s >= dateadd(day, -%d, (select max(%s) from %s))" % (base, n - 1, base, src))
    time_dims = [dims[k] for k in q.dims if _is_time(dims[k], d)]
    if snap and not time_filtered:
        if time_dims:   # a trend: a default window that suits the finest grain shown
            windows = [DEFAULT_WINDOW[x.get("grain") or "day"] for x in time_dims]
            days = None if any(w is None for w in windows) else min(windows)
            if days:
                where.append("%s >= dateadd(day, -%d, (select max(%s) from %s))" % (snap_col, days - 1, snap_col, src))
        else:           # totals: the latest snapshot only
            where.append("%s = (select max(%s) from %s)" % (snap_col, snap_col, src))
    # Snapshots rolled up to periods (or a date range without a date field): mark each period's last date.
    rollup = bool(snap) and (any(x.get("grain") not in (None, "day") for x in time_dims) or (time_filtered and not time_dims))
    select, cols = [], []
    for k in q.dims:
        select.append("%s as \"d_%s\"" % (dim_sql(dims[k]), k))
        cols.append({"key": k, "label": dims[k]["label"], "kind": "dim", "type": dims[k].get("type", "text"),
                     "grain": dims[k].get("grain")})
    for k in q.measures:
        select.append("%s as \"m_%s\"" % (_measure_sql(meas[k], meas, snap_col if rollup else None), k))
        cols.append({"key": k, "label": meas[k]["label"], "kind": "measure", "format": meas[k].get("format", "num")})
    if snap:
        select.append("max(%s) as \"__asof\"" % snap_col)
    cond = " and ".join(where) or "1=1"
    if rollup:
        part = ", ".join(dim_sql(x) for x in time_dims if x.get("grain") not in (None, "day"))
        inner = "select s.*, max(s.%s) over (%s) as \"__pend\" from %s s where %s" % (
            snap_col, "partition by " + part if part else "", src, cond)
        sql = "select %s from (%s) t" % (", ".join(select), inner)
    else:
        sql = "select %s from %s where %s" % (", ".join(select), src, cond)
    if q.dims:
        sql += " group by %s" % ", ".join(str(i + 1) for i in range(len(q.dims)))
    sort = q.sort or {}
    by, dir_ = sort.get("by"), "desc" if sort.get("dir") == "desc" else "asc"
    if by in q.dims:
        sql += ' order by "d_%s" %s nulls last' % (by, dir_)
    elif by in q.measures:
        sql += ' order by "m_%s" %s nulls last' % (by, dir_)
    elif q.dims:
        sql += " order by 1"
    sql += " limit %d" % q.limit
    return sql, binds, cols


def run_query(d: dict, q: Query, ctx: Ctx) -> dict:
    sql, binds, cols = build_query(d, q, ctx)
    key = [d["code"], d["updated_at"].isoformat() if d.get("updated_at") else "", q.model_dump(),
           "ALL" if ctx.scope.all else sorted(ctx.scope.sites) if d["scope_column"] else "-"]
    try:
        res = gateway.run_sql("bi:" + d["code"], sql, binds, key, ctx.user_id, cache_seconds=d["cache_seconds"],
                              max_rows=q.limit, bucket="bi", limit=BI_RATE_PER_HOUR, executor=EXECUTOR)
    except gateway.GatewayError as e:
        raise HTTPException(e.status, str(e))
    names = [c.lower() for c in res["columns"]]
    idx = {n: i for i, n in enumerate(names)}
    rows, asof = [], None
    for r in res["rows"]:
        out = []
        for c in cols:
            k = ("d_" if c["kind"] == "dim" else "m_") + c["key"]
            out.append(r[idx[k]] if k in idx else None)
        rows.append(out)
        if "__asof" in idx and r[idx["__asof"]]:
            v = str(r[idx["__asof"]])
            asof = v if not asof or v > asof else asof
    return {"columns": cols, "rows": rows, "snapshot": asof, "as_of": res["as_of"], "cache_hit": res["cache_hit"],
            "truncated": len(rows) >= q.limit}


@router.post("/bi/query")
def query(body: Query, ctx: Ctx = Depends(VIEW)):
    with db.tx() as cur:
        d = _dataset(cur, body.dataset, ctx)
    out = run_query(d, body, ctx)
    return envelope(out, scope=ctx.scope_label if d["scope_column"] else None)


@router.get("/bi/datasets/{code}/values")
def values(code: str, dim: str, q: str = "", ctx: Ctx = Depends(VIEW)):
    """Values of one field for slicers, most frequent first (latest snapshot, caller's scope)."""
    with db.tx() as cur:
        d = _dataset(cur, code, ctx)
    dims = {x["key"]: x for x in d["dimensions"]}
    if dim not in dims:
        raise HTTPException(422, "Unknown field.")
    if _is_time(dims[dim], d) and d["snapshot_dim"]:   # newest first, last ~3 years of dates
        body = Query(dataset=code, dims=[dim], measures=[], sort={"by": dim, "dir": "desc"}, limit=1000,
                     filters=[Filter(dim=d["snapshot_dim"], op="last_days", values=["1100"])])
    else:
        # Up to MAX_ROWS values: the slicer searches the list it gets, and inventory has ~1,350 major categories
        # and ~2,600 merge vendors - with 1000 fetched / 500 returned, most of them could never be picked.
        body = Query(dataset=code, dims=[dim], measures=[], sort={"by": dim, "dir": "asc"}, limit=MAX_ROWS)
    res = run_query(d, body, ctx)
    s = q.strip().lower()
    vals = [r[0] for r in res["rows"] if r[0] is not None and (not s or s in str(r[0]).lower())]
    return {"data": vals[:MAX_ROWS]}


# ---------------------------------------------------------------- report definitions
def validate_definition(defn: dict, d: dict) -> dict:
    if not isinstance(defn, dict) or not isinstance(defn.get("pages"), list) or not 1 <= len(defn["pages"]) <= 12:
        raise HTTPException(422, "A report needs 1 to 12 pages.")
    dims = {x["key"] for x in d["dimensions"]}
    meas = {x["key"] for x in d["measures"]}
    # Report filters apply to every visual on every page (e.g. "active stores only").
    rf = defn.get("filters") or []
    if not isinstance(rf, list) or len(rf) > 10:
        raise HTTPException(422, "A report can have at most 10 report filters.")
    for f in rf:
        if not isinstance(f, dict) or f.get("dim") not in dims or f.get("op", "in") not in OPS                 or not isinstance(f.get("values"), list) or not f["values"]:
            raise HTTPException(422, "Report filter on unknown field or without values.")
    seen = set()
    for p in defn["pages"]:
        if not isinstance(p, dict) or not str(p.get("name", "")).strip():
            raise HTTPException(422, "Every page needs a name.")
        vis = p.get("visuals") or []
        if len(vis) > 30:
            raise HTTPException(422, "A page can hold at most 30 visuals.")
        for s in p.get("slicers") or []:
            if s.get("dim") not in dims:
                raise HTTPException(422, "Slicer on unknown field %s." % s.get("dim"))
        for v in vis:
            if v.get("type") not in VISUALS:
                raise HTTPException(422, "Unknown visual type %s." % v.get("type"))
            if not KEY.match(str(v.get("id", ""))) or v["id"] in seen:
                raise HTTPException(422, "Visual ids must be unique short names.")
            seen.add(v["id"])
            for k in v.get("dims") or []:
                if k not in dims:
                    raise HTTPException(422, "Visual '%s' uses unknown field %s." % (v.get("title") or v["id"], k))
            for k in v.get("measures") or []:
                if k not in meas:
                    raise HTTPException(422, "Visual '%s' uses unknown measure %s." % (v.get("title") or v["id"], k))
            if v["type"] != "kpi" and v["type"] != "table" and not v.get("dims"):
                raise HTTPException(422, "Visual '%s' needs a field to group by." % (v.get("title") or v["id"]))
            max_dims = {"table": 25, "matrix": 2, "kpi": 0}.get(v["type"], 1)
            if len(v.get("dims") or []) > max_dims:
                raise HTTPException(422, "Visual '%s' can group by at most %d field(s)." % (v.get("title") or v["id"], max_dims))
            if not v.get("measures"):
                raise HTTPException(422, "Visual '%s' needs at least one measure." % (v.get("title") or v["id"]))
            for f, lo, hi in (("x", 0, 11), ("w", 1, 12), ("y", 0, 400), ("h", 1, 24)):
                if not isinstance(v.get(f), int) or not lo <= v[f] <= hi:
                    raise HTTPException(422, "Visual '%s' has a bad position." % (v.get("title") or v["id"]))
    return defn


def _report(cur, rid: str, ctx: Ctx, for_edit=False) -> dict:
    try:
        uuid.UUID(rid)
    except ValueError:
        raise HTTPException(404, "Report not found.")
    cur.execute("select r.*, u.display_name owner_name from bi.report r left join iam.users u on u.id = r.owner_id"
                " where r.id = %s", (rid,))
    r = cur.fetchone()
    if not r:
        raise HTTPException(404, "Report not found.")
    if r["visibility"] == "personal":
        if str(r["owner_id"]) != str(ctx.user_id):
            raise HTTPException(404, "Report not found.")
    else:
        designer = ctx.can("reports.design")
        if for_edit and not designer:
            raise HTTPException(403, "Only report designers can change library reports.")
        if not designer and (r["status"] != "published" or (r["audience_roles"] and not set(r["audience_roles"]) & set(ctx.roles))):
            raise HTTPException(404, "Report not found.")
        cur.execute("select * from bi.dataset where code = %s", (r["dataset_code"],))
        d = cur.fetchone()
        if not d or not _dataset_visible(d, ctx):
            raise HTTPException(404, "Report not found.")
    return r


def _shape_report(r: dict, ctx: Ctx, full=False) -> dict:
    out = {"id": str(r["id"]), "name": r["name"], "description": r["description"], "category": r["category"],
           "visibility": r["visibility"], "status": r["status"], "dataset": r["dataset_code"],
           "audience_roles": r["audience_roles"], "updated_at": r["updated_at"].isoformat(),
           "pages": len((r["definition"] or {}).get("pages", [])),
           "visuals": sum(len(p.get("visuals") or []) for p in (r["definition"] or {}).get("pages", [])),
           "can_edit": r["visibility"] == "personal" or ctx.can("reports.design"),
           "copied_from": str(r["copied_from"]) if r["copied_from"] else None}
    if full:
        out["definition"] = r["definition"]
    return out


@router.get("/bi/reports")
def reports(scope: str = "library", ctx: Ctx = Depends(VIEW)):
    with db.tx() as cur:
        if scope == "mine":
            cur.execute("select * from bi.report where visibility = 'personal' and owner_id = %s order by updated_at desc",
                        (ctx.user_id,))
            return {"data": [_shape_report(r, ctx) for r in cur.fetchall()]}
        cur.execute("select r.*, d.audience_roles d_aud, d.status d_status from bi.report r"
                    " join bi.dataset d on d.code = r.dataset_code where r.visibility = 'library' order by r.category, r.name")
        out = []
        for r in cur.fetchall():
            if not ctx.can("reports.design"):
                if r["status"] != "published" or r["d_status"] != "live":
                    continue
                if r["d_aud"] and not set(r["d_aud"]) & set(ctx.roles):
                    continue
                if r["audience_roles"] and not set(r["audience_roles"]) & set(ctx.roles):
                    continue
            out.append(_shape_report(r, ctx))
        return {"data": out}


@router.get("/bi/reports/{rid}")
def report(rid: str, request: Request, ctx: Ctx = Depends(VIEW)):
    with db.tx() as cur:
        r = _report(cur, rid, ctx)
        audit.action(cur, ctx, "bi.report.open", "bi_report", rid, {"name": r["name"], "visibility": r["visibility"]},
                     req=request)
    return {"data": _shape_report(r, ctx, full=True)}


class ReportIn(BaseModel):
    visibility: str = Field("personal", pattern=r"^(library|personal)$")
    name: str = Field(min_length=2, max_length=120)
    description: str = Field("", max_length=500)
    category: str = Field("General", min_length=1, max_length=60)
    dataset: str
    definition: dict
    status: str = Field("draft", pattern=r"^(draft|published)$")
    audience_roles: list[str] = []


def _check_create_rights(visibility: str, ctx: Ctx):
    if visibility == "library" and not ctx.can("reports.design"):
        raise HTTPException(403, "Only report designers can create library reports.")
    if visibility == "personal" and not ctx.can("reports.personal"):
        raise HTTPException(403, "You cannot build personal reports.")


@router.post("/bi/reports")
def create_report(body: ReportIn, request: Request, ctx: Ctx = Depends(VIEW)):
    _check_create_rights(body.visibility, ctx)
    with db.tx() as cur:
        d = _dataset(cur, body.dataset, ctx)
        validate_definition(body.definition, d)
        rid = uuid.uuid4()
        cur.execute("insert into bi.report (id, visibility, owner_id, name, description, category, dataset_code, definition,"
                    " status, audience_roles, updated_by) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (rid, body.visibility, ctx.user_id if body.visibility == "personal" else None, body.name.strip(),
                     body.description, body.category.strip(), d["code"], json.dumps(body.definition),
                     body.status if body.visibility == "library" else "published", body.audience_roles, ctx.user_id))
        audit.action(cur, ctx, "bi.report.create", "bi_report", rid,
                     {"name": body.name, "visibility": body.visibility, "dataset": d["code"]}, req=request)
    return {"data": {"id": str(rid)}}


@router.put("/bi/reports/{rid}")
def update_report(rid: str, body: ReportIn, request: Request, ctx: Ctx = Depends(VIEW)):
    with db.tx() as cur:
        r = _report(cur, rid, ctx, for_edit=True)
        if body.visibility != r["visibility"]:
            raise HTTPException(422, "Use 'Save a copy' to move a report between the library and My reports.")
        d = _dataset(cur, body.dataset, ctx)
        validate_definition(body.definition, d)
        cur.execute("update bi.report set name = %s, description = %s, category = %s, dataset_code = %s, definition = %s,"
                    " status = %s, audience_roles = %s, updated_at = now(), updated_by = %s where id = %s",
                    (body.name.strip(), body.description, body.category.strip(), d["code"], json.dumps(body.definition),
                     body.status if r["visibility"] == "library" else "published", body.audience_roles, ctx.user_id, rid))
        audit.action(cur, ctx, "bi.report.update", "bi_report", rid,
                     {"name": body.name, "status": body.status, "visibility": r["visibility"]}, req=request)
    return {"ok": True}


@router.delete("/bi/reports/{rid}")
def delete_report(rid: str, request: Request, ctx: Ctx = Depends(VIEW)):
    with db.tx() as cur:
        r = _report(cur, rid, ctx, for_edit=True)
        cur.execute("delete from bi.report where id = %s", (rid,))
        audit.action(cur, ctx, "bi.report.delete", "bi_report", rid, {"name": r["name"], "visibility": r["visibility"]},
                     req=request)
    return {"ok": True}


class CopyIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    visibility: str = Field("personal", pattern=r"^(library|personal)$")


@router.post("/bi/reports/{rid}/copy")
def copy_report(rid: str, body: CopyIn, request: Request, ctx: Ctx = Depends(VIEW)):
    """Save a copy: a library report into My reports (a personal template), or the reverse for designers."""
    _check_create_rights(body.visibility, ctx)
    with db.tx() as cur:
        r = _report(cur, rid, ctx)
        nid = uuid.uuid4()
        cur.execute("insert into bi.report (id, visibility, owner_id, name, description, category, dataset_code, definition,"
                    " status, audience_roles, copied_from, updated_by) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (nid, body.visibility, ctx.user_id if body.visibility == "personal" else None, body.name.strip(),
                     r["description"], r["category"], r["dataset_code"], json.dumps(r["definition"]),
                     "published" if body.visibility == "personal" else "draft", [], r["id"], ctx.user_id))
        audit.action(cur, ctx, "bi.report.copy", "bi_report", nid,
                     {"from": rid, "name": body.name, "visibility": body.visibility}, req=request)
    return {"data": {"id": str(nid)}}


# ---------------------------------------------------------------- admin: datasets
class DatasetIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = ""
    source_object: str = Field(pattern=r"^V2RETAIL\.PULSE\.[A-Z0-9_]+$")
    dimensions: list[dict]
    measures: list[dict]
    snapshot_dim: str | None = None
    scope_column: str | None = None
    refresh_note: str = ""
    status: str = Field("live", pattern=r"^(draft|live|deprecated)$")
    audience_roles: list[str] = []
    cache_seconds: int = Field(900, ge=0, le=86400)


def _validate_dataset(b: DatasetIn):
    dk = [x.get("key") for x in b.dimensions]
    mk = [x.get("key") for x in b.measures]
    if len(set(dk + mk)) != len(dk + mk) or not all(KEY.match(str(k or "")) for k in dk + mk):
        raise HTTPException(422, "Field and measure keys must be unique lower-case names.")
    for x in b.dimensions:
        if not COL.match(str(x.get("col", ""))) or not x.get("label"):
            raise HTTPException(422, "Every field needs a label and an upper-case column.")
        if x.get("grain") and x["grain"] not in GRAINS:
            raise HTTPException(422, "Field %s has an unknown time grain." % x.get("key"))
    bykey = {x["key"]: x for x in b.measures}
    for x in b.measures:
        if x.get("time_agg") not in (None, "sum", "last"):
            raise HTTPException(422, "Measure %s: time_agg must be sum or last." % x.get("key"))
        if x.get("agg") not in AGGS or not x.get("label"):
            raise HTTPException(422, "Measure %s needs a label and a known aggregation." % x.get("key"))
        if x["agg"] in ("ratio", "growth"):
            if x.get("num") not in bykey or x.get("den") not in bykey:
                raise HTTPException(422, "Ratio %s needs num and den measures." % x["key"])
        elif not COL.match(str(x.get("col", ""))):
            raise HTTPException(422, "Measure %s needs an upper-case column." % x["key"])
    if b.snapshot_dim and b.snapshot_dim not in dk:
        raise HTTPException(422, "Snapshot field must be one of the fields.")
    if b.scope_column and not COL.match(b.scope_column):
        raise HTTPException(422, "Scope column must be an upper-case column name.")


@router.put("/admin/bi/datasets/{code}")
def save_dataset(code: str, body: DatasetIn, request: Request, ctx: Ctx = Depends(require("reports.design"))):
    if not KEY.match(code):
        raise HTTPException(422, "Dataset code must be a short lower-case name.")
    _validate_dataset(body)
    with db.tx() as cur:
        cur.execute("insert into bi.dataset (code, name, description, source_object, dimensions, measures, snapshot_dim,"
                    " scope_column, refresh_note, status, audience_roles, cache_seconds, updated_by)"
                    " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict (code) do update set name = excluded.name,"
                    " description = excluded.description, source_object = excluded.source_object,"
                    " dimensions = excluded.dimensions, measures = excluded.measures, snapshot_dim = excluded.snapshot_dim,"
                    " scope_column = excluded.scope_column, refresh_note = excluded.refresh_note, status = excluded.status,"
                    " audience_roles = excluded.audience_roles, cache_seconds = excluded.cache_seconds,"
                    " updated_at = now(), updated_by = excluded.updated_by",
                    (code, body.name, body.description, body.source_object, json.dumps(body.dimensions),
                     json.dumps(body.measures), body.snapshot_dim, body.scope_column, body.refresh_note, body.status,
                     body.audience_roles, body.cache_seconds, ctx.user_id))
        audit.action(cur, ctx, "bi.dataset.save", "bi_dataset", code, body.model_dump(), req=request)
    return {"ok": True}


@router.get("/admin/bi/datasets")
def admin_datasets(ctx: Ctx = Depends(require("reports.design"))):
    with db.tx() as cur:
        cur.execute("select d.*, (select count(*) from bi.report r where r.dataset_code = d.code) reports from bi.dataset d"
                    " order by d.name")
        return {"data": [{**{k: v for k, v in d.items() if k != "updated_by"}, "updated_at": d["updated_at"].isoformat()}
                         for d in cur.fetchall()]}


# ---------------------------------------------------------------- pre-warming
def visual_query(d: dict, v: dict, report_filters: list[dict]) -> Query:
    """The exact query the browser sends for a visual with no slicer picked (keep in step with
    web/src/pages/reports/dynamic/Canvas.tsx: VisualCard), so a pre-warmed result is a cache hit."""
    dims = {x["key"]: x for x in d["dimensions"]}
    first = (v.get("dims") or [None])[0]
    is_time = bool(first) and first in dims and (bool(dims[first].get("grain")) or first == d["snapshot_dim"])
    sort = v.get("sort") or ({"by": first, "dir": "asc"} if is_time else
                             {"by": v["measures"][0], "dir": "desc"} if v.get("measures") else None)
    limit = v.get("limit") or (500 if v["type"] == "table" else 1 if v["type"] == "kpi" else 50)
    return Query(dataset=d["code"], dims=[] if v["type"] == "kpi" else (v.get("dims") or []), measures=v.get("measures") or [],
                 filters=[Filter(dim=f["dim"], op=f.get("op", "in"), values=f["values"]) for f in report_filters if f.get("values")],
                 sort=sort, limit=limit)


def prewarm() -> dict:
    """Worker job: run every published library report's visuals ahead of time (company scope), one query at
    a time, so the first person to open a report gets cached results instead of waiting in the warehouse
    queue. Runs in business hours only, so the warehouse can suspend at night."""
    import types
    from datetime import datetime, timedelta, timezone
    from ..db import Scope
    hour = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).hour
    if not 8 <= hour < 22:
        return {"skipped": "outside business hours (08:00-22:00 IST)"}
    ctx = types.SimpleNamespace(scope=Scope(all=True), user_id=None)
    with db.tx() as cur:
        # Skip very heavy datasets (big detail-table joins like DFS): they still cache on first open, but
        # pre-running dozens of them every cycle would overload the shared warehouse.
        cur.execute("select r.name, r.definition, d.* from bi.report r join bi.dataset d on d.code = r.dataset_code"
                    " where r.visibility = 'library' and r.status = 'published' and d.status = 'live'"
                    " and d.code not in ('dfs_sale', 'hero_sales', 'analytics')")
        rows = cur.fetchall()
    warmed = hits = failed = 0
    for r in rows:
        for p in (r["definition"] or {}).get("pages", []):
            for v in p.get("visuals") or []:
                try:
                    res = run_query(r, visual_query(r, v, (r["definition"] or {}).get("filters") or []), ctx)
                    hits += res["cache_hit"]
                    warmed += not res["cache_hit"]
                except Exception:  # noqa: BLE001 - a broken visual must not stop the others
                    failed += 1
    return {"reports": len(rows), "warmed": warmed, "already_cached": hits, "failed": failed}


# ---------------------------------------------------------------- seed
def seed(cur):
    for d in cat.DATASETS:
        cur.execute("insert into bi.dataset (code, name, description, source_object, dimensions, measures, snapshot_dim,"
                    " scope_column, refresh_note, audience_roles) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                    " on conflict (code) do nothing",
                    (d["code"], d["name"], d["description"], "%s.%s" % (cat.PULSE, d["view"]), json.dumps(d["dimensions"]),
                     json.dumps(d["measures"]), d.get("snapshot_dim"), d.get("scope"), d.get("refresh", ""),
                     d.get("audience", [])))
    for r in cat.REPORTS + cat.dfs_report_defs():
        cur.execute("select 1 from bi.report where visibility = 'library' and name = %s", (r["name"],))
        if cur.fetchone():
            continue
        cur.execute("insert into bi.report (visibility, name, description, category, dataset_code, definition, status)"
                    " values ('library',%s,%s,%s,%s,%s,'published')",
                    (r["name"], r["description"], r["category"], r["dataset"], json.dumps(r["definition"])))
