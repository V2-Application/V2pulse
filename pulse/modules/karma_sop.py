"""Karma ST SOP Checklist reports in Retail Operations > SOP Checklist: two tables and a dashboard.

  karma-sop-detail   one row per checklist submission (GOLD.RPT_KARMA_ST_SOP_CHECKLIST, REPORT_KEY = ST_SOP)
  karma-sop-summary  store-wise TD / MTD budget, actual and compliance (GOLD.VW_KARMA_STORE_DAILY_SUMMARY, ST_SOP)
  dashboard          completion, YES / NO / NA and trend over the store x day x checklist fact
                     (PULSE.V_BI_KARMA_CHECKLIST_SHEET, filtered to the ST SOP CHECKLIST report)

The table views follow the HU Swap pattern: a thin PULSE wrapper over the GOLD view that adds LAST_REFRESHED. Nothing in
GOLD is changed. The seed replaces the two earlier hand-made reports in the folder ("ST SOP Checklist - Summary / Detail");
it runs again on every start but only ever touches those and its own rows, so an admin's later edits are kept."""
import json
import logging
import uuid

from .bi_catalog import D, M, V

log = logging.getLogger("pulse")

PULSE = "V2RETAIL.PULSE"
TREE = "dept:RETAIL_OPS"
FOLDER_ID = "ebcb5039-fdcb-4942-909d-3a3bb177b0cd"      # Retail Operations > SOP Checklist
FOLDER_NAME = "SOP Checklist"
CATEGORY = "Retail Operations"
AUDIENCE = ["EXEC", "MIS_CENTRAL", "PLATFORM", "DEPT_MIS_OWNER", "HO_STAFF", "ZONAL_HEAD", "REGIONAL_HEAD", "AREA_MGR",
            "STORE_MGR"]
_T, _S, _C = "Time", "Store", "Checklist"


def P(label, type_="str", choices=None, default=None, max_len=60):
    p = {"type": type_, "label": label, "required": False, "max_len": max_len}
    if choices:
        p["choices"] = choices
    if default is not None:
        p["default"] = default
    return p


DETAIL_VIEW, SUMMARY_VIEW = "V_WM_KARMA_SOP_DETAIL", "V_WM_KARMA_SOP_SUMMARY"
_LAST = ("cross join (select to_varchar(max(%s), 'DD-MM-YYYY HH24:MI') from %s) r (LAST_REFRESHED)")

