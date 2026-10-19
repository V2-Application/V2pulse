"""Karma Yes/No checklist reports (SM, ASM) in Retail Operations: the same three reports as the ST SOP checklist, each in its
own folder ("SM Yes No", "ASM Yes No").

  karma-<k>-detail   one row per submission (GOLD.FACT_KARMA_CHECKLIST_SUBMISSION, REPORT_KEY = <KEY>)
  karma-<k>-summary  store-wise TD / MTD budget, actual and compliance (GOLD.RPT_KARMA_<K>_YES_NO_STOREWISE)
  dashboard          completion, YES / NO / NA and trend (PULSE.V_BI_KARMA_CHECKLIST_SHEET, filtered to the report)

Built from karma_sop.py: same columns, filters, scope and dashboard pages; only the source, names and report filter change.
GOLD has no per-submission view for these, so each detail PULSE view selects the same columns as GOLD.RPT_KARMA_ST_SOP_CHECKLIST
straight from the fact. Nothing in GOLD is changed.

Add another Yes/No report by adding a row to CONFIGS once its REPORT_KEY exists in GOLD.FACT_KARMA_CHECKLIST_SUBMISSION (the keys
come from SILVER.DIM_KARMA_REPORT_PROCESS)."""
import copy
import json
import uuid

from . import karma_sop as _s

PULSE = _s.PULSE
TREE = _s.TREE
_FACT = "V2RETAIL.GOLD.FACT_KARMA_CHECKLIST_SUBMISSION"
_DETAIL_COLS = ("SHEET_NAME, SHEET_ORDER, REPORT_DATE AS DATE, REF_NO, STORE_CODE, COVERAGE, ZONE, REG, CLUSTER, CLUSTER_MANAGER, "
                "QUESTIONS_ATTEMPTED, TOTAL_QUESTIONS, COMPLETION_PCT, YES_COUNT, NO_COUNT, NA_COUNT, SUBMITTED_BY, "
                "SUBMITTED_DATETIME_IST, STATUS, APPROVAL_STATUS, EVIDENCE_COUNT, PROOF_URL, SUBMISSION_ID")

# key: GOLD REPORT_KEY; label: report name; pulse_label: REPORT value in the dataset view; summary_view: GOLD store-wise view.
# managed=False: the PULSE views are not generated here (FM / LOBM sit on PULSE.KARMA_FMLOBM_SUB, see deploy/karma_fmlobm_views.sql);
# role: the headcount budget column shown in their summary (FM_BGT / LOBM_BGT); person: the detail also shows who filled it.
CONFIGS = [
    dict(key="SM_YN", short="sm", label="SM Yes/No", pulse_label="SM YES/NO", folder="SM Yes No",
         summary_view="V2RETAIL.GOLD.RPT_KARMA_SM_YES_NO_STOREWISE"),
    dict(key="ASM_YN", short="asm", label="ASM Yes/No", pulse_label="ASM YES/NO", folder="ASM Yes No",
         summary_view="V2RETAIL.GOLD.RPT_KARMA_ASM_YES_NO_STOREWISE"),
    dict(key="FM_YN", short="fm", label="FM Yes/No", pulse_label="FM YES/NO", folder="FM Yes No", managed=False, role="FM",
         person=True, dataset_view="V_BI_KARMA_FMLOBM_SHEET"),
    dict(key="LOBM_YN", short="lobm", label="LOBM Yes/No", pulse_label="LOBM YES/NO", folder="LOBM Yes No", managed=False,
         role="LOBM", person=True, dataset_view="V_BI_KARMA_FMLOBM_SHEET"),
]

