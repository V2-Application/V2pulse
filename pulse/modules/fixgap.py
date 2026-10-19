"""Fixture GAP (installed vs planned gondolas) reports in Retail > GAP Reports > LAYOUT.

Source: ST INSTALLED VS PLANNED FIXTURE GAP.xlsx, loaded to RETAIL_HUB.BRZ_FIXGAP_* -> GOLD.RPT_FIXGAP_* -> PULSE.V_BI_FIXGAP_*
(deploy/fixgap_gold_pulse.sql). One report per Excel sheet, plus a dashboard:

  fixgap-oneliner    store one-liner (summary)             V_BI_FIXGAP_ST_ONELINER
  fixgap-div         store x division                      V_BI_FIXGAP_ST_DIV
  fixgap-flr-subdiv  store x floor x sub-division          V_BI_FIXGAP_ST_FLR_SUBDIV
  fixgap-flr         store x floor                         V_BI_FIXGAP_ST_FLR
  fixgap-majcat      store x floor x major category        V_BI_FIXGAP_ST_MAJCAT
  dashboard          October budget, fixture breakup and budget gap by zone / division / store (dataset fixgap_div)

Store-level figures (ST FIX, floor fixtures) repeat on every row of a finer grain, so only the tables show them; the dashboard
sums the additive budget and breakup columns only, and recomputes shares as ratios. Store scope applies on ST_CD."""
import json
import uuid

from .bi_catalog import D, M, V

PULSE = "V2RETAIL.PULSE"
TREE_NAME = "Retail"           # the department whose tree holds the folders (iam.org_node name, case-insensitive)
FOLDER = "GAP Reports"
SUBFOLDER = "LAYOUT"
CATEGORY = "Fixture GAP"
AUDIENCE = ["EXEC", "MIS_CENTRAL", "PLATFORM", "DEPT_MIS_OWNER", "HO_STAFF", "MERCH", "ZONAL_HEAD", "REGIONAL_HEAD", "AREA_MGR",
            "STORE_MGR"]
ZONES = ["ZONE-1", "ZONE-2", "ZONE-3", "ZONE-4", "ZONE-5", "ZONE-6"]
_S = "Store"


def P(label, type_="str", choices=None, required=False, max_len=60):
    p = {"type": type_, "label": label, "required": required, "max_len": max_len}
    if choices:
        p["choices"] = choices
    return p


_MEASURES = ('OCT_BGT "oct budget", FRESH_FIX "fresh fix", BS_FIX "bs fix", round(BS_FIX_CONT * 100, 1) "bs share pct", '
             'C_ART_FIX "c-art fix", round(C_ART_FIX_CONT * 100, 1) "c-art share pct", SEPT_BGT_PURCHASE "sept purchase", '
             'SEPT_BGT_ALLOCATION "sept allocation", DIFF_PURCHASE "diff purchase", DIFF_ALLOCATION "diff allocation", '
             'LAST_REFRESHED "refreshed"')
_WHERE = ("{scope} and (%(zone)s is null or upper(ZONE) = upper(%(zone)s)) and (%(region)s is null or upper(REGION) = upper(%(region)s))"
          " and (%(store)s is null or ST_CD = upper(%(store)s)) and (%(status)s is null or upper(ST_STAT) = upper(%(status)s))")
_DIV = " and (%(division)s is null or upper(DIV) = upper(%(division)s))"
_FLR = " and (%(floor)s is null or FLR = %(floor)s)"
_SUB = " and (%(sub_division)s is null or upper(SUB_DIV) = upper(%(sub_division)s))"
_MAJ = " and (%(major_category)s is null or MAJCAT ilike '%%' || %(major_category)s || '%%')"


def _report(code, name, desc, view, extra_cols, extra_where, extra_params, order, zone_required=False):
    sql = ('select ST_CD "store", STORE_NAME "store name", ZONE "zone", REGION "region", CLUSTER "cluster", ST_STAT "status", '
           'OP_DATE "opening date", %s, %s\nfrom %s.%s\nwhere %s%s\norder by %s'
           % (extra_cols, _MEASURES, PULSE, view, _WHERE, extra_where, order))
    params = {"zone": P("Zone", choices=ZONES, required=zone_required), "region": P("Region"), "store": P("Store code", max_len=10),
              "status": P("Store status", max_len=10)}
    params.update(extra_params)
    return dict(code=code, name=name, description=desc, view=view, sql=sql, params=params, scope="ST_CD")


