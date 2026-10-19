"""The DFS (sale-plan) dump kinds beyond MIS reports, ported from planning-service/src/routes/saleplan.js:
day_trend (_runDayTrendDump), tna (_runTnaDump), bin_wise (_runBinDump), inv_report (_runInvDump +
_invSplitDump) and mis_day_load (_runMisLoad). Same inputs (the DFS schedule config, copied unchanged),
same engine calls (the copied ml-engine routes, called in-process), same folders and file names:
<folder>\\YYYY_MM_DD\\<file name>_<stamp>[__LEVEL_value...].<csv|xlsx>. Not ported: Excel templates and
the bin "one workbook, a sheet per level" option (DFS falls back to plain CSV without a template too)."""
import asyncio
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta

from . import engine as E

INV_FILTER_COLS = {"ST_TYP", "ST_CD", "SEG", "DIV", "SUB_DIV", "MJ", "ART_TYPE", "CLR", "SLOC", "SRC"}
NO_FOLDER = {"mis_day_load"}


def _suffix(levels, combo):
    return "__".join("%s_%s" % (E._mis_safe(lv), E._mis_safe(v)) for lv, v in zip(levels, combo))


def _save(resp, tmp, run):
    """Write a Starlette response (streamed or not) from a copied engine route to `tmp`."""
    if hasattr(resp, "body_iterator"):
        async def drain():
            with open(tmp, "wb") as fh:
                async for chunk in resp.body_iterator:
                    run.check()
                    fh.write(chunk if isinstance(chunk, (bytes, bytearray)) else str(chunk).encode("utf-8"))
        asyncio.run(drain())
    elif getattr(resp, "path", None):
        shutil.copyfile(resp.path, tmp)
    else:
        with open(tmp, "wb") as fh:
            fh.write(resp.body)


def _write_resp(make_resp, out_dir, stem, ext, run, paths):
    final = os.path.join(out_dir, "%s.%s" % (stem, ext))
    tmp = final + ".part"
    try:
        _save(make_resp(), tmp, run)
        run.check()
        E._publish(tmp, final, paths)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _per_level(levels, combos, write, run):
    """DFS: one file per combination that occurs; a failing combination is noted, the rest go on."""
    errors = []
    if not combos:
        raise ValueError("%s: no values - nothing written" % " + ".join(levels))
    for k, combo in enumerate(combos, 1):
        run.check()
        run.note = "File %d of %d: %s" % (k, len(combos), ", ".join("%s=%s" % x for x in zip(levels, combo)))
        try:
            write(combo)
        except (E.Stopped, TimeoutError):
            raise
        except Exception as e:  # noqa: BLE001
            errors.append(("%s: %s" % (combo, e))[:300])
    return errors


def _finish_errors(errors, paths):
    if errors and not paths:
        raise RuntimeError(errors[0])
    return errors


# ── day_trend ──────────────────────────────────────────────────────────────────────────────────────
def dump_day_trend(sc, out_dir, name, run, paths):
    from ..mis_engine.routes import sf_day_trend as dt
    from ..mis_engine.routes import sf_day_trend_report as dtr
    cfg = sc["config"] or {}
    run.note = "Resolving the latest loaded day"
    params = dt.trend_params()
    to = params.get("latest")
    if not to:
        raise RuntimeError("No stock day is loaded - nothing to trend")
    days, group_by, tags, meas = cfg.get("days") or 30, cfg.get("groupBy") or [], cfg.get("stockTags") or [], cfg.get("measures") or []
    col_filters = dict(cfg.get("colFilters") or {})
    extra = {}
    if cfg.get("reportSlug"):
        rep = (dtr.dt_defs(cfg["reportSlug"]).get("reports") or [None])[0]
        if not rep:
            raise RuntimeError("No Day Wise Trend Report '%s' - it was deleted or renamed" % cfg["reportSlug"])
        d = rep.get("definition") or {}
        days, group_by, tags, meas = d.get("days") or days, d.get("group_by") or [], d.get("stock_tags") or [], d.get("measures") or []
        col_filters.update(d.get("filters") or {})
        extra = {"v2r_plants": d.get("v2r_plants") or None, "store_only": bool(d.get("store_only")),
                 "stock_total": d.get("stock_total") or "add", "dim_conds": d.get("dim_filters") or None,
                 "meas_conds": d.get("col_filters") or None}
    span = max(1, min(int(days or 30), int(params.get("maxDays") or 31)))
    frm = (datetime.fromisoformat(str(to)[:10]) - timedelta(days=span - 1)).date().isoformat()
    ext = "csv" if cfg.get("format") == "csv" else "xlsx"
    scope = {"from_date": frm, "to_date": str(to)[:10], "group_by": group_by or None, "stock_tags": tags or None,
             "measures": meas or None, **{k: v for k, v in extra.items() if v is not None}}
    stem = "%s_%s_to_%s" % (name, frm, str(to)[:10])

    def write(filters, suffix):
        f = {**col_filters, **(filters or {})}
        req = dt.TrendReq(**scope, filters=f or None)
        _write_resp(lambda: dt.trend_export(req, ext), out_dir, stem + ("__" + suffix if suffix else ""), ext, run, paths)

    levels = [x for x in (cfg.get("levels") or []) if x]
    if not levels:
        run.note = "Writing %s .. %s" % (frm, to)
        write(None, "")
        return 0
    v = dt.trend_values(dt.ValuesReq(from_date=frm, to_date=str(to)[:10], columns=levels, filters=col_filters or None,
                                     dim_conds=scope.get("dim_conds")))
    errs = _per_level(levels, v.get("combos") or [], lambda c: write(dict(zip(levels, ([x] for x in c))), _suffix(levels, c)), run)
    _finish_errors(errs, paths)
    return 0