TABLES = [
    dict(
        code="karma-sop-detail", name="Karma SOP Checklist — submissions (detail)",
        description="Every ST SOP checklist submission: sheet, store, zone / region / cluster, questions attempted, completion %, "
                    "YES / NO / NA counts, who submitted and when, status, approval and the proof link. Defaults to the last "
                    "7 days; change the dates, zone, store, checklist or status above.",
        view=DETAIL_VIEW,
        view_sql="select b.*, r.LAST_REFRESHED from (select * from V2RETAIL.GOLD.RPT_KARMA_ST_SOP_CHECKLIST) b "
                 + _LAST % ("SUBMITTED_DATETIME_IST", "V2RETAIL.GOLD.RPT_KARMA_ST_SOP_CHECKLIST"),
        sql="""select "DATE" "date", SHEET_NAME "checklist", REF_NO "ref no", STORE_CODE "store", COVERAGE "coverage", ZONE "zone",
       REG "region", CLUSTER "cluster", CLUSTER_MANAGER "cluster manager", QUESTIONS_ATTEMPTED "attempted",
       TOTAL_QUESTIONS "total questions", round(COMPLETION_PCT, 1) "completion pct", YES_COUNT "yes", NO_COUNT "no",
       NA_COUNT "na", SUBMITTED_BY "submitted by", SUBMITTED_DATETIME_IST "submitted at (ist)", STATUS "status",
       APPROVAL_STATUS "approval", EVIDENCE_COUNT "evidence", PROOF_URL "proof", LAST_REFRESHED "refreshed"
from V2RETAIL.PULSE.V_WM_KARMA_SOP_DETAIL
where {scope}
  and "DATE" between coalesce(%(from_date)s, dateadd(day, -6, (select max("DATE") from V2RETAIL.PULSE.V_WM_KARMA_SOP_DETAIL)))
                 and coalesce(%(to_date)s, (select max("DATE") from V2RETAIL.PULSE.V_WM_KARMA_SOP_DETAIL))
  and (%(zone)s is null or upper(ZONE) = upper(%(zone)s)) and (%(store)s is null or STORE_CODE = upper(%(store)s))
  and (%(checklist)s is null or SHEET_NAME ilike '%%' || %(checklist)s || '%%')
  and (%(status)s is null or upper(STATUS) = upper(%(status)s))
order by "DATE" desc, SHEET_ORDER, STORE_CODE""",
        params={"from_date": P("From date", "date"), "to_date": P("To date", "date"), "zone": P("Zone"),
                "store": P("Store code", max_len=10), "checklist": P("Checklist (contains)"), "status": P("Status")},
        scope="STORE_CODE"),
    dict(
        code="karma-sop-summary", name="Karma SOP Checklist — store TD/MTD (summary)",
        description="Store-wise ST SOP checklist budget against actual for today (TD) and month to date (MTD), with compliance "
                    "% and the zone, region, cluster and cluster manager. Defaults to the latest day.",
        view=SUMMARY_VIEW,
        view_sql="select b.*, r.LAST_REFRESHED from (select * from V2RETAIL.GOLD.VW_KARMA_STORE_DAILY_SUMMARY"
                 " where REPORT_KEY = 'ST_SOP') b " + _LAST % ("AS_OF_DATE", "V2RETAIL.GOLD.VW_KARMA_STORE_DAILY_SUMMARY"),
        sql="""select ST_CD "store", ST_NM "store name", ZONE "zone", REG "region", CLUSTER "cluster",
       CLUSTER_MANAGER "cluster manager", ST_OPENING_DATE "opening date", MTD_DAYS "mtd days", TD_BGT "td budget",
       TD_ACT "td actual", round(TD_COMP_PCT * 100, 1) "td compliance pct", MTD_BGT "mtd budget", MTD_ACT "mtd actual",
       round(MTD_COMP_PCT * 100, 1) "mtd compliance pct", AS_OF_DATE "as of"
from V2RETAIL.PULSE.V_WM_KARMA_SOP_SUMMARY
where {scope}
  and AS_OF_DATE = coalesce(%(as_of)s, (select max(AS_OF_DATE) from V2RETAIL.PULSE.V_WM_KARMA_SOP_SUMMARY))
  and (%(zone)s is null or upper(ZONE) = upper(%(zone)s)) and (%(store)s is null or ST_CD = upper(%(store)s))
order by MTD_COMP_PCT nulls last, ST_CD""",
        params={"as_of": P("As of date", "date"), "zone": P("Zone"), "store": P("Store code", max_len=10)},
        scope="ST_CD"),
]

# ---------------------------------------------------------------- dashboard (dataset over the existing PULSE view)
SOP_FILTER = [{"dim": "report", "op": "in", "values": ["ST SOP CHECKLIST"]}]