TABLES = [
    _report("fixgap-oneliner", "Fixture GAP — Store summary (one-liner)",
            "One line per store: installed store fixtures, the October budget and its breakup (fresh, BS and C-article fixtures), "
            "the September budget (purchase and allocation) and the gap against installed fixtures. Same as the ST ONELINER sheet.",
            "V_BI_FIXGAP_ST_ONELINER", 'ST_FIX "store fixtures"', "", {}, "ST_CD"),
    _report("fixgap-div", "Fixture GAP — Store x Division",
            "Store x division: October budget and its breakup, September budget and the gap. Same as the ST DIV sheet.",
            "V_BI_FIXGAP_ST_DIV", 'DIV "division", ST_FIX "store fixtures"', _DIV, {"division": P("Division")}, "ST_CD, DIV"),
    _report("fixgap-flr-subdiv", "Fixture GAP — Store x Floor x Sub-division",
            "Store x floor x sub-division: October budget and its breakup, September budget and the gap. Same as the ST FLR SUB-DIV sheet.",
            "V_BI_FIXGAP_ST_FLR_SUBDIV", 'FLR "floor", DIV "division", SUB_DIV "sub division", ST_FL_FIX "floor fixtures"',
            _FLR + _DIV + _SUB, {"floor": P("Floor", max_len=4), "division": P("Division"), "sub_division": P("Sub-division")},
            "ST_CD, FLR, DIV, SUB_DIV"),
    _report("fixgap-flr", "Fixture GAP — Store x Floor",
            "Store x floor with the layout flags: October budget and its breakup, September budget and the gap. Same as the ST FLR sheet. "
            "Pick a zone to open it.",
            "V_BI_FIXGAP_ST_FLR", 'FLR "floor", LAYOUT_BLANK "layout blank", LAYOUT_WORKING "layout working", ST_FL_FIX "floor fixtures"',
            _FLR, {"floor": P("Floor", max_len=4)}, "ST_CD, FLR", zone_required=True),
    _report("fixgap-majcat", "Fixture GAP — Store x Major category",
            "Store x floor x major category: October budget and its breakup, September budget and the gap. Same as the ST MAJCAT sheet. "
            "Pick a zone to open it, then narrow by store, division or major category.",
            "V_BI_FIXGAP_ST_MAJCAT", 'FLR "floor", SSN "season", DIV "division", SUB_DIV "sub division", MAJCAT "major category", '
            'ST_FL_FIX "floor fixtures"', _FLR + _DIV + _SUB + _MAJ,
            {"floor": P("Floor", max_len=4), "division": P("Division"), "sub_division": P("Sub-division"),
             "major_category": P("Major category (contains)")}, "ST_CD, FLR, DIV, SUB_DIV, MAJCAT", zone_required=True),
]

