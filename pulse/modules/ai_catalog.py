"""AI suggestions (Analytics module): every morning, rules written by Claude scan the hero position, sales and DC stock and
write one row per alert - where it is, what is wrong, what to do, how much and what it is worth - and Claude (Snowflake
Cortex, claude-sonnet-4-5) writes a brief per scope from those exact figures. Built by SILVER.SP_AI_SUGGESTIONS after the
hero build (task SILVER.TASK_AI_SUGGESTIONS). Snowflake SQL: deploy/20_ai_suggestions.sql.

One dashboard has one dataset and its visuals cannot carry their own filters, so the view gives every suggestion type
its own masked columns (BUY_*, SEND_*, ...): they are filled only on that type's rows, and each page's table groups by
its own columns. Claude's briefs ride along as rows of type '0 · Claude brief' with N_F = 0, so they never count.
Rows are scoped by store: a store manager sees their store's alerts; company and DC alerts need company or DC scope."""
from .bi_catalog import D, M, V

CATEGORY = "Analytics"
AUDIENCE = ["EXEC", "MIS_CENTRAL", "PLATFORM", "DEPT_MIS_OWNER", "HO_STAFF", "MERCH", "SOURCING", "FINANCE",
            "ZONAL_HEAD", "REGIONAL_HEAD", "AREA_MGR", "STORE_MGR", "WH_OPS"]

# key, category label, the masked text fields that type uses (WHERE = store / DC, ITEM = article, MC = category)
TYPES = [("BUY", "1 · What to buy", ["ITEM", "MC", "VENDOR"]),
         ("SEND", "2 · What to send to stores", ["WHERE", "ITEM"]),
         ("MOVE", "3 · Move between stores", ["WHERE", "ITEM"]),
         ("STORE", "4 · What is wrong in stores", ["WHERE"]),
         ("RANGE", "5 · Range fix in stores (category)", ["WHERE", "MC"]),
         ("DC", "6 · DC placement", ["WHERE", "MC"]),
         ("FILL", "7 · Store fill rate", ["WHERE", "MC"])]
_FIELD_SQL = {"WHERE": "coalesce(s.ST_NM || ' (' || s.ST_CD || ')', s.ST_CD, s.LOCATION_TYPE)",
              "ITEM": "s.GEN_ART_DESC || ' · ' || s.GEN_ART_NUMBER", "MC": "s.MAJ_CAT", "VENDOR": "s.VENDOR_NAME"}
_COMMON = ["PRIORITY", "ALERT", "ACTION", "EVIDENCE"]
BRIEF = "0 · Claude brief"