# Definition of PULSE.V_BI_KARMA_FMLOBM_SHEET (kept here so a regenerated bi-sql re-creates it unchanged).
FMLOBM_SHEET_SQL = """SELECT f.REPORT_DATE AS SNAPSHOT_DATE,
  CASE f.REPORT_KEY WHEN 'FM_YN' THEN 'FM YES/NO' WHEN 'LOBM_YN' THEN 'LOBM YES/NO' ELSE f.REPORT_KEY END AS REPORT,
  f.SHEET_NAME AS CHECKLIST, f.SHEET_ORDER AS CHECKLIST_ORDER,
  f.STORE_CODE, COALESCE(h.STORE_NAME, d.STORE_NAME) AS STORE_NAME, COALESCE(h.STATE, d.STORE_STATE) AS STATE,
  COALESCE(h.ZONE_CODE, d.ZONE_CODE) AS ZONE_CODE, COALESCE(h.REGION_CODE, d.REGION_CODE) AS REGION_CODE, COALESCE(h.CLUSTER_CODE, d.CLUSTER_CODE) AS CLUSTER_CODE,
  h.CLUSTER_HEAD_NAME, d.CLUSTER_MANAGER,
  f.SUBMISSIONS, f.ANSWERS_TOTAL AS QUESTIONS_TOTAL, f.ANSWERS_ATTEMPTED AS QUESTIONS_ATTEMPTED,
  f.YES_COUNT, f.NO_COUNT, f.NA_COUNT, f.COMPLETION_PCT
FROM (SELECT REPORT_KEY, SHEET_NAME, SHEET_ORDER, STORE_CODE, REPORT_DATE, COUNT(DISTINCT SUBMISSION_ID) AS SUBMISSIONS,
        SUM(TOTAL_QUESTIONS) AS ANSWERS_TOTAL, SUM(QUESTIONS_ATTEMPTED) AS ANSWERS_ATTEMPTED, SUM(YES_COUNT) AS YES_COUNT,
        SUM(NO_COUNT) AS NO_COUNT, SUM(NA_COUNT) AS NA_COUNT,
        IFF(SUM(TOTAL_QUESTIONS) = 0, 0, ROUND(SUM(QUESTIONS_ATTEMPTED) / SUM(TOTAL_QUESTIONS) * 100, 1)) AS COMPLETION_PCT
      FROM V2RETAIL.PULSE.KARMA_FMLOBM_SUB WHERE STORE_CODE IS NOT NULL
      GROUP BY REPORT_KEY, SHEET_NAME, SHEET_ORDER, STORE_CODE, REPORT_DATE) f
LEFT JOIN V2RETAIL.PULSE.V_STORE_HIERARCHY h ON h.STORE_CODE = f.STORE_CODE
LEFT JOIN V2RETAIL.SILVER.DIM_KARMA_STORE d ON d.STORE_CODE = f.STORE_CODE"""

TABLES, DATASETS, REPORTS, FOLDERS = [], [], [], {}


def _build(c):
    s = c["short"].upper()
    managed = c.get("managed", True)
    dview, sview = "V_WM_KARMA_%s_DETAIL" % s, "V_WM_KARMA_%s_SUMMARY" % s
    detail, summary = copy.deepcopy(_s.TABLES)
    detail.update(
        code="karma-%s-detail" % c["short"], name="Karma %s — submissions (detail)" % c["label"], view=dview,
        description="Every %s checklist submission: sheet, store, zone / region / cluster, questions attempted, completion %%, "
                    "YES / NO / NA counts, who submitted and when, status, approval and the proof link. Defaults to the last "
                    "7 days; change the dates, zone, store, checklist or status above." % c["label"],
        view_sql="select b.*, r.LAST_REFRESHED from (select %s from %s where REPORT_KEY = '%s') b "
                 "cross join (select to_varchar(max(SUBMITTED_DATETIME_IST), 'DD-MM-YYYY HH24:MI') from %s where REPORT_KEY = '%s') "
                 "r (LAST_REFRESHED)" % (_DETAIL_COLS, _FACT, c["key"], _FACT, c["key"]),
        sql=detail["sql"].replace(_s.DETAIL_VIEW, dview).replace(
            'SUBMITTED_BY "submitted by",', 'SUBMITTED_BY "submitted by", PERSON "person",' if c.get("person") else 'SUBMITTED_BY "submitted by",'))
    summary.update(
        code="karma-%s-summary" % c["short"], name="Karma %s — store TD/MTD (summary)" % c["label"], view=sview,
        description="Store-wise %s checklist budget against actual for today (TD) and month to date (MTD), with compliance "
                    "pct and the zone, region, cluster and cluster manager. Defaults to the latest day." % c["label"],
        view_sql="select b.*, r.LAST_REFRESHED from (select * from %s) b cross join (select to_varchar(max(AS_OF_DATE), "
                 "'DD-MM-YYYY HH24:MI') from %s) r (LAST_REFRESHED)" % (c.get("summary_view"), c.get("summary_view")),
        sql=summary["sql"].replace(_s.SUMMARY_VIEW, sview).replace(
            'MTD_DAYS "mtd days"', '%s_BGT "%s headcount", MTD_DAYS "mtd days"' % (c["role"], c["role"].lower()) if c.get("role") else 'MTD_DAYS "mtd days"'))
    if c.get("role"):
        summary["description"] = ("Store-wise %s checklist budget against actual for today (TD) and month to date (MTD). Budget = "
                                  "the store's %s headcount x %d sheets x days; actual = fills (one per person per sheet per day). "
                                  "Compliance pct can pass 100. Defaults to the latest day." % (c["label"], c["role"], 9 if c["role"] == "FM" else 4))
    if not managed:
        detail["view_sql"] = summary["view_sql"] = None
    ds = dict(copy.deepcopy(_s.DATASETS[0]), code="karma_%s" % c["short"], name="Karma - %s checklist" % c["label"],
              description="%s checklist submissions by store, day and checklist sheet: submissions, questions attempted, YES / NO / "
                          "NA answers and completion %%. Figures add up over the dates you pick." % c["label"])
    if c.get("dataset_view"):
        ds.update(view=c["dataset_view"], sql=FMLOBM_SHEET_SQL,
                  refresh="Rebuilt from the Karma checklist answers (PULSE.KARMA_FMLOBM_SUB); see deploy/karma_fmlobm_views.sql.")
    dash = copy.deepcopy(_s.REPORTS[0])
    dash.update(name="Karma %s — dashboard" % c["label"], dataset=ds["code"], report_code="karma-%s-dashboard" % c["short"],
                description="%s checklist completion and answers by zone, region, cluster, checklist, store and day: how much "
                            "was filled, how many YES / NO / NA, and where completion is lowest." % c["label"])
    dash["definition"]["filters"] = [{"dim": "report", "op": "in", "values": [c["pulse_label"]]}]
    TABLES.extend([detail, summary])
    DATASETS.append(ds)
    REPORTS.append(dash)
    FOLDERS[detail["code"]] = FOLDERS[summary["code"]] = FOLDERS[dash["report_code"]] = c


