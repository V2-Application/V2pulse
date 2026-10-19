"""Ask a question (/api/v1/assist): a Claude panel on every report page. The page sends what it is showing and the
question; the answer is grounded in the report's own data, read with the caller's permissions and store scope.

What Claude sees, by page:
  /reports/view/<code>    table report: the full result of the report's registered query with the filters the page
                          last asked for (data gateway, so usually a cache hit), as a column profile and sample rows.
  /reports/dynamic/<id>   BI dashboard: the report definition, the dataset's fields and measures, and the results of
                          the queries the page's visuals last ran (these carry the user's slicer choices).
  anything else           the text on the screen (MIS tabs, Analytics, Simulation, Control Tower, ...).
Claude can ask for more than the first look shows: it replies with an <analyse> block (group/filter/sort the table
rows in Python, or a BI query on the dashboard's dataset) and gets the result back, up to MAX_ROUNDS times. Nothing
it asks for runs as SQL text; BI queries go through bi.build_query's validation and scope like any visual.

The model is Claude via Snowflake Cortex (PULSE_SVC; CORTEX_USER is granted to PUBLIC). Each question is audited as
assist.ask and rate-limited per user (bucket 'assist')."""
import json
import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import audit, db
from ..data import gateway
from ..data import snowflake as sf
from ..iam.context import Ctx
from ..web import current, require
from . import bi, reports