def _view_sql():
    cols = ["s.AS_OF_DT", "s.SUGG_ID", "s.CATEGORY", "s.RULE_CODE", "s.PRIORITY", "s.OWNER", "s.LOCATION_TYPE", "s.ST_CD",
            "s.ST_NM", "coalesce(s.ZONE_CODE, 'Company / DC') as ZONE_CODE", "s.REGION_CODE", "s.CLUSTER_CODE", "s.RDC_CD",
            "s.SEG", "s.DIV", "s.SUB_DIV", "s.MAJ_CAT", "s.GEN_ART_NUMBER", "s.GEN_ART_DESC", "s.VENDOR_NAME", "s.HERO_TIER",
            "s.ALERT", "s.ACTION", "s.EVIDENCE", "s.QTY", "s.VALUE_V", "s.SCORE",
            "1 as N_F", "iff(s.PRIORITY like 'P1%', 1, 0) as P1_F", "iff(s.PRIORITY like 'P2%', 1, 0) as P2_F",
            "null as BRIEF_SCOPE", "null as BRIEF_TEXT", "null as BRIEF_F"]
    for key, label, fields in TYPES:
        cond = "s.CATEGORY = '%s'" % label
        for f in _COMMON:
            cols.append("iff(%s, s.%s, null) as %s_%s" % (cond, f, key, f))
        for f in fields:
            cols.append("iff(%s, %s, null) as %s_%s" % (cond, _FIELD_SQL[f], key, f))
        cols += ["iff(%s, s.QTY, null) as %s_QTY" % (cond, key), "iff(%s, s.VALUE_V, null) as %s_VALUE" % (cond, key),
                 "iff(%s, s.SCORE, null) as %s_SCORE" % (cond, key), "iff(%s, 1, 0) as %s_N" % (cond, key),
                 "iff(%s and s.PRIORITY like 'P1%%', 1, 0) as %s_P1" % (cond, key)]
    names = [c.split(" as ")[-1].split(".")[-1] for c in cols]
    brief = []
    for c, n in zip(cols, names):
        brief.append({"AS_OF_DT": "b.AS_OF_DT", "SUGG_ID": "'BRIEF-' || b.SCOPE_TYPE || '-' || b.SCOPE_CODE",
                      "CATEGORY": "'%s'" % BRIEF, "OWNER": "iff(b.SCOPE_TYPE = 'Owner', b.SCOPE_CODE, null)",
                      "LOCATION_TYPE": "b.SCOPE_TYPE", "ZONE_CODE": "iff(b.SCOPE_TYPE = 'Zone', b.SCOPE_CODE, 'Company / DC')",
                      "N_F": "0", "P1_F": "0", "P2_F": "0",
                      "BRIEF_SCOPE": "decode(b.SCOPE_TYPE, 'Company', '1', 'Zone', '2', '3') || ' · ' || b.TITLE",
                      "BRIEF_TEXT": "b.BRIEF", "BRIEF_F": "1"}.get(n, "0" if n.endswith(("_N", "_P1")) else "null") + " as " + n)
    return ("select %s\nfrom V2RETAIL.SILVER.AI_SUGGESTION s\nunion all\nselect %s\nfrom V2RETAIL.SILVER.AI_BRIEF b"
            % (",\n       ".join(cols), ",\n       ".join(brief)))


_L, _P, _W, _X = "Where", "Product", "What", "Per type"
_dims = [D("as_of", "As of", "AS_OF_DT", "date", "Time"),
         D("category", "Suggestion type", "CATEGORY", group=_W), D("priority", "Priority", "PRIORITY", group=_W),
         D("owner", "Owner team", "OWNER", group=_W), D("rule", "Rule", "RULE_CODE", group=_W),
         D("location_type", "Level (company / store / DC)", "LOCATION_TYPE", group=_L),
         D("zone", "Zone", "ZONE_CODE", group=_L), D("region", "Region", "REGION_CODE", group=_L),
         D("cluster", "Cluster", "CLUSTER_CODE", group=_L), D("store", "Store / DC code", "ST_CD", group=_L),
         D("store_name", "Store / DC", "ST_NM", group=_L), D("rdc", "RDC", "RDC_CD", group=_L),
         D("segment", "Segment", "SEG", group=_P), D("division", "Division", "DIV", group=_P),
         D("sub_division", "Sub-division", "SUB_DIV", group=_P), D("major_category", "Major category", "MAJ_CAT", group=_P),
         D("gen_article", "Generic article", "GEN_ART_NUMBER", "number", _P),
         D("gen_article_desc", "Article", "GEN_ART_DESC", group=_P), D("vendor", "Vendor", "VENDOR_NAME", group=_P),
         D("hero_tier", "Hero tier", "HERO_TIER", group=_P),
         D("alert", "Alert", "ALERT", group=_W), D("action", "Suggested action", "ACTION", group=_W),
         D("evidence", "Evidence", "EVIDENCE", group=_W),
         D("brief_scope", "Brief for", "BRIEF_SCOPE", group="Claude brief"),
         D("brief_text", "Claude's brief", "BRIEF_TEXT", group="Claude brief")]
_FIELD_LABEL = {"PRIORITY": "Priority", "ALERT": "Alert", "ACTION": "Suggested action", "EVIDENCE": "Evidence",
                "WHERE": "Store / DC", "ITEM": "Article", "MC": "Major category", "VENDOR": "Vendor"}