# The view already exists in Snowflake (all three Karma checklist reports); this is its current definition, so a
# regenerated bi-sql re-creates it unchanged. The dashboard narrows it to ST SOP with the report filter above.
SHEET_SQL = """SELECT f.REPORT_DATE AS SNAPSHOT_DATE,
  CASE f.REPORT_KEY WHEN 'SM_YN' THEN 'SM YES/NO' WHEN 'ASM_YN' THEN 'ASM YES/NO' WHEN 'ST_SOP' THEN 'ST SOP CHECKLIST' ELSE f.REPORT_KEY END AS REPORT,
  f.SHEET_NAME AS CHECKLIST, f.SHEET_ORDER AS CHECKLIST_ORDER,
  f.STORE_CODE, COALESCE(h.STORE_NAME, d.STORE_NAME) AS STORE_NAME, COALESCE(h.STATE, d.STORE_STATE) AS STATE,
  COALESCE(h.ZONE_CODE, d.ZONE_CODE) AS ZONE_CODE, COALESCE(h.REGION_CODE, d.REGION_CODE) AS REGION_CODE, COALESCE(h.CLUSTER_CODE, d.CLUSTER_CODE) AS CLUSTER_CODE,
  h.CLUSTER_HEAD_NAME, d.CLUSTER_MANAGER,
  f.SUBMISSIONS, f.ANSWERS_TOTAL AS QUESTIONS_TOTAL, f.ANSWERS_ATTEMPTED AS QUESTIONS_ATTEMPTED,
  f.YES_COUNT, f.NO_COUNT, f.NA_COUNT, f.COMPLETION_PCT
FROM V2RETAIL.GOLD.FACT_KARMA_STORE_DAY_SHEET f
LEFT JOIN V2RETAIL.PULSE.V_STORE_HIERARCHY h ON h.STORE_CODE = f.STORE_CODE
LEFT JOIN V2RETAIL.SILVER.DIM_KARMA_STORE d ON d.STORE_CODE = f.STORE_CODE"""

DATASETS = [dict(
    code="karma_sop", name="Karma - ST SOP checklist",
    description="ST SOP checklist submissions by store, day and checklist sheet: submissions, questions attempted, YES / NO / NA "
                "answers and completion %. Figures add up over the dates you pick.",
    view="V_BI_KARMA_CHECKLIST_SHEET", sql=SHEET_SQL, snapshot_dim=None, scope="STORE_CODE", cache=1800, audience=AUDIENCE,
    refresh="Daily with the Karma checklist build (GOLD.FACT_KARMA_STORE_DAY_SHEET).",
    dimensions=[
        D("fiscal_year", "Fiscal year", "SNAPSHOT_DATE", "date", _T, "fy"), D("month", "Month", "SNAPSHOT_DATE", "date", _T, "month"),
        D("week", "Week", "SNAPSHOT_DATE", "date", _T, "week"), D("weekday", "Day of week", "SNAPSHOT_DATE", "number", _T, "dow"),
        D("date", "Date", "SNAPSHOT_DATE", "date", _T),
        D("report", "Report", "REPORT", group=_C), D("checklist", "Checklist", "CHECKLIST", group=_C),
        D("zone", "Zone", "ZONE_CODE", group=_S), D("region", "Region", "REGION_CODE", group=_S),
        D("cluster", "Cluster", "CLUSTER_CODE", group=_S), D("store", "Store code", "STORE_CODE", group=_S),
        D("store_name", "Store", "STORE_NAME", group=_S), D("state", "State", "STATE", group=_S),
        D("cluster_head", "Cluster head", "CLUSTER_HEAD_NAME", group=_S), D("cluster_manager", "Cluster manager", "CLUSTER_MANAGER", group=_S),
    ],
    measures=[
        M("submissions", "Submissions", "SUBMISSIONS", time_agg="sum"), M("total_q", "Questions", "QUESTIONS_TOTAL", time_agg="sum"),
        M("attempted", "Questions attempted", "QUESTIONS_ATTEMPTED", time_agg="sum"),
        M("yes", "YES", "YES_COUNT", time_agg="sum"), M("no", "NO", "NO_COUNT", time_agg="sum"), M("na", "NA", "NA_COUNT", time_agg="sum"),
        M("stores", "Stores", "STORE_CODE", agg="count_distinct", time_agg="sum"),
        M("completion", "Completion %", agg="ratio", num="attempted", den="total_q", fmt="pct"),
        M("yes_rate", "YES % of attempted", agg="ratio", num="yes", den="attempted", fmt="pct"),
        M("no_rate", "NO % of attempted", agg="ratio", num="no", den="attempted", fmt="pct"),
    ],
)]

_LOC = [{"dim": "zone"}, {"dim": "region"}, {"dim": "cluster"}]
_RANGE = {"dim": "date", "mode": "range", "preset": "mtd"}