router = APIRouter(prefix="/api/v1/assist")
PERMS = ("reports.view", "analytics.view", "mis.view")
USE = require(*PERMS)
MODEL = "claude-sonnet-4-5"
RATE_PER_HOUR = 60          # questions per user per hour; each one is 1-5 Cortex calls
MAX_ROUNDS = 4              # analysis round trips before Claude must answer
MAX_TOKENS = 2500
SAMPLE_ROWS = 25
MAX_CONTEXT_CHARS = 60000
OPS_TABLE = {"aggregate", "rows", "distinct"}
AGGS = {"sum", "avg", "min", "max", "count", "count_distinct"}
FILTER_OPS = {"eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "contains", "is_null", "not_null"}


class Turn(BaseModel):
    role: str = Field(pattern=r"^(user|assistant)$")
    content: str = Field(max_length=12000)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    path: str = Field("", max_length=500)
    title: str = Field("", max_length=300)
    report_code: str | None = Field(None, max_length=80)
    bi_report_id: str | None = Field(None, max_length=60)
    bi_page: int = 0
    params: dict = Field(default_factory=dict)                       # table report: filters of the page's last load
    bi_queries: list[dict] = Field(default_factory=list, max_length=40)   # BI: the visuals' last queries
    page_text: str = Field("", max_length=30000)
    history: list[Turn] = Field(default_factory=list, max_length=20)


@router.get("/status")
def status(ctx: Ctx = Depends(current)):
    """Whether the panel is shown. Plain sign-in check, so pages of users without access do not log a denial."""
    return {"data": {"enabled": any(ctx.can(p) for p in PERMS), "model": MODEL}}


# ---------------------------------------------------------------- sources
class TableSource:
    kind = "table"

    def __init__(self, report: dict, columns: list[str], rows: list[list], as_of: str | None, params: dict, cap: int):
        self.report, self.columns, self.rows, self.as_of, self.params = report, columns, rows, as_of, params
        self.truncated = len(rows) >= cap
        self.idx = {c: i for i, c in enumerate(columns)}

    def describe(self) -> str:
        r = self.report
        out = ["REPORT: %s (code %s, table report)" % (r["name"], r["code"])]
        if r.get("description"):
            out.append("ABOUT: " + r["description"])
        out.append("FILTERS ON THE PAGE: " + (json.dumps(self.params) if self.params else "none"))
        out.append("DATA AS OF: %s. ROWS: %d%s." % (self.as_of or "?", len(self.rows),
                                                     " (capped: the report itself is truncated at this many rows, "
                                                     "largest first)" if self.truncated else ""))
        out.append("COLUMNS (profile over ALL rows):")
        out.extend(_profile(self.columns, self.rows))
        out.append("FIRST %d ROWS, in the order the page shows them (tab-separated):" % min(SAMPLE_ROWS, len(self.rows)))
        out.append(_tsv(self.columns, self.rows[:SAMPLE_ROWS]))
        return "\n".join(out)

    def tools(self) -> str:
        return TABLE_TOOLS % ", ".join(self.columns)

    def run(self, op: dict) -> str:
        return run_table_op(self.columns, self.rows, op)

    def label(self):
        return {"kind": "table", "name": self.report["name"], "rows": len(self.rows), "as_of": self.as_of}


class BiSource:
    kind = "bi"

    def __init__(self, report: dict, dataset: dict, page: int, shown: list[tuple[dict, dict | str]],
                 active_filters: list[dict], ctx: Ctx):
        self.report, self.d, self.page, self.shown, self.active, self.ctx = report, dataset, page, shown, active_filters, ctx

    def describe(self) -> str:
        r, d = self.report, self.d
        defn = r["definition"] or {}
        pages = defn.get("pages") or []
        out = ["DASHBOARD: %s (BI report, dataset %s: %s)" % (r["name"], d["code"], d["name"])]
        for t in (r.get("description"), d.get("description"), d.get("refresh_note")):
            if t:
                out.append("ABOUT: " + t)
        out.append("PAGES: " + " | ".join("%s%s" % ("> " if i == self.page else "", p.get("name") or "Page %d" % (i + 1))
                                          for i, p in enumerate(pages)))
        if 0 <= self.page < len(pages):
            vis = pages[self.page].get("visuals") or []
            out.append("VISUALS ON THE OPEN PAGE: " + "; ".join(
                "%s (%s)" % (v.get("title") or v.get("id"), v.get("type")) for v in vis))
        out.append("FILTERS IN FORCE (report filters + the user's slicers): " +
                   (json.dumps(self.active) if self.active else "none"))
        out.append("FIELDS (dims, use the key): " + "; ".join(
            "%s = %s%s" % (x["key"], x.get("label") or x["key"], " [date]" if bi._is_time(x, d) else "")
            for x in d["dimensions"]))
        out.append("MEASURES (use the key): " + "; ".join(
            "%s = %s (%s%s)" % (m["key"], m.get("label") or m["key"], m["agg"],
                                ", " + m["format"] if m.get("format") else "") for m in d["measures"]))
        if d["snapshot_dim"]:
            out.append("SNAPSHOTS: %s is the snapshot date; without a filter on it a query returns the LATEST snapshot "
                       "only (or a default window when grouping by it)." % d["snapshot_dim"])
        out.append("WHAT THE OPEN PAGE SHOWS (results of its visuals' queries, scoped to the user):")
        for q, res in self.shown:
            out.append("- query " + json.dumps({k: q[k] for k in ("dims", "measures", "filters") if q.get(k)}))
            out.append(res if isinstance(res, str) else _bi_table(res, 40))
        return "\n".join(out)

    def tools(self) -> str:
        return BI_TOOLS

    def run(self, op: dict) -> str:
        if op.get("op") != "bi_query":
            return "ERROR: on a dashboard the only op is bi_query."
        filters = list(op.get("filters") or [])
        if not op.get("ignore_active_filters"):
            mine = {f.get("dim") for f in filters}
            filters += [f for f in self.active if f.get("dim") not in mine]
        try:
            q = bi.Query(dataset=self.d["code"], dims=list(op.get("dims") or [])[:6],
                         measures=list(op.get("measures") or [])[:10], filters=filters[:30],
                         sort=op.get("sort") or None, limit=max(1, min(int(op.get("limit") or 100), 300)))
            res = bi.run_query(self.d, q, self.ctx)
        except HTTPException as e:
            return "ERROR: %s" % e.detail
        except Exception as e:  # noqa: BLE001 - pydantic errors etc. go back to Claude to correct
            return "ERROR: %s" % str(e)[:300]
        return _bi_table(res, 300)

    def label(self):
        return {"kind": "bi", "name": self.report["name"], "dataset": self.d["code"]}


class PageSource:
    kind = "page"

    def __init__(self, title: str, path: str, text: str):
        self.title, self.path, self.text = title, path, text

    def describe(self) -> str:
        return "PAGE: %s (%s)\nTEXT ON THE SCREEN (as the user sees it, may be cut off):\n%s" % (
            self.title or "?", self.path, self.text or "(nothing captured)")

    def tools(self) -> str:
        return "There is no analysis tool on this page: answer from the screen text only."

    def run(self, op: dict) -> str:
        return "ERROR: no analysis tool on this page."

    def label(self):
        return {"kind": "page", "name": self.title}


def _table_source(body: AskIn, ctx: Ctx):
    with db.tx() as cur:
        r = reports._report(cur, body.report_code, ctx)
        if r["kind"] != "table" or not r["query_code"]:
            return None, r
        cur.execute("select * from data.query_def where code = %s and enabled", (r["query_code"],))
        qdef = cur.fetchone()
    if not qdef:
        return None, r
    params = {k: str(v) for k, v in (body.params or {}).items()
              if k not in ("sort", "dir", "cursor", "limit", "format") and v not in (None, "")}
    try:
        res = gateway.run(qdef, params, ctx.scope, ctx.user_id)
    except gateway.GatewayError as e:
        raise HTTPException(e.status, str(e))
    return TableSource(r, res["columns"], res["rows"], res.get("as_of"), params, int(qdef.get("max_rows") or 50000)), r


def _bi_source(body: AskIn, ctx: Ctx):
    with db.tx() as cur:
        r = bi._report(cur, body.bi_report_id, ctx)
        d = bi._dataset(cur, r["dataset_code"], ctx)
    seen, shown, active = set(), [], None
    for qb in body.bi_queries[-24:][::-1]:            # newest first: the page as it is now
        if not isinstance(qb, dict) or qb.get("dataset") != d["code"]:
            continue
        key = json.dumps(qb, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        if active is None:
            active = [f for f in (qb.get("filters") or []) if isinstance(f, dict)]
        if len(shown) >= 12:
            continue
        try:
            res = bi.run_query(d, bi.Query(**qb), ctx)       # the page's own query, so a cache hit
        except HTTPException as e:
            res = "ERROR: %s" % e.detail
        except Exception as e:  # noqa: BLE001
            res = "ERROR: %s" % str(e)[:200]
        shown.append((qb, res))
    if active is None:
        active = list((r["definition"] or {}).get("filters") or [])
    return BiSource(r, d, body.bi_page, shown[::-1], active, ctx)


# ---------------------------------------------------------------- table analysis (pure Python, no SQL)
def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    return None


def _is_numeric(col_vals) -> bool:
    vals = [v for v in col_vals if v is not None and v != ""]
    return bool(vals) and all(_num(v) is not None for v in vals)


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return ("%.2f" % v).rstrip("0").rstrip(".") if abs(v) < 1e15 else "%g" % v
    s = str(v).replace("\t", " ").replace("\n", " ")
    return s if len(s) <= 60 else s[:57] + "..."


def _tsv(cols, rows) -> str:
    return "\n".join(["\t".join(cols)] + ["\t".join(_fmt(v) for v in r) for r in rows])


def _profile(cols, rows) -> list[str]:
    out = []
    for i, c in enumerate(cols):
        vals = [r[i] for r in rows]
        present = [v for v in vals if v is not None and v != ""]
        nulls = len(vals) - len(present)
        if _is_numeric(present):
            nums = [_num(v) for v in present]
            out.append("- %s: number; sum %s, min %s, max %s, avg %s, zero %d, blank %d" % (
                c, _fmt(sum(nums)), _fmt(min(nums)), _fmt(max(nums)), _fmt(sum(nums) / len(nums)),
                sum(1 for n in nums if n == 0), nulls))
        else:
            counts = {}
            for v in present:
                k = _fmt(v)
                counts[k] = counts.get(k, 0) + 1
            top = sorted(counts.items(), key=lambda x: -x[1])[:8]
            if len(counts) == len(present) and len(present) > 20:
                out.append("- %s: text, every value different (an id/name); e.g. %s; blank %d" % (
                    c, ", ".join(k for k, _ in top[:3]), nulls))
            else:
                out.append("- %s: text, %d distinct, blank %d; top: %s" % (
                    c, len(counts), nulls, ", ".join("%s (%d)" % kv for kv in top)))
    return out


def _cmp_val(v):
    n = _num(v)
    return n if n is not None else str(v).strip().lower()


def _match(row_v, op, val) -> bool:
    if op == "is_null":
        return row_v is None or row_v == ""
    if op == "not_null":
        return not (row_v is None or row_v == "")
    if row_v is None:
        return False
    if op in ("in", "not_in"):
        vals = {_cmp_val(x) for x in (val if isinstance(val, list) else [val])}
        hit = _cmp_val(row_v) in vals
        return hit if op == "in" else not hit
    if op == "contains":
        return str(val).lower() in str(row_v).lower()
    a, b = _cmp_val(row_v), _cmp_val(val)
    if isinstance(a, (int, float)) and not isinstance(b, (int, float)):
        try:
            b = float(str(val))
        except ValueError:
            return op == "ne"
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        a, b = str(a), str(b)
    return {"eq": a == b, "ne": a != b, "gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]


def _filter(cols, rows, filters):
    idx = {c: i for i, c in enumerate(cols)}
    for f in filters or []:
        c, op = f.get("col"), f.get("op", "eq")
        if c not in idx:
            raise ValueError("unknown column %s" % c)
        if op not in FILTER_OPS:
            raise ValueError("unknown filter op %s" % op)
        i, val = idx[c], f.get("value")
        rows = [r for r in rows if _match(r[i], op, val)]
    return rows


def _sort(cols, rows, sort):
    if not sort or sort.get("by") not in cols:
        return rows
    i = cols.index(sort["by"])
    desc = sort.get("dir") == "desc"
    present = [r for r in rows if r[i] is not None and r[i] != ""]
    blank = [r for r in rows if r[i] is None or r[i] == ""]
    def key(r):   # numbers before text, so a mixed column still sorts
        n = _num(r[i])
        return (0, n, "") if n is not None else (1, 0, str(r[i]).lower())
    return sorted(present, key=key, reverse=desc) + blank


def run_table_op(cols: list[str], rows: list[list], op: dict) -> str:
    kind = op.get("op")
    if kind not in OPS_TABLE:
        return "ERROR: op must be one of %s." % ", ".join(sorted(OPS_TABLE))
    try:
        sel = _filter(cols, rows, op.get("filters"))
        limit = max(1, min(int(op.get("limit") or 50), 200))
        if kind == "rows":
            want = [c for c in (op.get("columns") or cols) if c in cols][:20] or cols
            ix = [cols.index(c) for c in want]
            sel = _sort(cols, sel, op.get("sort"))
            return "%d rows match; showing %d.\n%s" % (len(sel), min(limit, len(sel)),
                                                        _tsv(want, [[r[i] for i in ix] for r in sel[:limit]]))
        if kind == "distinct":
            c = op.get("col")
            if c not in cols:
                return "ERROR: unknown column %s" % c
            i, counts = cols.index(c), {}
            for r in sel:
                counts[_fmt(r[i])] = counts.get(_fmt(r[i]), 0) + 1
            top = sorted(counts.items(), key=lambda x: -x[1])
            return "%d distinct values over %d rows; top %d:\n%s" % (
                len(top), len(sel), min(limit, len(top)), "\n".join("%s\t%d" % kv for kv in top[:limit]))
        # aggregate
        group = [c for c in (op.get("group_by") or []) if c in cols][:4]
        if len(group) != len(op.get("group_by") or []):
            return "ERROR: unknown group_by column."
        metrics = [m for m in (op.get("metrics") or []) if isinstance(m, dict)][:10]
        for m in metrics:
            if m.get("col") not in cols or m.get("agg") not in AGGS:
                return "ERROR: bad metric %s (col must be a column, agg one of %s)." % (json.dumps(m), ", ".join(sorted(AGGS)))
        gi = [cols.index(c) for c in group]
        buckets = {}
        for r in sel:
            buckets.setdefault(tuple(_fmt(r[i]) for i in gi), []).append(r)
        names = ["%s(%s)" % (m["agg"], m["col"]) for m in metrics]
        out_cols = group + ["rows"] + names

        def agg(rs, m):
            i = cols.index(m["col"])
            vals = [r[i] for r in rs if r[i] is not None and r[i] != ""]
            if m["agg"] == "count":
                return len(vals)
            if m["agg"] == "count_distinct":
                return len({_fmt(v) for v in vals})
            nums = [_num(v) for v in vals if _num(v) is not None]
            if not nums:
                return None
            return {"sum": sum(nums), "avg": sum(nums) / len(nums), "min": min(nums), "max": max(nums)}[m["agg"]]

        table = [list(k) + [len(rs)] + [agg(rs, m) for m in metrics] for k, rs in buckets.items()]
        table = _sort(out_cols, table, op.get("sort") or ({"by": names[0], "dir": "desc"} if names else {"by": "rows", "dir": "desc"}))
        total = ["TOTAL"] * len(group) + [len(sel)] + [agg(sel, m) for m in metrics] if group else None
        body = _tsv(out_cols, table[:limit] + ([total] if total else []))
        return "%d rows matched, %d groups; showing %d%s.\n%s" % (
            len(sel), len(table), min(limit, len(table)), " plus the TOTAL line" if total else "", body)
    except (ValueError, TypeError) as e:
        return "ERROR: %s" % e


def _bi_table(res: dict, limit: int) -> str:
    cols = [c["label"] + (" [%s]" % c["key"]) for c in res["columns"]]
    rows = res["rows"]
    head = "%d rows%s%s" % (len(rows), ", snapshot %s" % res["snapshot"] if res.get("snapshot") else "",
                            ", TRUNCATED at the query limit" if res.get("truncated") else "")
    return head + (" (first %d shown)" % limit if len(rows) > limit else "") + "\n" + _tsv(cols, rows[:limit])


# ---------------------------------------------------------------- Claude
SYSTEM = """You are the V2Pulse assistant inside V2 Retail's intranet (V2 Retail: an Indian value-fashion retailer, \
~470 stores, RDCs/DCs, SAP + Snowflake data). A colleague has a report open and is asking you about it. Today is %(today)s. \
The user is %(user)s (%(scope)s).

Your job: answer questions about THIS report, explain what it shows, find what matters (gaps, outliers, trends, \
top/bottom items), and suggest concrete actions with owners where the data supports it.

Rules:
- Ground every figure in the data below or in an <analyse> result. Never invent numbers, stores, articles or causes. If \
the data cannot answer, say so plainly and say what data would.
- Do not speculate about causes, seasons, plans or deadlines the data does not show. A likely reason may be offered only \
when it follows from the columns, marked "possible reason", in one line. Actions name who acts (zone, store, DC, \
allocation, buying team) and what to check, based on the figures.
- Profile figures are over ALL rows; the sample rows are only the first few. For anything beyond the profile (rankings, \
breakdowns, a specific store/article), run an analysis instead of guessing from the sample.
- Indian conventions: amounts in Rs with lakh / crore (1 lakh = 100,000; 1 crore = 10,000,000), quantities with \
thousands separators.
- Be brief and scannable: a one-line direct answer first, then bullets or a small markdown table (at most ~12 rows). \
No preamble, no restating the question.
- Store codes, article numbers, division/category codes and column names exactly as in the data. Never expand an \
abbreviation or code (LS, MW, KBW...) unless the data itself spells it out.
- If the report's rows are capped (see ROWS), say that counts and totals cover only the rows the report returned.
- If asked to change data, send something or do anything outside this page, explain that you can only read and analyse.

ANALYSIS TOOL
%(tools)s
To use it, reply with ONLY an <analyse> block containing one JSON object, or a JSON list of up to 3 objects, and \
nothing else, e.g. <analyse>{"op": "..."}</analyse>. You will get the results back and can analyse again (at most \
%(rounds)d times) or answer.

FINAL ANSWER
Markdown. Finish with exactly one line <followups>["...", "...", "..."]</followups>: 2-3 short follow-up questions the \
user might ask next about this report (in their words, under 60 characters each).

===== THE REPORT ON SCREEN =====
%(context)s
===== END OF REPORT ====="""

TABLE_TOOLS = """Operations over ALL rows of the report table (columns: %s):
- {"op": "aggregate", "group_by": [cols, max 4], "metrics": [{"col": c, "agg": "sum|avg|min|max|count|count_distinct"}], \
"filters": [...], "sort": {"by": "<output column, e.g. sum(GAP_QTY) or rows>", "dir": "desc"}, "limit": 50}
- {"op": "rows", "columns": [cols], "filters": [...], "sort": {"by": col, "dir": "asc|desc"}, "limit": 50}
- {"op": "distinct", "col": c, "filters": [...], "limit": 50}
filters: [{"col": c, "op": "eq|ne|in|not_in|gt|gte|lt|lte|contains|is_null|not_null", "value": v or [v, ...]}]"""

BI_TOOLS = """One operation, a query on the dashboard's dataset (same engine and store scope as the visuals):
- {"op": "bi_query", "dims": [field keys, max 6], "measures": [measure keys, max 10], \
"filters": [{"dim": key, "op": "in|not_in|between|gte|lte|last_days", "values": [..]}], \
"sort": {"by": key, "dir": "desc"}, "limit": 100}
The filters in force on the page are added automatically (yours replace them for the same field); \
set "ignore_active_filters": true to look beyond the user's current selection. Dates as YYYY-MM-DD."""


def _complete(messages: list[dict]) -> tuple[str, dict]:
    sql = "select snowflake.cortex.complete(%(model)s, parse_json(%(msgs)s), parse_json(%(opts)s)) as r"
    binds = {"model": MODEL, "msgs": json.dumps(messages), "opts": json.dumps({"max_tokens": MAX_TOKENS, "temperature": 0.2})}
    rows, _ = gateway.SF_POOL.run(lambda conn: sf.query(conn, sql, binds))
    raw = rows[0]["r"]
    obj = json.loads(raw) if isinstance(raw, str) else raw
    msg = obj["choices"][0].get("messages") or obj["choices"][0].get("message") or ""
    if isinstance(msg, dict):
        msg = msg.get("content") or ""
    return str(msg), obj.get("usage") or {}


ANALYSE = re.compile(r"<analyse>(.*?)</analyse>", re.S)
FOLLOWUPS = re.compile(r"<followups>(.*?)</followups>", re.S)


def _summary(op: dict) -> str:
    if op.get("op") == "aggregate":
        s = "Totals by %s" % (", ".join(op.get("group_by") or []) or "all rows")
    elif op.get("op") == "rows":
        s = "Rows" + (" sorted by %s" % op["sort"]["by"] if isinstance(op.get("sort"), dict) and op["sort"].get("by") else "")
    elif op.get("op") == "distinct":
        s = "Values of %s" % op.get("col")
    elif op.get("op") == "bi_query":
        s = "Query: %s by %s" % (", ".join(op.get("measures") or []) or "rows", ", ".join(op.get("dims") or []) or "total")
    else:
        s = str(op.get("op"))
    fl = op.get("filters") or []
    if fl:
        s += " where " + "; ".join("%s %s %s" % (f.get("col") or f.get("dim"), f.get("op", "eq"),
                                                 f.get("value", f.get("values", ""))) for f in fl[:3])
    return s[:200]


def converse(src, body: AskIn, ctx: Ctx) -> dict:
    context = src.describe()
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n... (cut here: use the analysis tool for the rest)"
    system = SYSTEM % {"today": datetime.now(timezone.utc).strftime("%d %b %Y"), "user": ctx.display_name,
                       "scope": ctx.scope_label or "company", "tools": src.tools(), "rounds": MAX_ROUNDS,
                       "context": context}
    msgs = [{"role": "system", "content": system}]
    for t in body.history[-12:]:
        msgs.append({"role": t.role, "content": t.content})
    msgs.append({"role": "user", "content": body.question})
    steps, tokens, text = [], 0, ""
    for rnd in range(MAX_ROUNDS + 1):
        text, usage = _complete(msgs)
        tokens += int(usage.get("total_tokens") or 0)
        m = ANALYSE.search(text)
        if not m or src.kind == "page":
            break
        if rnd == MAX_ROUNDS:   # out of rounds: answer with what it has
            msgs += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "No more analysis is available. Answer now from the results you have."}]
            text, usage = _complete(msgs)
            tokens += int(usage.get("total_tokens") or 0)
            break
        try:
            ops = json.loads(m.group(1).strip())
        except ValueError:
            ops = None
        ops = ops if isinstance(ops, list) else [ops]
        results = []
        for op in ops[:3]:
            if not isinstance(op, dict):
                results.append("ERROR: each operation must be a JSON object.")
                continue
            t0 = time.perf_counter()
            res = src.run(op)
            steps.append({"step": _summary(op), "ok": not res.startswith("ERROR"),
                          "ms": int((time.perf_counter() - t0) * 1000)})
            results.append(res[:20000])
        msgs += [{"role": "assistant", "content": text},
                 {"role": "user", "content": "<result>\n" + "\n\n".join(results) + "\n</result>"}]
    text = ANALYSE.sub("", text).strip()
    followups = []
    f = FOLLOWUPS.search(text)
    if f:
        try:
            followups = [str(x)[:90] for x in json.loads(f.group(1)) if str(x).strip()][:3]
        except ValueError:
            pass
        text = FOLLOWUPS.sub("", text).strip()
    return {"answer": text or "I could not form an answer. Try asking it another way.", "followups": followups,
            "steps": steps, "tokens": tokens}


@router.post("/ask")
def ask(body: AskIn, request: Request, ctx: Ctx = Depends(USE)):
    t0 = time.perf_counter()
    try:
        gateway._rate_limit(ctx.user_id, "assist", RATE_PER_HOUR)
    except gateway.GatewayError:
        raise HTTPException(429, "You have asked %d questions this hour. Try again later." % RATE_PER_HOUR)
    src, obj_type, obj_id = None, "page", body.path[:200]
    if body.report_code:
        tsrc, r = _table_source(body, ctx)
        obj_type, obj_id = "report", body.report_code
        src = tsrc or PageSource(r["name"] + (": " + r["description"] if r.get("description") else ""), body.path,
                                 body.page_text)
    elif body.bi_report_id:
        src, obj_type, obj_id = _bi_source(body, ctx), "bi_report", body.bi_report_id
    else:
        src = PageSource(body.title, body.path, body.page_text)
    try:
        out = converse(src, body, ctx)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        audit.action_now(ctx, "assist.ask", obj_type, obj_id, {"question": body.question[:500], "error": str(e)[:300]},
                         outcome="error", req=request)
        raise HTTPException(502, "Claude could not answer just now. Try again in a minute.")
    ms = int((time.perf_counter() - t0) * 1000)
    audit.action_now(ctx, "assist.ask", obj_type, obj_id,
                     {"question": body.question[:500], "path": body.path, "source": src.kind, "steps": len(out["steps"]),
                      "tokens": out["tokens"], "ms": ms, "answer_chars": len(out["answer"])}, req=request)
    return {"data": {"answer": out["answer"], "followups": out["followups"], "steps": out["steps"],
                     "source": src.label(), "scope": ctx.scope_label, "ms": ms, "model": MODEL}}