for _c in CONFIGS:
    _build(_c)


def snowflake_sql():
    out = ["-- V2Pulse Karma Yes/No reports: PULSE wrappers over the GOLD data. Generated from pulse/modules/karma_yn.py.",
           "-- Run as ACCOUNTADMIN on COMPUTE_WH. Safe to re-run. GOLD is not changed.", "use role ACCOUNTADMIN;",
           "use warehouse COMPUTE_WH;", ""]
    for t in TABLES:
        if t["view_sql"]:   # FM / LOBM views are in deploy/karma_fmlobm_views.sql
            out += [_s.view_sql(t), "grant select on view %s.%s to role PULSE_SVC_ROLE;" % (PULSE, t["view"]), ""]
    return "\n".join(out)


def _folder(cur, name, desc):
    """A top-level folder in the Retail Operations tree; created once if it is not there."""
    cur.execute("select id from nav.folder where tree = %s and section = 'reports' and parent_id is null"
                " and lower(name) = lower(%s) and not is_archived", (TREE, name))
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
                (fid, TREE, name, desc, "/%s/" % fid, cur.fetchone()["n"]))
    return fid


def seed(cur, rid):
    """Table reports, dashboard links and filing in each report's folder. Run after bi.dataset / bi.report seed."""
    for t in TABLES:
        c = FOLDERS[t["code"]]
        cur.execute("insert into data.query_def (code, name, sql_template, params, scope_column, permission, cache_seconds, max_rows)"
                    " values (%s,%s,%s,%s,%s,'reports.view',1800,100000) on conflict (code) do update set name = excluded.name,"
                    " sql_template = excluded.sql_template, params = excluded.params, scope_column = excluded.scope_column",
                    (t["code"], t["name"], t["sql"], json.dumps(t["params"]), t["scope"]))
        cur.execute("insert into rpt.report (code, name, description, layer, owner_dept, owner_name, kind, query_code, tags)"
                    " values (%s,%s,%s,'department','RETAIL_OPS','Data Platform','table',%s,%s) on conflict (code) do nothing",
                    (t["code"], t["name"], t["description"], t["code"], ["Karma", c["short"].upper(), "live"]))
    for dash in REPORTS:
        c = FOLDERS[dash["report_code"]]
        cur.execute("insert into rpt.report (code, name, description, layer, owner_dept, owner_name, kind, url, tags)"
                    " values (%s,%s,%s,'department','RETAIL_OPS','Data Platform','link',%s,%s)"
                    " on conflict (code) do update set url = excluded.url",
                    (dash["report_code"], dash["name"], dash["description"], "/reports/dynamic/%s" % rid(dash),
                     ["Karma", c["short"].upper(), "dashboard"]))
    for c in CONFIGS:
        fid = _folder(cur, c["folder"], "%s checklist reports: submissions, store-wise TD/MTD and a dashboard." % c["label"])
        if not fid:
            continue
        codes = ["karma-%s-detail" % c["short"], "karma-%s-summary" % c["short"], "karma-%s-dashboard" % c["short"]]
        for n, code in enumerate(codes):
            cur.execute("select id from rpt.report where code = %s", (code,))
            rpt = cur.fetchone()["id"]
            cur.execute("select 1 from nav.item where folder_id = %s and report_id = %s", (fid, rpt))
            if not cur.fetchone():
                cur.execute("insert into nav.item (id, folder_id, kind, report_id, sort_order) values (%s,%s,'report',%s,%s)",
                            (uuid.uuid4(), fid, rpt, 10 * (n + 1)))