# ---------------------------------------------------------------- dashboard (store x division, additive columns only)
DS_SQL = "select * from %s.V_BI_FIXGAP_ST_DIV" % PULSE
DATASETS = [dict(
    code="fixgap_div", name="Fixture GAP - store x division",
    description="October budget fixtures and their breakup (fresh, BS and C-article), September budget (purchase and allocation) and the "
                "gap against installed fixtures, by store and division. Shares are recomputed from the sums.",
    view="V_BI_FIXGAP_ST_DIV", sql=DS_SQL, snapshot_dim=None, scope="ST_CD", cache=1800, audience=AUDIENCE,
    refresh="From ST INSTALLED VS PLANNED FIXTURE GAP.xlsx (see LAST_REFRESHED on the table reports); reload to refresh.",
    dimensions=[
        D("zone", "Zone", "ZONE", group=_S), D("region", "Region", "REGION", group=_S), D("cluster", "Cluster", "CLUSTER", group=_S),
        D("store", "Store code", "ST_CD", group=_S), D("store_name", "Store", "STORE_NAME", group=_S),
        D("status", "Store status", "ST_STAT", group=_S), D("division", "Division", "DIV", group="Merchandise"),
    ],
    measures=[
        M("oct_bgt", "October budget", "OCT_BGT"), M("fresh_fix", "Fresh fix", "FRESH_FIX"), M("bs_fix", "BS fix", "BS_FIX"),
        M("c_art_fix", "C-article fix", "C_ART_FIX"), M("sept_purchase", "September budget (purchase)", "SEPT_BGT_PURCHASE"),
        M("sept_alloc", "September budget (allocation)", "SEPT_BGT_ALLOCATION"), M("diff_purchase", "Gap vs purchase", "DIFF_PURCHASE"),
        M("diff_alloc", "Gap vs allocation", "DIFF_ALLOCATION"), M("stores", "Stores", "ST_CD", agg="count_distinct"),
        M("fresh_share", "Fresh fix % of budget", agg="ratio", num="fresh_fix", den="oct_bgt", fmt="pct"),
        M("bs_share", "BS fix % of budget", agg="ratio", num="bs_fix", den="oct_bgt", fmt="pct"),
        M("c_art_share", "C-article fix % of budget", agg="ratio", num="c_art_fix", den="oct_bgt", fmt="pct"),
        M("gap_share", "Gap vs allocation % of budget", agg="ratio", num="diff_alloc", den="oct_bgt", fmt="pct"),
    ],
)]

_LOC = [{"dim": "zone"}, {"dim": "region"}, {"dim": "cluster"}]
_ALL = ["stores", "oct_bgt", "fresh_fix", "bs_fix", "c_art_fix", "sept_purchase", "sept_alloc", "diff_purchase", "diff_alloc",
        "fresh_share", "bs_share", "c_art_share", "gap_share"]


def _kpis(*items):
    w = 12 // len(items)
    return [V("k%d" % (i + 1), "kpi", t, i * w, 0, w, 2, measures=[m]) for i, (t, m) in enumerate(items)]


REPORTS = [dict(
    name="Fixture GAP — dashboard", category=CATEGORY, dataset="fixgap_div", audience=AUDIENCE, report_code="fixgap-dashboard",
    description="Installed vs planned fixtures: October budget, its breakup (fresh, BS, C-article) and the gap against September "
                "purchase and allocation, by zone, region, cluster, division and store.",
    definition={"version": 1, "filters": [], "pages": [
        {"id": "p1", "name": "Overview", "slicers": _LOC + [{"dim": "division"}, {"dim": "status"}],
         "visuals": _kpis(("October budget", "oct_bgt"), ("Fresh fix", "fresh_fix"), ("BS fix", "bs_fix"), ("Gap vs allocation", "diff_alloc")) + [
             V("c1", "column", "October budget by zone", 0, 2, 6, 6, dims=["zone"], measures=["oct_bgt", "fresh_fix"],
               sort={"by": "oct_bgt", "dir": "desc"}, limit=12),
             V("b1", "bar", "Gap vs allocation by division", 6, 2, 6, 6, dims=["division"], measures=["diff_alloc"],
               sort={"by": "diff_alloc", "dir": "desc"}, limit=15),
             V("t1", "table", "Zones", 0, 8, 12, 6, dims=["zone"], measures=_ALL, sort={"by": "oct_bgt", "dir": "desc"}, limit=50)]},
        {"id": "p2", "name": "Divisions", "slicers": _LOC + [{"dim": "status"}],
         "visuals": [
             V("t1", "table", "By division", 0, 0, 12, 8, dims=["division"], measures=_ALL, sort={"by": "oct_bgt", "dir": "desc"}, limit=50),
             V("m1", "matrix", "Gap vs allocation: division by zone", 0, 8, 12, 8, dims=["division", "zone"], measures=["diff_alloc"])]},
        {"id": "p3", "name": "Stores", "slicers": _LOC + [{"dim": "division"}, {"dim": "status"}],
         "visuals": [
             V("b1", "bar", "Top 15 stores by gap vs allocation", 0, 0, 5, 9, dims=["store_name"], measures=["diff_alloc"],
               sort={"by": "diff_alloc", "dir": "desc"}, limit=15),
             V("t1", "table", "All stores", 5, 0, 7, 9, dims=["store", "store_name", "region", "cluster"], measures=_ALL[1:],
               sort={"by": "oct_bgt", "dir": "desc"}, limit=1000)]},
    ]},
)]