_measures = [M("suggestions", "Suggestions", "N_F"), M("p1", "P1 · act today", "P1_F"), M("p2", "P2 · this week", "P2_F"),
             M("qty", "Suggested units", "QTY"), M("value", "Value (Rs)", "VALUE_V", fmt="inr"),
             M("score", "Priority score", "SCORE"), M("briefs", "Briefs", "BRIEF_F")]
for _k, _lbl, _f in TYPES:
    _short = _lbl[4:]
    for _fld in _COMMON + _f:
        _dims.append(D("%s_%s" % (_k.lower(), _fld.lower()), "%s: %s" % (_short, _FIELD_LABEL[_fld]), "%s_%s" % (_k, _fld),
                       group=_X))
    _measures += [M("%s_n" % _k.lower(), "%s: suggestions" % _short, "%s_N" % _k),
                  M("%s_p1" % _k.lower(), "%s: P1" % _short, "%s_P1" % _k),
                  M("%s_qty" % _k.lower(), "%s: units" % _short, "%s_QTY" % _k),
                  M("%s_value" % _k.lower(), "%s: value (Rs)" % _short, "%s_VALUE" % _k, fmt="inr"),
                  M("%s_score" % _k.lower(), "%s: priority score" % _short, "%s_SCORE" % _k)]

DATASET = dict(
    code="ai_suggestions", name="AI suggestions - alerts and actions",
    description="One row per alert found each morning: what to buy, what to send to which store, what to move between "
                "stores, what is wrong in a store, which categories are over- or under-placed in a store, and which DC "
                "holds the wrong stock - with the suggested action, quantity, value, priority and owner team, plus "
                "Claude's written brief for the company, each zone and each owner team.",
    view="V_BI_AI_SUGGESTIONS", sql=_view_sql(), snapshot_dim="as_of", scope="ST_CD", cache=3600, audience=AUDIENCE,
    refresh="Daily after the hero build (09:30, repeat 13:30 IST): SILVER.SP_AI_SUGGESTIONS. Briefs by Claude "
            "(claude-sonnet-4-5 on Snowflake Cortex) from the day's alerts only.",
    dimensions=_dims, measures=_measures,
)
DATASETS = [DATASET]

def _type_table(key, title, extra_dims, limit):
    k = key.lower()
    return V("t1", "table", title, 0, 2, 12, 15,
             dims=["%s_priority" % k] + extra_dims + ["%s_alert" % k, "%s_action" % k, "%s_evidence" % k],
             measures=["%s_qty" % k, "%s_value" % k], sort={"by": "%s_score" % k, "dir": "desc"}, limit=limit)


def _type_kpis(key, label):
    k = key.lower()
    return [V("k1", "kpi", "%s - suggestions" % label, 0, 0, 3, 2, measures=["%s_n" % k]),
            V("k2", "kpi", "P1 · act today", 3, 0, 3, 2, measures=["%s_p1" % k]),
            V("k3", "kpi", "Units", 6, 0, 3, 2, measures=["%s_qty" % k]),
            V("k4", "kpi", "Value", 9, 0, 3, 2, measures=["%s_value" % k])]


