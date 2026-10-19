"""Karma SM Yes/No checklist reports in Retail Operations > SM Yes No: the same three reports as the ST SOP checklist.

  karma-sm-detail   one row per SM Y/N checklist submission (GOLD.FACT_KARMA_CHECKLIST_SUBMISSION, REPORT_KEY = SM_YN)
  karma-sm-summary  store-wise TD / MTD budget, actual and compliance (GOLD.RPT_KARMA_SM_YES_NO_STOREWISE)
  dashboard         completion, YES / NO / NA and trend (PULSE.V_BI_KARMA_CHECKLIST_SHEET, filtered to SM YES/NO)

Built from karma_sop.py: same columns, filters, scope and dashboard pages; only the source, names and report filter change.
GOLD has no per-submission SM view, so the detail PULSE view selects the same columns as GOLD.RPT_KARMA_ST_SOP_CHECKLIST
straight from the fact. Nothing in GOLD is changed."""
import copy
import json
import uuid

from . import karma_sop as _s

PULSE = _s.PULSE
TREE = _s.TREE
FOLDER_NAME = "SM Yes No"
FOLDER_DESC = "SM Yes/No checklist reports: submissions, store-wise TD/MTD and a dashboard."
DETAIL_VIEW, SUMMARY_VIEW = "V_WM_KARMA_SM_DETAIL", "V_WM_KARMA_SM_SUMMARY"

_FACT = "V2RETAIL.GOLD.FACT_KARMA_CHECKLIST_SUBMISSION"
_DETAIL_COLS = ("SHEET_NAME, SHEET_ORDER, REPORT_DATE AS DATE, REF_NO, STORE_CODE, COVERAGE, ZONE, REG, CLUSTER, CLUSTER_MANAGER, "
                "QUESTIONS_ATTEMPTED, TOTAL_QUESTIONS, COMPLETION_PCT, YES_COUNT, NO_COUNT, NA_COUNT, SUBMITTED_BY, "
                "SUBMITTED_DATETIME_IST, STATUS, APPROVAL_STATUS, EVIDENCE_COUNT, PROOF_URL, SUBMISSION_ID")


def _tables():
    detail, summary = copy.deepcopy(_s.TABLES)
    detail.update(
        code="karma-sm-detail", name="Karma SM Yes/No — submissions (detail)", view=DETAIL_VIEW,
        description="Every SM Yes/No checklist submission: sheet, store, zone / region / cluster, questions attempted, completion %, "
                    "YES / NO / NA counts, who submitted and when, status, approval and the proof link. Defaults to the last "
                    "7 days; change the dates, zone, store, checklist or status above.",
        view_sql="select b.*, r.LAST_REFRESHED from (select %s from %s where REPORT_KEY = 'SM_YN') b "
                 "cross join (select to_varchar(max(SUBMITTED_DATETIME_IST), 'DD-MM-YYYY HH24:MI') from %s where REPORT_KEY = 'SM_YN') "
                 "r (LAST_REFRESHED)" % (_DETAIL_COLS, _FACT, _FACT),
        sql=detail["sql"].replace(_s.DETAIL_VIEW, DETAIL_VIEW))
    summary.update(
        code="karma-sm-summary", name="Karma SM Yes/No — store TD/MTD (summary)", view=SUMMARY_VIEW,
        description="Store-wise SM Yes/No checklist budget against actual for today (TD) and month to date (MTD), with compliance "
                    "pct and the zone, region, cluster and cluster manager. Defaults to the latest day.",
        view_sql="select b.*, r.LAST_REFRESHED from (select * from V2RETAIL.GOLD.RPT_KARMA_SM_YES_NO_STOREWISE) b "
                 "cross join (select to_varchar(max(AS_OF_DATE), 'DD-MM-YYYY HH24:MI') from V2RETAIL.GOLD.RPT_KARMA_SM_YES_NO_STOREWISE) "
                 "r (LAST_REFRESHED)",
        sql=summary["sql"].replace(_s.SUMMARY_VIEW, SUMMARY_VIEW))
    return [detail, summary]


TABLES = _tables()

SM_FILTER = [{"dim": "report", "op": "in", "values": ["SM YES/NO"]}]