# ---------------------------------------------------------------- seed
def _tree(cur):
    cur.execute("select code from iam.org_node where node_type = 'dept' and is_active and lower(name) = lower(%s)", (TREE_NAME,))
    r = cur.fetchone()
    return ("dept:" + r["code"]) if r else None


def _folder(cur, tree, name, parent, desc):
    cur.execute("select id, path from nav.folder where tree = %s and section = 'reports' and parent_id is not distinct from %s"
                " and lower(name) = lower(%s) and not is_archived", (tree, parent["id"] if parent else None, name))
    f = cur.fetchone()
    if f:
        return f
    fid = uuid.uuid4()
    path = (parent["path"] if parent else "/") + str(fid) + "/"
    cur.execute("select coalesce(max(sort_order), 0) + 10 n from nav.folder where tree = %s and section = 'reports'"
                " and parent_id is not distinct from %s", (tree, parent["id"] if parent else None))
    cur.execute("insert into nav.folder (id, tree, parent_id, name, description, path, sort_order) values (%s,%s,%s,%s,%s,%s,%s)"
                " returning id, path", (fid, tree, parent["id"] if parent else None, name, desc, path, cur.fetchone()["n"]))
    return cur.fetchone()


def seed(cur, rid):
    """Table reports, the dashboard link, and the Retail > GAP Reports > LAYOUT folders. Run after bi.dataset / bi.report seed."""
    tree = _tree(cur)
    if not tree:
        return
    owner = tree.split(":", 1)[1]
    for t in TABLES:
        cur.execute("insert into data.query_def (code, name, sql_template, params, scope_column, permission, cache_seconds, max_rows)"
                    " values (%s,%s,%s,%s,%s,'reports.view',1800,100000) on conflict (code) do update set name = excluded.name,"
                    " sql_template = excluded.sql_template, params = excluded.params, scope_column = excluded.scope_column",
                    (t["code"], t["name"], t["sql"], json.dumps(t["params"]), t["scope"]))
        cur.execute("insert into rpt.report (code, name, description, layer, owner_dept, owner_name, kind, query_code, tags)"
                    " values (%s,%s,%s,'department',%s,'Data Platform','table',%s,%s) on conflict (code) do nothing",
                    (t["code"], t["name"], t["description"], owner, t["code"], ["Fixture GAP", "Layout", "live"]))
    dash = REPORTS[0]
    cur.execute("insert into rpt.report (code, name, description, layer, owner_dept, owner_name, kind, url, tags)"
                " values (%s,%s,%s,'department',%s,'Data Platform','link',%s,%s) on conflict (code) do update set url = excluded.url",
                (dash["report_code"], dash["name"], dash["description"], owner, "/reports/dynamic/%s" % rid(dash),
                 ["Fixture GAP", "Layout", "dashboard"]))
    top = _folder(cur, tree, FOLDER, None, "Gap reports for the Retail team.")
    sub = _folder(cur, tree, SUBFOLDER, top, "Installed vs planned fixtures (layout): store summary, details and a dashboard.")
    for n, code in enumerate([t["code"] for t in TABLES] + [dash["report_code"]]):
        cur.execute("select id from rpt.report where code = %s", (code,))
        rpt = cur.fetchone()["id"]
        cur.execute("select 1 from nav.item where folder_id = %s and report_id = %s", (sub["id"], rpt))
        if not cur.fetchone():
            cur.execute("insert into nav.item (id, folder_id, kind, report_id, sort_order) values (%s,%s,'report',%s,%s)",
                        (uuid.uuid4(), sub["id"], rpt, 10 * (n + 1)))