_LOC = [{"dim": "zone"}, {"dim": "region"}, {"dim": "store"}]
REPORT = dict(
    name="AI suggestions", category=CATEGORY, dataset="ai_suggestions", audience=AUDIENCE,
    description="Where the alerts are and what to do today: Claude's brief, what to buy, what to send to stores, what to "
                "move between stores, what is wrong in each store, categories wrongly placed in stores, and DC placement. "
                "Every row has a quantity, a value, a priority and an owner team.",
    definition={"version": 1, "filters": [], "pages": [
        {"id": "p1", "name": "Claude's brief", "slicers": [{"dim": "zone"}, {"dim": "owner"}],
         "visuals": [
             V("k1", "kpi", "Suggestions today", 0, 0, 3, 2, measures=["suggestions"]),
             V("k2", "kpi", "P1 · act today", 3, 0, 3, 2, measures=["p1"]),
             V("k3", "kpi", "P2 · this week", 6, 0, 3, 2, measures=["p2"]),
             V("k4", "kpi", "To buy (value)", 9, 0, 3, 2, measures=["buy_value"]),
             V("t1", "table", "Claude's brief - company, each zone and each owner team", 0, 2, 12, 12,
               dims=["brief_scope", "brief_text"], measures=["briefs"], sort={"by": "brief_scope", "dir": "asc"}, limit=20),
             V("m1", "matrix", "Suggestions by type and priority", 0, 14, 7, 6, dims=["category", "priority"],
               measures=["suggestions"]),
             V("b1", "bar", "P1 suggestions by zone", 7, 14, 5, 6, dims=["zone"], measures=["p1"],
               sort={"by": "p1", "dir": "desc"}, limit=12),
         ]},
        {"id": "p2", "name": "What to buy",
         "slicers": [{"dim": "priority"}, {"dim": "division"}, {"dim": "major_category"}, {"dim": "hero_tier"}, {"dim": "vendor"}],
         "visuals": _type_kpis("BUY", "To buy") + [
             _type_table("BUY", "Articles to buy, most urgent first", ["buy_item", "buy_mc", "buy_vendor"], 500)]},
        {"id": "p3", "name": "What to send to stores",
         "slicers": [{"dim": "priority"}] + _LOC + [{"dim": "rdc"}, {"dim": "division"}],
         "visuals": _type_kpis("SEND", "To send") + [
             _type_table("SEND", "Send from the RDC, most urgent first (never more than the RDC holds)",
                         ["send_where", "send_item"], 2000)]},
        {"id": "p4", "name": "Move between stores",
         "slicers": [{"dim": "priority"}] + _LOC + [{"dim": "division"}],
         "visuals": _type_kpis("MOVE", "To move") + [
             _type_table("MOVE", "Transfer excess to stores of the same RDC that are short", ["move_where", "move_item"], 2000)]},
        {"id": "p5", "name": "What is wrong in stores",
         "slicers": [{"dim": "priority"}] + _LOC + [{"dim": "rule"}],
         "visuals": [
             V("k1", "kpi", "Store issues", 0, 0, 4, 2, measures=["store_n"]),
             V("k2", "kpi", "P1 store issues", 4, 0, 4, 2, measures=["store_p1"]),
             V("b1", "bar", "Store issues by zone", 8, 0, 4, 6, dims=["zone"], measures=["store_n"],
               sort={"by": "store_n", "dir": "desc"}, limit=12),
             V("t1", "table", "Stores and what is wrong, most serious first", 0, 2, 8, 16,
               dims=["store_priority", "store_where", "store_alert", "store_action", "store_evidence"],
               measures=["store_value"], sort={"by": "store_score", "dir": "desc"}, limit=1000),
         ]},
        {"id": "p6", "name": "Range fix in stores",
         "slicers": [{"dim": "priority"}] + _LOC + [{"dim": "division"}, {"dim": "rule"}],
         "visuals": _type_kpis("RANGE", "Range fixes") + [
             _type_table("RANGE", "Categories over-, under- or wrongly placed in a store", ["range_where", "range_mc"], 2000)]},
        {"id": "p8", "name": "Store fill rate",
         "slicers": [{"dim": "priority"}] + _LOC + [{"dim": "division"}, {"dim": "major_category"}, {"dim": "rule"}],
         "visuals": _type_kpis("FILL", "Fill-rate alerts") + [
             _type_table("FILL", "Categories below 85% of MBQ, stores with a low fill rate, stores ARS cannot fill",
                         ["fill_where", "fill_mc"], 2000)]},
        {"id": "p7", "name": "DC placement",
         "slicers": [{"dim": "priority"}, {"dim": "rdc"}, {"dim": "division"}, {"dim": "rule"}],
         "visuals": _type_kpis("DC", "DC alerts") + [
             _type_table("DC", "DC stock against the demand of the stores it serves", ["dc_where", "dc_mc"], 1000)]},
    ]},
)
REPORTS = [REPORT]