DATASETS = [dict(copy.deepcopy(_s.DATASETS[0]), code="karma_sm", name="Karma - SM Yes/No checklist",
                 description="SM Yes/No checklist submissions by store, day and checklist sheet: submissions, questions attempted, "
                             "YES / NO / NA answers and completion %. Figures add up over the dates you pick.")]

_dash = copy.deepcopy(_s.REPORTS[0])
_dash.update(name="Karma SM Yes/No — dashboard", dataset="karma_sm", report_code="karma-sm-dashboard",
             description="SM Yes/No checklist completion and answers by zone, region, cluster, checklist, store and day: how much "
                         "was filled, how many YES / NO / NA, and where completion is lowest.")
_dash["definition"]["filters"] = SM_FILTER
REPORTS = [_dash]


def snowflake_sql():
    out = ["-- V2Pulse Karma SM Yes/No: PULSE wrappers over the GOLD data. Generated from pulse/modules/karma_sm.py.",
           "-- Run as ACCOUNTADMIN on COMPUTE_WH. Safe to re-run. GOLD is not changed.", "use role ACCOUNTADMIN;",
           "use warehouse COMPUTE_WH;", ""]
    for t in TABLES:
        out += [_s.view_sql(t), "grant select on view %s.%s to role PULSE_SVC_ROLE;" % (PULSE, t["view"]), ""]
    return "\n".join(out)


def _folder(cur):
    """The 'SM Yes No' folder at the top of the Retail Operations tree; created once if it is not there."""
    cur.execute("select id from nav.folder where tree = %s and section = 'reports' and parent_id is null"
                " and lower(name) = lower(%s) and not is_archived", (TREE, FOLDER_NAME))
    f = cur.fetchone()
    if f:
        return f["id"]
    cur.execute("select 1 from iam.org_node where node_type = 'dept' and code = %s", (TREE.split(":", 1)[1],))
    if not cur.fetchone():
        return None
    fid = uuid.uuid4()
    cur.execute("select coalesce(max(sort_order), 0) + 10 n from nav.folder where tree = %s and section = 'reports'"
                " and parent_id is null", (TREE,))
    cur.execute("insert into nav.folder (id, tree, name, description, path, sort_order) values (%s,%s,%s,%s,%s,%s)",
                (fid, TREE, FOLDER_NAME, FOLDER_DESC, "/%s/" % fid, cur.fetchone()["n"]))
    return fid


def seed(cur, rid):
    """Table reports, dashboard link and filing in Retail Operations > SM Yes No. Run after bi.dataset / bi.report seed."""
    for t in TABLES:
        cur.execute("insert into data.query_def (code, name, sql_template, params, scope_column, permission, cache_seconds, max_rows)"
                    " values (%s,%s,%s,%s,%s,'reports.view',1800,100000) on conflict (code) do update set name = excluded.name,"
                    " sql_template = excluded.sql_template, params = excluded.params, scope_column = excluded.scope_column",
                    (t["code"], t["name"], t["sql"], json.dumps(t["params"]), t["scope"]))
        cur.execute("insert into rpt.report (code, name, description, layer, owner_dept, owner_name, kind, query_code, tags)"
                    " values (%s,%s,%s,'department','RETAIL_OPS','Data Platform','table',%s,%s) on conflict (code) do nothing",
                    (t["code"], t["name"], t["description"], t["code"], ["Karma", "SM", "live"]))
    dash = REPORTS[0]
    cur.execute("insert into rpt.report (code, name, description, layer, owner_dept, owner_name, kind, url, tags)"
                " values (%s,%s,%s,'department','RETAIL_OPS','Data Platform','link',%s,%s) on conflict (code) do update set url = excluded.url",
                (dash["report_code"], dash["name"], dash["description"], "/reports/dynamic/%s" % rid(dash), ["Karma", "SM", "dashboard"]))
    fid = _folder(cur)
    if not fid:
        return
    for n, code in enumerate([t["code"] for t in TABLES] + [dash["report_code"]]):
        cur.execute("select id from rpt.report where code = %s", (code,))
        rpt = cur.fetchone()["id"]
        cur.execute("select 1 from nav.item where folder_id = %s and report_id = %s", (fid, rpt))
        if not cur.fetchone():
            cur.execute("insert into nav.item (id, folder_id, kind, report_id, sort_order) values (%s,%s,'report',%s,%s)",
                        (uuid.uuid4(), fid, rpt, 10 * (n + 1)))