# ── tna ────────────────────────────────────────────────────────────────────────────────────────────
def dump_tna(sc, out_dir, name, run, paths):
    from ..mis_engine.routes import sf_tna as tna
    cfg = sc["config"] or {}
    run.note = "Checking what is loaded"
    params = tna.tna_params()
    stats = params.get("stats") or {}
    if not stats.get("rows"):
        raise RuntimeError("The TNA extract is empty - nothing to dump")
    stamp = str(stats.get("loadedAt") or "")[:10] or datetime.now(E.IST).strftime("%Y_%m_%d")
    ext = "csv" if cfg.get("format") == "csv" else "xlsx"
    scope = {"group_by": cfg.get("groupBy") or None, "measures": cfg.get("measures") or None,
             "stages": cfg.get("stages") or None, "stage_cols": cfg.get("stageCols") or None,
             "from_date": None, "to_date": None}
    col_filters = dict(cfg.get("colFilters") or {})
    stem = "%s_%s" % (name, stamp)

    def write(filters, suffix):
        f = {**col_filters, **(filters or {})}
        req = tna.TnaReq(**scope, filters=f or None)
        _write_resp(lambda: tna.tna_export(req, ext), out_dir, stem + ("__" + suffix if suffix else ""), ext, run, paths)

    levels = [x for x in (cfg.get("levels") or []) if x]
    if not levels:
        run.note = "Writing the whole extract as at %s" % stamp
        write(None, "")
        return 0
    v = tna.tna_values(tna.ValuesReq(columns=levels, filters=col_filters or None))
    _finish_errors(_per_level(levels, v.get("combos") or [],
                              lambda c: write(dict(zip(levels, ([x] for x in c))), _suffix(levels, c)), run), paths)
    return 0


# ── bin_wise ───────────────────────────────────────────────────────────────────────────────────────
def dump_bin(sc, out_dir, name, run, paths):
    from ..mis_engine.routes import sf_bin_report as b
    cfg = sc["config"] or {}
    col_filters, search = dict(cfg.get("colFilters") or {}), cfg.get("search") or None
    run.note = "Reading the bin snapshot"
    probe = b.bin_result(b.BinReq(date=None, col_filters=col_filters or None, search=search, page=1, limit=1))
    day = str(probe.get("date") or datetime.now(E.IST).strftime("%Y_%m_%d"))[:10]
    stem = "%s_%s" % (name, day)

    def write(filters, suffix):
        req = b.BinReq(date=day, search=search, col_filters={**col_filters, **(filters or {})})
        _write_resp(lambda: b.bin_export(req), out_dir, stem + ("__" + suffix if suffix else ""), "csv", run, paths)

    levels = [x for x in (cfg.get("levels") or []) if x]
    if not levels:
        write(None, "")
        return 0
    v = b.bin_values(b.BinValuesReq(date=day, col_filters=col_filters or None, search=search, columns=levels))
    _finish_errors(_per_level(levels, v.get("combos") or [],
                              lambda c: write(dict(zip(levels, ([x] for x in c))), _suffix(levels, c)), run), paths)
    return 0