# ---------------------------------------------------------------- store fill rate (analysis behind the fill-rate alerts)
FILL_DATASET = dict(
    code="store_fill_rate", name="Store fill rate - stock against MBQ",
    description="Sales-area stock (SLoc 0001) of every store and major category against its MBQ - the norm of the latest "
                "clean ARS allocation run (display quantity plus daily sale budget x cover days) - with the shortfall, its "
                "value at selling price, the excess over 110% of MBQ, sale and the stock at the store's RDC. Daily, 30 days kept.",
    view="V_BI_STORE_FILL_RATE", sql="select * from V2RETAIL.SILVER.STORE_FILL_RATE", snapshot_dim="as_of", scope="ST_CD",
    cache=3600, audience=AUDIENCE,
    refresh="Daily with the AI suggestions (after the 09:30 / 13:30 IST hero build). MBQ is from the latest ARS run without "
            "ad-hoc rows; stock from BRONZE.DAY_WISE_ST_VAR_STOCK SLoc 0001.",
    dimensions=[D("as_of", "As of", "AS_OF_DT", "date", "Time"), D("zone", "Zone", "ZONE_CODE", group=_L),
                D("region", "Region", "REGION_CODE", group=_L), D("cluster", "Cluster", "CLUSTER_CODE", group=_L),
                D("store", "Store code", "ST_CD", group=_L), D("store_name", "Store", "ST_NM", group=_L),
                D("store_status", "Store status", "ST_STATUS", group=_L), D("rdc", "RDC", "RDC_CD", group=_L),
                D("segment", "Segment", "SEG", group=_P), D("division", "Division", "DIV", group=_P),
                D("sub_division", "Sub-division", "SUB_DIV", group=_P), D("major_category", "Major category", "MAJ_CAT", group=_P),
                D("fill_band", "Fill-rate band", "FR_BAND", group=_W), D("ars_session", "ARS run", "ARS_SESSION", group=_W)],
    measures=[M("mbq", "MBQ (norm)", "MBQ"), M("display_q", "Display quantity", "DISP_Q"), M("stock_q", "Sales-area stock", "STOCK_Q"),
              M("stock_v", "Sales-area stock value", "STOCK_V", fmt="inr"), M("fill_q", "Stock up to MBQ", "FILL_Q"),
              M("short_q", "Short of MBQ (units)", "SHORT_Q"), M("short_v", "Short of MBQ (value)", "SHORT_V", fmt="inr"),
              M("excess_q", "Above 110% of MBQ (units)", "EXCESS_Q"), M("sale_q_l7d", "Sale qty L7D", "SALE_Q_L7D"),
              M("sale_q_l30d", "Sale qty L30D", "SALE_Q_L30D"), M("sale_v_l30d", "Sale L30D", "SALE_V_L30D", fmt="inr"),
              M("rdc_stock", "RDC stock of the category", "RDC_STK_Q", agg="max"),
              M("categories", "Store x categories", "MBQ_F"), M("below85", "Categories below 85%", "BELOW85_F"),
              M("selling", "Selling categories", "SELLING_F"),
              M("stores", "Stores", "ST_CD", agg="count_distinct"),
              M("fill_rate", "Fill rate % (stock up to MBQ)", agg="ratio", num="fill_q", den="mbq", fmt="pct"),
              M("stock_vs_mbq", "Stock vs MBQ % (incl. excess)", agg="ratio", num="stock_q", den="mbq", fmt="pct"),
              M("below85_share", "Share of categories below 85%", agg="ratio", num="below85", den="categories", fmt="pct")],
)