def _kpis(*items):
    w = 12 // len(items)
    return [V("k%d" % (i + 1), "kpi", t, i * w, 0, w, 2, measures=[m]) for i, (t, m) in enumerate(items)]


REPORTS = [dict(
    name="Karma SOP Checklist — dashboard", category=CATEGORY, dataset="karma_sop", audience=AUDIENCE,
    report_code="karma-sop-dashboard",
    description="ST SOP checklist completion and answers by zone, region, cluster, checklist, store and day: how much was filled, "
                "how many YES / NO / NA, and where completion is lowest.",
    definition={"version": 1, "filters": SOP_FILTER, "pages": [
        {"id": "p1", "name": "Overview", "slicers": [_RANGE] + _LOC + [{"dim": "checklist"}],
         "visuals": _kpis(("Submissions", "submissions"), ("Stores reporting", "stores"), ("Completion %", "completion"),
               ("NO % of attempted", "no_rate")) + [
             V("c1", "column", "Completion % by zone", 0, 2, 6, 6, dims=["zone"], measures=["completion"],
               sort={"by": "completion", "dir": "asc"}, limit=12),
             V("b1", "bar", "Completion % by checklist", 6, 2, 6, 6, dims=["checklist"], measures=["completion"],
               sort={"by": "completion", "dir": "asc"}, limit=12),
             V("t1", "table", "Zones", 0, 8, 12, 6, dims=["zone"],
               measures=["submissions", "stores", "total_q", "attempted", "completion", "yes", "no", "na", "no_rate"],
               sort={"by": "submissions", "dir": "desc"}, limit=50)]},
        {"id": "p2", "name": "Trend", "slicers": [_RANGE] + _LOC + [{"dim": "checklist"}],
         "visuals": [
             V("l1", "line", "Completion % by day", 0, 0, 12, 6, dims=["date"], measures=["completion"], sort={"by": "date", "dir": "asc"}),
             V("l2", "line", "Submissions by day", 0, 6, 6, 6, dims=["date"], measures=["submissions"], sort={"by": "date", "dir": "asc"}),
             V("l3", "line", "YES and NO answers by day", 6, 6, 6, 6, dims=["date"], measures=["yes", "no"], sort={"by": "date", "dir": "asc"})]},
        {"id": "p3", "name": "Checklists", "slicers": [_RANGE] + _LOC,
         "visuals": [
             V("t1", "table", "By checklist", 0, 0, 12, 8, dims=["checklist"],
               measures=["submissions", "stores", "total_q", "attempted", "completion", "yes", "no", "na", "yes_rate", "no_rate"],
               sort={"by": "submissions", "dir": "desc"}, limit=50),
             V("m1", "matrix", "Completion %: checklist by zone", 0, 8, 12, 8, dims=["checklist", "zone"], measures=["completion"])]},
        {"id": "p4", "name": "Stores", "slicers": [_RANGE] + _LOC + [{"dim": "checklist"}],
         "visuals": [
             V("b1", "bar", "Lowest 15 stores by completion %", 0, 0, 5, 9, dims=["store_name"], measures=["completion"],
               sort={"by": "completion", "dir": "asc"}, limit=15),
             V("t1", "table", "All stores", 5, 0, 7, 9, dims=["store", "store_name", "cluster_manager"],
               measures=["submissions", "attempted", "completion", "yes", "no", "na", "no_rate"],
               sort={"by": "completion", "dir": "asc"}, limit=1000)]},
    ]},
)]


# ---------------------------------------------------------------- Snowflake views
def view_sql(t):
    return "create or replace view %s.%s\n  copy grants\n  comment = 'V2Pulse report: %s'\nas %s;" % (PULSE, t["view"], t["name"].replace("—", "-"), t["view_sql"])