# ── inv_report ─────────────────────────────────────────────────────────────────────────────────────
def dump_inv(sc, out_dir, name, run, paths):
    from ..mis_engine.routes import sf_inv_report as inv
    cfg = sc["config"] or {}
    slug = cfg.get("slug") or (cfg.get("slugs") or [None])[0]
    if not slug:
        raise RuntimeError("No inventory report in this schedule")
    want = (datetime.now(E.IST).date() - timedelta(days=1)).isoformat() if cfg.get("dateRule") == "yesterday" else None
    col_filters = dict(cfg.get("colFilters") or {})
    base_req = {"slug": slug, "units": cfg.get("units") or None, "search": cfg.get("search") or None,
                "meas_conds": cfg.get("measConds") or None}
    run.note = "Resolving the as-of day of %s" % slug
    as_of = inv.inv_as_of(inv.RunReq(slug=slug, as_of=want))["asOf"]
    stem = "%s_%s" % (name, as_of)
    levels = [x for x in (cfg.get("levels") or []) if x]
    if not levels:
        req = inv.RunReq(**base_req, as_of=as_of, col_filters=col_filters or None, dim_conds=cfg.get("dimConds") or None)
        run.note = "Writing %s as of %s" % (slug, as_of)
        _write_resp(lambda: inv.inv_export(req), out_dir, stem, "csv", run, paths)
        return 0
    # DFS _invSplitDump: the report ONCE for every level value (NDJSON, ordered by the levels), one file per value.
    run.note = "Reading every %s of %s in one go" % (" + ".join(levels), slug)
    req = inv.RunReq(**base_req, as_of=as_of, columns=levels, col_filters=col_filters or None,
                     dim_conds=cfg.get("dimConds") or None)
    fd, spool = tempfile.mkstemp(prefix="inv_split_", suffix=".ndjson")
    os.close(fd)
    total = 0
    try:
        _save(inv.inv_split(req, mode="csv"), spool, run)
        n, head, key, vals, rows = len(levels), None, None, None, []

        def flush():
            final = os.path.join(out_dir, "%s__%s.csv" % (stem, _suffix(levels, vals)))
            tmp = final + ".part"
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                fh.write(head["header_csv"] + "\r\n" + "\r\n".join(rows) + "\r\n")
            E._publish(tmp, final, paths)

        ended = False
        with open(spool, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                if head is None:
                    head = json.loads(line)
                    continue
                a = json.loads(line)
                if not isinstance(a, list):
                    ended = bool(a.get("end"))
                    continue
                k = a[:n]
                if k != key:
                    if key is not None:
                        run.check()
                        flush()
                    key, vals, rows = k, k, []
                rows.append(a[n])
                total += 1
                run.rows = total
        if not ended:
            raise RuntimeError("The inventory split stream broke off before its last row")
        if key is not None:
            flush()
        if not paths:
            raise ValueError("%s: no values on %s - nothing written" % (" + ".join(levels), as_of))
    finally:
        if os.path.exists(spool):
            os.remove(spool)
    return total


# ── mis_day_load (not a dump: the two day-wise load procedures) ───────────────────────────────────
def run_mis_day_load(sc, run):
    from ..mis_engine.routes import sf_var_combined as vc
    cfg = sc["config"] or {}
    frm, to = E.date_range(cfg.get("dateRule") or "yesterday")
    run.note = "Loading %s -> %s, %s" % (frm, to, "%d store(s)" % len(cfg["stores"]) if cfg.get("stores") else "all stores")
    fields = vc.LoadReq.model_fields
    req = vc.LoadReq(from_date=frm, to_date=to, stores=cfg.get("stores") or None, triggered_by="scheduled",
                     operator="V2Pulse", **({"heal_days": 7} if "heal_days" in fields else {}))
    out = vc.sf_var_combined_load(req) or {}
    if out.get("etMissing"):
        raise RuntimeError("ET store stock not loaded for %s - ET_STOCK_DATA had not landed yet; the next scheduled "
                           "load will fill it" % ", ".join(out["etMissing"]))
    return 0


KIND_RUNNERS = {"day_trend": dump_day_trend, "tna": dump_tna, "bin_wise": dump_bin, "inv_report": dump_inv}