_FILL_LOC = [{"dim": "zone"}, {"dim": "region"}, {"dim": "cluster"}, {"dim": "division"}]
FILL_REPORT = dict(
    name="Store fill rate", category=CATEGORY, dataset="store_fill_rate", audience=AUDIENCE,
    description="How full each store is against its ARS MBQ, by zone, store and category: the fill rate, the categories "
                "below 85%, the shortfall in units and value, the excess, and the trend. The AI suggestions report turns "
                "every gap into an action.",
    definition={"version": 1, "filters": [], "pages": [
        {"id": "p1", "name": "Overview", "slicers": _FILL_LOC + [{"dim": "store_status"}],
         "visuals": [
             V("k1", "kpi", "Fill rate (stock up to MBQ)", 0, 0, 3, 2, measures=["fill_rate"]),
             V("k2", "kpi", "Categories below 85%", 3, 0, 3, 2, measures=["below85"]),
             V("k3", "kpi", "Short of MBQ (units)", 6, 0, 3, 2, measures=["short_q"]),
             V("k4", "kpi", "Short of MBQ (value)", 9, 0, 3, 2, measures=["short_v"]),
             V("c1", "column", "Fill rate by zone", 0, 2, 6, 6, dims=["zone"], measures=["fill_rate"],
               sort={"by": "fill_rate", "dir": "asc"}),
             V("c2", "column", "Store x categories by fill-rate band", 6, 2, 6, 6, dims=["fill_band"], measures=["categories"],
               sort={"by": "fill_band", "dir": "asc"}),
             V("l1", "line", "Fill rate by day", 0, 8, 12, 5, dims=["as_of"], measures=["fill_rate"],
               sort={"by": "as_of", "dir": "asc"}),
             V("t1", "table", "Regions", 0, 13, 12, 7, dims=["zone", "region"],
               measures=["stores", "mbq", "stock_q", "fill_rate", "stock_vs_mbq", "below85", "short_q", "short_v", "excess_q"],
               sort={"by": "fill_rate", "dir": "asc"}, limit=200),
         ]},
        {"id": "p2", "name": "Stores", "slicers": _FILL_LOC + [{"dim": "rdc"}],
         "visuals": [
             V("b1", "bar", "Lowest 15 stores by fill rate", 0, 0, 5, 9, dims=["store_name"], measures=["fill_rate"],
               sort={"by": "fill_rate", "dir": "asc"}, limit=15),
             V("t1", "table", "Stores, lowest fill first", 5, 0, 7, 14, dims=["store", "store_name", "cluster"],
               measures=["fill_rate", "stock_vs_mbq", "below85", "short_q", "short_v", "excess_q", "sale_v_l30d"],
               sort={"by": "fill_rate", "dir": "asc"}, limit=1000),
         ]},
        {"id": "p3", "name": "Categories", "slicers": [{"dim": "zone"}, {"dim": "region"}, {"dim": "division"}, {"dim": "sub_division"}],
         "visuals": [
             V("b1", "bar", "Biggest shortfall by major category (value)", 0, 0, 5, 9, dims=["major_category"],
               measures=["short_v"], sort={"by": "short_v", "dir": "desc"}, limit=15),
             V("t1", "table", "Major categories", 5, 0, 7, 14, dims=["division", "major_category"],
               measures=["fill_rate", "stock_vs_mbq", "below85", "short_q", "short_v", "excess_q", "rdc_stock"],
               sort={"by": "short_v", "dir": "desc"}, limit=1000),
         ]},
        {"id": "p4", "name": "Store x category", "slicers": _FILL_LOC + [{"dim": "store"}, {"dim": "fill_band"}],
         "visuals": [
             V("t1", "table", "Every store and category, biggest shortfall first", 0, 0, 12, 16,
               dims=["store", "store_name", "major_category", "fill_band"],
               measures=["mbq", "stock_q", "fill_rate", "short_q", "short_v", "excess_q", "sale_q_l7d", "rdc_stock"],
               sort={"by": "short_v", "dir": "desc"}, limit=2000),
         ]},
    ]},
)
DATASETS.append(FILL_DATASET)
REPORTS.append(FILL_REPORT)
