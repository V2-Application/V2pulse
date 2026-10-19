"""Simulation (/simulation): what-if tools under Libraries. Each simulator is an entry in SIMULATORS; the page lists
them and runs the live ones. Figures are computed in Snowflake through the data gateway (cache, rate limit, query
log), limited to the caller's store scope exactly like BI queries.

Store Revival: what happens to sales if the fill rate of a store's major categories is raised or lowered.
Source is the curated view PULSE.V_SIM_STORE_REVIVAL (latest SILVER.STORE_FILL_RATE snapshot + PEER_RATE, the
30-day sell-through of well-stocked store-categories in the same major category, capped at 1). Per store x
major category:
  units  = target fill rate x MBQ - stock. Mode 'raise' (default) only tops up categories below the target;
           'set' also takes stock out of categories above it. Raising is capped by RDC stock, shared pro-rata
           between the stores in the run that the RDC serves ('cap').
  extra  = units x sell-through x ASP. Raising uses PEER_RATE ('peer', conservative) or the store's own
           30-day sell-through capped at 1 ('own'); lowering always uses the store's own rate.
Rows without a peer rate (fewer than 10 well-stocked stores in the category) or without an ASP are skipped and
counted, never guessed."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..data import gateway
from ..iam.context import Ctx
from ..web import require

router = APIRouter(prefix="/api/v1/simulation")
VIEW = require("simulation.view")
SRC = "V2RETAIL.PULSE.V_SIM_STORE_REVIVAL"
CACHE_SECONDS = 900
RATE_PER_HOUR = 900
MAX_ROWS = 500

SIMULATORS = [
    {"key": "store-revival", "name": "Store Revival", "status": "live",
     "description": "Raise or lower the fill rate of a store's categories and see the effect on sales."},
    {"key": "rebalancing", "name": "Stock rebalancing", "status": "planned",
     "description": "Move stock from over-filled stores to short ones."},
    {"key": "mbq-cover", "name": "MBQ / cover days", "status": "planned",
     "description": "Change the days of cover behind a category's MBQ."},
    {"key": "display-space", "name": "Display space", "status": "planned",
     "description": "Shift fixture space between categories."},
    {"key": "markdown", "name": "Markdown", "status": "planned",
     "description": "Price cuts on ageing stock: sell-through and margin."},
]


def _run(code: str, sql: str, binds: dict, key: list, ctx: Ctx, max_rows: int) -> list[dict]:
    try:
        res = gateway.run_sql("sim:" + code, sql, binds, key, ctx.user_id, cache_seconds=CACHE_SECONDS,
                              max_rows=max_rows, bucket="bi", limit=RATE_PER_HOUR)
    except gateway.GatewayError as e:
        raise HTTPException(e.status, str(e))
    names = [c.lower() for c in res["columns"]]
    return [dict(zip(names, r)) for r in res["rows"]]


def _scope(ctx: Ctx, binds: dict) -> tuple[str, list]:
    """SQL condition limiting rows to the caller's stores, and the cache-key part for it."""
    if ctx.scope.all:
        return "1=1", ["ALL"]
    sites = sorted(ctx.scope.sites or [])
    if not sites:
        return "1=0", ["NONE"]
    names = []
    for i, s in enumerate(sites):
        binds["site%d" % i] = s
        names.append("%%(site%d)s" % i)
    return "st_cd in (%s)" % ", ".join(names), sites


def _num(v):
    return float(v) if v is not None else None


@router.get("/catalog")
def catalog(ctx: Ctx = Depends(VIEW)):
    return {"data": SIMULATORS, "user": {"name": ctx.display_name, "scope": ctx.scope_label}}


@router.get("/store-revival/options")
def options(ctx: Ctx = Depends(VIEW)):
    """Pickers: stores (with zone) and division x major category, within the caller's scope."""
    binds: dict = {}
    cond, skey = _scope(ctx, binds)
    stores = _run("store-revival-stores",
                  "select st_cd, max(st_nm) st_nm, max(zone_code) zone_code, max(rdc_cd) rdc_cd, sum(sale_v_l30d) sale_v,"
                  " max(as_of_dt) as_of from %s where %s group by st_cd order by st_cd" % (SRC, cond),
                  binds, ["stores", skey], ctx, 2000)
    cats = _run("store-revival-cats",
                "select div, maj_cat, count(*) n from %s where %s group by div, maj_cat order by div, maj_cat" % (SRC, cond),
                binds, ["cats", skey], ctx, 5000)
    as_of = max((str(s["as_of"]) for s in stores if s.get("as_of")), default=None)
    return {"data": {
        "as_of": as_of,
        "stores": [{"code": s["st_cd"], "name": s["st_nm"], "zone": s["zone_code"], "rdc": s["rdc_cd"],
                    "sale_v_l30d": _num(s["sale_v"])} for s in stores],
        "zones": sorted({s["zone_code"] for s in stores if s["zone_code"]}),
        "categories": [{"div": c["div"], "maj_cat": c["maj_cat"]} for c in cats if c["maj_cat"]],
    }}


class RunIn(BaseModel):
    scope: str = Field("store", pattern="^(store|zone|all)$")
    code: str | None = Field(None, max_length=60)
    div: str | None = Field(None, max_length=60)
    maj_cat: str | None = Field(None, max_length=80)
    target: float = Field(0.85, ge=0.1, le=1.5)
    mode: str = Field("raise", pattern="^(raise|set)$")   # raise: only categories below target; set: raise and lower
    basis: str = Field("peer", pattern="^(peer|own)$")
    cap: bool = True