def snowflake_sql():
    out = ["-- V2Pulse Karma SOP Checklist: PULSE wrappers over the GOLD views. Generated from pulse/modules/karma_sop.py.",
           "-- Run as ACCOUNTADMIN on COMPUTE_WH. Safe to re-run. GOLD is not changed.", "use role ACCOUNTADMIN;",
           "use warehouse COMPUTE_WH;", ""]
    for t in TABLES:
        out += [view_sql(t), "grant select on view %s.%s to role PULSE_SVC_ROLE;" % (PULSE, t["view"]), ""]
    return "\n".join(out)


# ---------------------------------------------------------------- seed
def _folder(cur):
    cur.execute("select id from nav.folder where id = %s and not is_archived", (FOLDER_ID,))
    f = cur.fetchone()
    if f:
        return f["id"]
    cur.execute("select id from nav.folder where tree = %s and section = 'reports' and lower(name) = lower(%s) and not is_archived",
                (TREE, FOLDER_NAME))
    f = cur.fetchone()
    return f["id"] if f else None


def _retire_old(cur, folder_id, keep_codes):
    """Delete the earlier 'ST SOP Checklist - Summary / Detail' reports filed in this folder (and their query definitions
    when nothing else uses them). Only matches that name pattern inside this one folder."""
    cur.execute("select r.id, r.code, r.name, r.query_code from nav.item i join rpt.report r on r.id = i.report_id"
                " where i.folder_id = %s and r.name ilike 'ST SOP Checklist%%' and r.code <> all(%s)", (folder_id, list(keep_codes)))
    for r in cur.fetchall():
        cur.execute("delete from nav.item where report_id = %s", (r["id"],))
        cur.execute("delete from rpt.report where id = %s", (r["id"],))
        if r["query_code"]:
            cur.execute("delete from data.query_def q where q.code = %s and not exists"
                        " (select 1 from rpt.report x where x.query_code = q.code)", (r["query_code"],))
        log.info("karma_sop: removed old report %s (%s)", r["name"], r["code"])


def seed(cur, rid):
    """Table reports, the dashboard link, and filing in Retail Operations > SOP Checklist. Run after bi.dataset / bi.report seed."""
    for t in TABLES:
        cur.execute("insert into data.query_def (code, name, sql_template, params, scope_column, permission, cache_seconds, max_rows)"
                    " values (%s,%s,%s,%s,%s,'reports.view',1800,100000) on conflict (code) do update set name = excluded.name,"
                    " sql_template = excluded.sql_template, params = excluded.params, scope_column = excluded.scope_column",
                    (t["code"], t["name"], t["sql"], json.dumps(t["params"]), t["scope"]))
        cur.execute("insert into rpt.report (code, name, description, layer, owner_dept, owner_name, kind, query_code, tags)"
                    " values (%s,%s,%s,'department','RETAIL_OPS','Data Platform','table',%s,%s) on conflict (code) do nothing",
                    (t["code"], t["name"], t["description"], t["code"], ["Karma", "SOP", "live"]))
    dash = REPORTS[0]
    cur.execute("insert into rpt.report (code, name, description, layer, owner_dept, owner_name, kind, url, tags)"
                " values (%s,%s,%s,'department','RETAIL_OPS','Data Platform','link',%s,%s) on conflict (code) do update set url = excluded.url",
                (dash["report_code"], dash["name"], dash["description"], "/reports/dynamic/%s" % rid(dash), ["Karma", "SOP", "dashboard"]))
    fid = _folder(cur)
    if not fid:
        log.warning("karma_sop: folder %s / %s not found; reports registered but not filed", TREE, FOLDER_NAME)
        return
    codes = [t["code"] for t in TABLES] + [dash["report_code"]]
    _retire_old(cur, fid, codes)
    for n, code in enumerate(codes):
        cur.execute("select id from rpt.report where code = %s", (code,))
        rpt = cur.fetchone()["id"]
        cur.execute("select 1 from nav.item where folder_id = %s and report_id = %s", (fid, rpt))
        if not cur.fetchone():
            cur.execute("insert into nav.item (id, folder_id, kind, report_id, sort_order) values (%s,%s,'report',%s,%s)",
                        (uuid.uuid4(), fid, rpt, 10 * (n + 1)))