@router.post("/store-revival/run")
def run(body: RunIn, ctx: Ctx = Depends(VIEW)):
    if body.scope != "all" and not body.code:
        raise HTTPException(422, "Pick a store or a zone.")
    binds: dict = {"target": round(body.target, 4), "basis": body.basis, "cap": 1 if body.cap else 0,
                   "mode": body.mode}
    cond, skey = _scope(ctx, binds)
    where = [cond]
    if body.scope == "store":
        where.append("st_cd = %(code)s")
        binds["code"] = body.code
    elif body.scope == "zone":
        where.append("zone_code = %(code)s")
        binds["code"] = body.code
    if body.div:
        where.append("div = %(div)s")
        binds["div"] = body.div
    if body.maj_cat:
        where.append("maj_cat = %(maj_cat)s")
        binds["maj_cat"] = body.maj_cat

    base = (
        "with b as (select * from %s where %s),"
        " s as (select * from b where peer_rate is not null and asp is not null and mbq > 0),"
        " r as (select s.*, least(iff(stock_q > 0, sale_q_l30d / stock_q, peer_rate), 1) own_rate,"
        "       iff(%%(mode)s = 'raise', greatest(%%(target)s * mbq - stock_q, 0), %%(target)s * mbq - stock_q) delta"
        "       from s),"
        " need as (select rdc_cd, maj_cat, max(coalesce(rdc_stk_q, 0)) rdc_q, sum(greatest(delta, 0)) need_tot"
        "          from r group by rdc_cd, maj_cat),"
        " a as (select r.*, case when r.delta >= 0 then iff(%%(cap)s = 1,"
        "         r.delta * least(1, coalesce(n.rdc_q / nullif(n.need_tot, 0), 1)), r.delta) else r.delta end u"
        "       from r join need n on equal_null(n.rdc_cd, r.rdc_cd) and n.maj_cat = r.maj_cat),"
        " x as (select a.*, iff(u >= 0, iff(%%(basis)s = 'own', own_rate, peer_rate), own_rate) rate_used,"
        "       u * iff(u >= 0, iff(%%(basis)s = 'own', own_rate, peer_rate), own_rate) * asp extra from a) "
    ) % (SRC, " and ".join(where))
    key = [body.model_dump(), skey]

    tot = _run("store-revival-totals", base +
               "select (select sum(sale_v_l30d) from b) sale_base,"
               " (select count_if(peer_rate is null or asp is null) from b) skipped,"
               " (select max(as_of_dt) from b) as_of, (select count(distinct st_cd) from b) stores,"
               " coalesce(sum(extra), 0) extra, coalesce(sum(u), 0) units, coalesce(sum(abs(u) * asp), 0) stock_value,"
               " coalesce(sum(mbq), 0) mbq, coalesce(sum(least(stock_q, mbq)), 0) fill_before,"
               " coalesce(sum(least(greatest(stock_q + u, 0), mbq)), 0) fill_after,"
               " count_if(abs(u) >= 1) changed, count(*) rows_used from x",
               binds, key + ["totals"], ctx, 1)
    by_div = _run("store-revival-div", base +
                  "select div, sum(extra) extra, sum(u) units from x group by div order by 2 desc",
                  binds, key + ["div"], ctx, 100)
    rows = _run("store-revival-rows", base +
                "select st_cd, st_nm, zone_code, div, maj_cat, mbq, stock_q, u, rate_used, extra,"
                " stock_q / mbq fr_before, greatest(stock_q + u, 0) / mbq fr_after"
                " from x where abs(u) >= 1 order by abs(extra) desc limit %d" % MAX_ROWS,
                binds, key + ["rows"], ctx, MAX_ROWS)
    t = tot[0] if tot else {}
    mbq = _num(t.get("mbq")) or 0
    return {"data": {
        "inputs": body.model_dump(),
        "as_of": str(t["as_of"]) if t.get("as_of") else None,
        "totals": {"extra_sales": _num(t.get("extra")) or 0, "units": _num(t.get("units")) or 0,
                   "stock_value": _num(t.get("stock_value")) or 0, "sales_base": _num(t.get("sale_base")) or 0,
                   "fill_before": (_num(t.get("fill_before")) or 0) / mbq if mbq else None,
                   "fill_after": (_num(t.get("fill_after")) or 0) / mbq if mbq else None,
                   "changed": int(t.get("changed") or 0), "rows_used": int(t.get("rows_used") or 0),
                   "skipped": int(t.get("skipped") or 0), "stores": int(t.get("stores") or 0)},
        "by_div": [{"div": d["div"], "extra_sales": _num(d["extra"]) or 0, "units": _num(d["units"]) or 0} for d in by_div],
        "rows": [{"store": r["st_cd"], "store_name": r["st_nm"], "zone": r["zone_code"], "div": r["div"],
                  "maj_cat": r["maj_cat"], "mbq": _num(r["mbq"]), "stock": _num(r["stock_q"]), "units": _num(r["u"]),
                  "rate": _num(r["rate_used"]), "extra_sales": _num(r["extra"]),
                  "fr_before": _num(r["fr_before"]), "fr_after": _num(r["fr_after"])} for r in rows],
        "truncated": len(rows) >= MAX_ROWS,
    }}
