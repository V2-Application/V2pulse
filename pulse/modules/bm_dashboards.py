"""Dashboards for the B&M reports: one per report in bm_reports (Merchandising & Planning, and Sourcing / SRM).

The table report shows every row; its dashboard answers where the gaps are and how big they are: tiles, charts by zone,
division, vendor or stage, and ranked tables down to the row. Each dashboard has its own governed dataset over the
report's PULSE view (V_RPT_BM_* / V_RPT_SRM_*), so the report and its dashboard read the same rows. The dataset views
(V_BI_BM_* / V_BI_SRM_*) only add region and cluster, 0/1 flag columns that can be counted, and sortable bands.
Store reports are scoped on STORE_CODE like their tables; the SRM ones are not store data and are limited by audience.

hero_catalog adds these to its DATASETS / REPORTS, so they are seeded with the other library dashboards. seed_links
files a link to each dashboard next to its report in the tree (one time; admins own the tree after that)."""
import uuid

from .bi_catalog import D, M, V

MERCH_CATEGORY = "Merchandising reports"
SRM_CATEGORY = "Sourcing reports"
MERCH_AUDIENCE = ["EXEC", "MIS_CENTRAL", "PLATFORM", "DEPT_MIS_OWNER", "HO_STAFF", "MERCH", "SOURCING", "FINANCE",
                  "ZONAL_HEAD", "REGIONAL_HEAD", "AREA_MGR", "STORE_MGR", "WH_OPS"]
SRM_AUDIENCE = ["EXEC", "MIS_CENTRAL", "PLATFORM", "DEPT_MIS_OWNER", "HO_STAFF", "MERCH", "SOURCING", "FINANCE"]
_PULSE = "V2RETAIL.PULSE"
_HIER = " left join V2RETAIL.PULSE.V_STORE_HIERARCHY h on h.STORE_CODE = r.STORE_CODE"
_RC = "coalesce(h.REGION_CODE, 'Unmapped') REGION_CODE, coalesce(h.CLUSTER_CODE, 'Unmapped') CLUSTER_CODE"
_T, _S, _P, _W, _V = "Time", "Store", "Merchandise", "Status", "Vendor"


def _store_dims(status_col="ST_STATUS"):
    return [D("zone", "Zone", "ZONE_CODE", group=_S), D("region", "Region", "REGION_CODE", group=_S),
            D("cluster", "Cluster", "CLUSTER_CODE", group=_S), D("store", "Store code", "STORE_CODE", group=_S),
            D("store_name", "Store", "STORE_NAME", group=_S)] + ([D("store_status", "Store status", status_col, group=_S)] if status_col else [])


def _merch_dims(season=True):
    out = [D("segment", "Segment", "SEG", group=_P), D("division", "Division", "DIVISION", group=_P),
           D("sub_division", "Sub-division", "SUB_DIV", group=_P), D("major_category", "Major category", "MAJOR_CATEGORY", group=_P)]
    return out + ([D("season", "Season", "SSN", group=_P)] if season else [])


def _gap(col):
    return "iff(%s = 'GAP', 1, 0)" % col


def _flag(col):
    return "iff(%s = 'GAP', 'Gap', 'OK')" % col


_STORES = M("stores", "Stores", "STORE_CODE", agg="count_distinct")
_ROWS = M("categories", "Store x categories", "ROW_F")
_LOC = [{"dim": "zone"}, {"dim": "region"}, {"dim": "cluster"}]


def _table(id_, title, x, y, w, h, dims, measures, by, dir_="desc", limit=1000):
    return V(id_, "table", title, x, y, w, h, dims=dims, measures=measures, sort={"by": by, "dir": dir_}, limit=limit)


def _chart(id_, type_, title, x, y, w, h, dim, measure, dir_="desc", limit=15, by=None):
    return V(id_, type_, title, x, y, w, h, dims=[dim], measures=[measure], sort={"by": by or measure, "dir": dir_}, limit=limit)


def _kpis(*items):
    """Up to four tiles across the top row: (title, measure)."""
    w = 12 // len(items)
    return [V("k%d" % (i + 1), "kpi", t, i * w, 0, w, 2, measures=[m]) for i, (t, m) in enumerate(items)]


DATASETS, REPORTS = [], []


def _add(code, report_code, name, description, view, sql, dims, measures, scope, audience, category, refresh, pages):
    DATASETS.append(dict(code=code, name=name, description=description, view=view, sql=sql, snapshot_dim="as_of",
                         scope=scope, cache=1800, audience=audience, refresh=refresh, dimensions=dims, measures=measures))
    REPORTS.append(dict(name="%s · dashboard" % name, category=category, dataset=code, audience=audience,
                        report_code=report_code, description=description,
                        definition={"version": 1, "filters": [], "pages": pages}))


# ---------------------------------------------------------------- Display GAP
_add("bm_display_gap", "bm-display-gap", "Display GAP",
     "This month's display plan against last month, LYSP and the auto plan for every store and major category: how "
     "many categories carry a gap of each kind, and where the fixtures and MBQ moved.",
     "V_BI_BM_DISPLAY_GAP",
     """select to_date(r.REPORT_MONTH || '-01') AS_OF, r.REPORT_MONTH, r.PLAN_MONTH, r.STORE_CODE, r.STORE_NAME, r.STATE,
       r.ZONE_CODE, """ + _RC + """, r.RDC, r.STORE_STATUS ST_STATUS, r.SEGMENT SEG, r.DIVISION, r.SUB_DIVISION SUB_DIV,
       r.MAJOR_CATEGORY, r.SEASON SSN, r.FIX_LAST_SEPT FIX_PRIOR, r.FIX_CURRENT_OCT FIX_PLAN, r.FIX_LYSP, r.FIX_LYSP_AUTO FIX_AUTO,
       r.GAP_FIX_VS_SEPT GAP_FIX_PRIOR, r.GAP_FIX_VS_LYSP GAP_FIX_LYSP, r.GAP_FIX_VS_AUTO GAP_FIX_AUTO, r.MBQ_LAST_SEPT MBQ_PRIOR,
       r.MBQ_CURRENT_OCT MBQ_PLAN, r.MBQ_LYSP, r.GAP_QTY_VS_LYSP GAP_QTY_LYSP,
       """ + _gap("r.FLAG_FIX_VS_SEPT") + """ GAP_PRIOR_F, """ + _gap("r.FLAG_FIX_VS_LYSP") + """ GAP_LYSP_F,
       """ + _gap("r.FLAG_FIX_VS_AUTO") + """ GAP_AUTO_F, """ + _gap("r.FLAG_QTY_VS_LYSP") + """ GAP_QTY_F,
       iff(r.ANY_GAP, 1, 0) ANY_GAP_F, iff(r.ANY_GAP, 'Gap', 'No gap') GAP_STATUS,
       """ + _flag("r.FLAG_FIX_VS_SEPT") + """ FLAG_PRIOR, """ + _flag("r.FLAG_FIX_VS_LYSP") + """ FLAG_LYSP,
       """ + _flag("r.FLAG_FIX_VS_AUTO") + """ FLAG_AUTO, """ + _flag("r.FLAG_QTY_VS_LYSP") + """ FLAG_QTY, 1 ROW_F
from V2RETAIL.PULSE.V_RPT_BM_DISPLAY_GAP r""" + _HIER + """
where r.REPORT_MONTH = (select max(REPORT_MONTH) from V2RETAIL.PULSE.V_RPT_BM_DISPLAY_GAP)""",
     [D("as_of", "Plan month", "AS_OF", "date", _T), D("plan_month", "Plan month (label)", "PLAN_MONTH", group=_T)]
     + _store_dims() + [D("state", "State", "STATE", group=_S), D("rdc", "RDC", "RDC", group=_S)] + _merch_dims()
     + [D("gap_status", "Any gap", "GAP_STATUS", group=_W), D("flag_prior", "Fixtures vs prior month", "FLAG_PRIOR", group=_W),
        D("flag_lysp", "Fixtures vs LYSP", "FLAG_LYSP", group=_W), D("flag_auto", "Fixtures vs auto", "FLAG_AUTO", group=_W),
        D("flag_qty", "MBQ vs LYSP", "FLAG_QTY", group=_W)],
     [_ROWS, _STORES, M("any_gap", "Categories with a gap", "ANY_GAP_F"),
      M("gap_share", "Share with a gap", agg="ratio", num="any_gap", den="categories", fmt="pct"),
      M("gap_prior", "Gap: fixtures vs prior month", "GAP_PRIOR_F"), M("gap_lysp", "Gap: fixtures vs LYSP", "GAP_LYSP_F"),
      M("gap_auto", "Gap: fixtures vs auto", "GAP_AUTO_F"), M("gap_qty", "Gap: MBQ vs LYSP", "GAP_QTY_F"),
      M("fix_plan", "Fixtures (plan month)", "FIX_PLAN"), M("fix_prior", "Fixtures (prior month)", "FIX_PRIOR"),
      M("fix_lysp", "Fixtures (LYSP)", "FIX_LYSP"), M("fix_auto", "Fixtures (auto plan)", "FIX_AUTO"),
      M("fix_change", "Fixture change vs prior month", "GAP_FIX_PRIOR"),
      M("fix_vs_prior", "Fixtures vs prior month", agg="growth", num="fix_plan", den="fix_prior", fmt="pct"),
      M("fix_vs_lysp", "Fixtures vs LYSP", agg="growth", num="fix_plan", den="fix_lysp", fmt="pct"),
      M("mbq_plan", "MBQ (plan month)", "MBQ_PLAN"), M("mbq_prior", "MBQ (prior month)", "MBQ_PRIOR"), M("mbq_lysp", "MBQ (LYSP)", "MBQ_LYSP"),
      M("mbq_vs_prior", "MBQ vs prior month", agg="growth", num="mbq_plan", den="mbq_prior", fmt="pct"),
      M("mbq_vs_lysp", "MBQ vs LYSP", agg="growth", num="mbq_plan", den="mbq_lysp", fmt="pct")],
     "STORE_CODE", MERCH_AUDIENCE, MERCH_CATEGORY, "Daily with GOLD.ST_MJ_GAP (the Display GAP build). Latest plan month only.",
     [{"id": "p1", "name": "Overview", "slicers": _LOC + [{"dim": "division"}, {"dim": "store_status"}],
       "visuals": _kpis(("Categories with a gap", "any_gap"), ("Share with a gap", "gap_share"),
                        ("Fixtures vs prior month", "fix_vs_prior"), ("MBQ vs prior month", "mbq_vs_prior")) + [
           _chart("c1", "column", "Categories with a gap by zone", 0, 2, 6, 6, "zone", "any_gap", limit=12),
           _chart("c2", "bar", "Categories with a gap by division", 6, 2, 6, 6, "division", "any_gap", limit=12),
           _table("t1", "Gaps by kind, by zone", 0, 8, 12, 7, ["zone"],
                  ["categories", "any_gap", "gap_share", "gap_prior", "gap_lysp", "gap_auto", "gap_qty", "fix_plan", "fix_prior",
                   "mbq_plan", "mbq_prior"], "any_gap"),
       ]},
      {"id": "p2", "name": "Stores", "slicers": _LOC + [{"dim": "division"}, {"dim": "store_status"}],
       "visuals": [
           _chart("b1", "bar", "Stores with the most gaps", 0, 0, 5, 10, "store_name", "any_gap"),
           _table("t1", "Stores, most gaps first", 5, 0, 7, 14, ["store", "store_name", "zone"],
                  ["any_gap", "gap_share", "gap_prior", "gap_lysp", "gap_auto", "gap_qty", "fix_plan", "fix_change", "mbq_vs_prior"], "any_gap"),
       ]},
      {"id": "p3", "name": "Categories", "slicers": _LOC + [{"dim": "division"}, {"dim": "sub_division"}],
       "visuals": [
           _chart("b1", "bar", "Major categories with the most gaps", 0, 0, 5, 10, "major_category", "any_gap"),
           _table("t1", "Major categories, most gaps first", 5, 0, 7, 14, ["division", "major_category"],
                  ["stores", "any_gap", "gap_share", "fix_plan", "fix_prior", "fix_lysp", "fix_auto", "mbq_plan", "mbq_vs_lysp"], "any_gap"),
       ]},
      {"id": "p4", "name": "Gap rows", "slicers": _LOC + [{"dim": "store"}, {"dim": "division"}, {"dim": "gap_status"},
                                                          {"dim": "flag_prior"}, {"dim": "flag_lysp"}, {"dim": "flag_auto"}, {"dim": "flag_qty"}],
       "visuals": [
           _table("t1", "Store x category with its flags (pick Any gap = Gap to see only the gaps)", 0, 0, 12, 16,
                  ["store", "store_name", "major_category", "flag_prior", "flag_lysp", "flag_auto", "flag_qty"],
                  ["fix_prior", "fix_plan", "fix_lysp", "fix_auto", "mbq_prior", "mbq_plan", "mbq_lysp", "any_gap"], "any_gap", limit=2000),
       ]}])

# ---------------------------------------------------------------- Sale Gap
_add("bm_sale_gap", "bm-sale-gap", "Sale Gap",
     "Sale against budget over the last 7 days and 4 weeks, and against last year, for every store and major category: "
     "achievement, how many categories are flagged, and the stores and categories furthest behind.",
     "V_BI_BM_SALE_GAP",
     """select r.ANCHOR_DT AS_OF, r.STORE_CODE, r.STORE_NAME, r.ZONE_CODE, """ + _RC + """, r.ST_STATUS, r.SEG, r.DIVISION, r.SUB_DIV,
       r.MAJOR_CATEGORY, r.SSN, r.SALE_L7D, r.BGT_AVG_L7D BGT_L7D, r.SALE_L4WK, r.BGT_AVG_L4WK BGT_L4WK,
       iff(r.AVG_SALE_L7D_LYSP is not null, r.AVG_SALE_L7D, null) AVG_L7D_M, r.AVG_SALE_L7D_LYSP AVG_L7D_LY,
       iff(r.AVG_SALE_L4WK_LYSP is not null, r.AVG_SALE_L4WK, null) AVG_L4W_M, r.AVG_SALE_L4WK_LYSP AVG_L4W_LY,
       iff(r.AVG_SALE_LYSP_SSN is not null, r.AVG_SALE_SSN, null) AVG_SSN_M, r.AVG_SALE_LYSP_SSN AVG_SSN_LY,
       """ + _gap("r.FLAG_L7D_ACH") + """ GAP_L7D_F, """ + _gap("r.FLAG_L4WK_ACH") + """ GAP_L4W_F,
       """ + _gap("r.FLAG_L7D_VS_LYSP") + """ GAP_L7D_LY_F, """ + _gap("r.FLAG_L4WK_VS_LYSP") + """ GAP_L4W_LY_F,
       """ + _gap("r.FLAG_SSN_VS_LYSP_SSN") + """ GAP_SSN_F,
       """ + _flag("r.FLAG_L7D_ACH") + """ FLAG_L7D, """ + _flag("r.FLAG_L4WK_ACH") + """ FLAG_L4W,
       case when r.BGT_AVG_L7D is null or r.BGT_AVG_L7D <= 0 then '9 · No budget'
            when r.SALE_L7D / r.BGT_AVG_L7D < 0.5 then '1 · Under 50%' when r.SALE_L7D / r.BGT_AVG_L7D < 0.8 then '2 · 50-80%'
            when r.SALE_L7D / r.BGT_AVG_L7D < 1 then '3 · 80-100%' else '4 · 100% and more' end ACH_BAND, 1 ROW_F
from V2RETAIL.PULSE.V_RPT_BM_SALE_GAP r""" + _HIER,
     [D("as_of", "As of", "AS_OF", "date", _T)] + _store_dims() + _merch_dims()
     + [D("ach_band", "L7D achievement band", "ACH_BAND", group=_W), D("flag_l7d", "L7D flag", "FLAG_L7D", group=_W),
        D("flag_l4w", "L4W flag", "FLAG_L4W", group=_W)],
     [_ROWS, _STORES, M("sale_l7d", "Sale L7D", "SALE_L7D", fmt="inr"), M("bgt_l7d", "Budget L7D", "BGT_L7D", fmt="inr"),
      M("ach_l7d", "L7D vs budget", agg="ratio", num="sale_l7d", den="bgt_l7d", fmt="pct"),
      M("sale_l4w", "Sale L4W", "SALE_L4WK", fmt="inr"), M("bgt_l4w", "Budget L4W", "BGT_L4WK", fmt="inr"),
      M("ach_l4w", "L4W vs budget", agg="ratio", num="sale_l4w", den="bgt_l4w", fmt="pct"),
      M("avg_l7d_m", "Avg daily sale L7D (where LY exists)", "AVG_L7D_M"), M("avg_l7d_ly", "Avg daily sale L7D LYSP", "AVG_L7D_LY"),
      M("growth_l7d", "L7D vs LYSP", agg="growth", num="avg_l7d_m", den="avg_l7d_ly", fmt="pct"),
      M("avg_l4w_m", "Avg daily sale L4W (where LY exists)", "AVG_L4W_M"), M("avg_l4w_ly", "Avg daily sale L4W LYSP", "AVG_L4W_LY"),
      M("growth_l4w", "L4W vs LYSP", agg="growth", num="avg_l4w_m", den="avg_l4w_ly", fmt="pct"),
      M("avg_ssn_m", "Avg daily sale season (where LY exists)", "AVG_SSN_M"), M("avg_ssn_ly", "Avg daily sale LY season", "AVG_SSN_LY"),
      M("growth_ssn", "Season vs LY season", agg="growth", num="avg_ssn_m", den="avg_ssn_ly", fmt="pct"),
      M("gap_l7d", "Flagged: L7D vs budget", "GAP_L7D_F"), M("gap_l4w", "Flagged: L4W vs budget", "GAP_L4W_F"),
      M("gap_l7d_ly", "Flagged: L7D vs LYSP", "GAP_L7D_LY_F"), M("gap_l4w_ly", "Flagged: L4W vs LYSP", "GAP_L4W_LY_F"),
      M("gap_ssn", "Flagged: season vs LY", "GAP_SSN_F"),
      M("gap_l7d_share", "Share flagged L7D", agg="ratio", num="gap_l7d", den="categories", fmt="pct")],
     "STORE_CODE", MERCH_AUDIENCE, MERCH_CATEGORY, "Daily with GOLD.VW_SALE_GAP. Latest day only. LY figures exist only where the store traded last year.",
     [{"id": "p1", "name": "Overview", "slicers": _LOC + [{"dim": "division"}, {"dim": "store_status"}],
       "visuals": _kpis(("Sale L7D", "sale_l7d"), ("L7D vs budget", "ach_l7d"), ("L4W vs budget", "ach_l4w"),
                        ("Categories flagged L7D", "gap_l7d")) + [
           _chart("c1", "column", "L7D vs budget by zone", 0, 2, 6, 6, "zone", "ach_l7d", "asc", 12),
           _chart("c2", "column", "Store x categories by L7D achievement", 6, 2, 6, 6, "ach_band", "categories", "asc", 10, by="ach_band"),
           _table("t1", "Divisions", 0, 8, 12, 7, ["division"],
                  ["sale_l7d", "bgt_l7d", "ach_l7d", "sale_l4w", "bgt_l4w", "ach_l4w", "growth_l7d", "gap_l7d", "gap_l7d_share"], "ach_l7d", "asc"),
       ]},
      {"id": "p2", "name": "Stores", "slicers": _LOC + [{"dim": "division"}, {"dim": "store_status"}],
       "visuals": [
           _chart("b1", "bar", "Stores furthest behind budget (L7D)", 0, 0, 5, 10, "store_name", "ach_l7d", "asc"),
           _table("t1", "Stores, furthest behind first", 5, 0, 7, 14, ["store", "store_name", "zone"],
                  ["sale_l7d", "bgt_l7d", "ach_l7d", "ach_l4w", "growth_l7d", "growth_ssn", "gap_l7d", "gap_l7d_share"], "ach_l7d", "asc"),
       ]},
      {"id": "p3", "name": "Categories", "slicers": _LOC + [{"dim": "division"}, {"dim": "sub_division"}],
       "visuals": [
           _chart("b1", "bar", "Major categories furthest behind budget (L7D)", 0, 0, 5, 10, "major_category", "ach_l7d", "asc"),
           _table("t1", "Major categories", 5, 0, 7, 14, ["division", "major_category"],
                  ["sale_l7d", "bgt_l7d", "ach_l7d", "ach_l4w", "growth_l7d", "growth_l4w", "gap_l7d", "gap_l4w"], "ach_l7d", "asc"),
       ]},
      {"id": "p4", "name": "Store x category", "slicers": _LOC + [{"dim": "store"}, {"dim": "division"}, {"dim": "ach_band"},
                                                                  {"dim": "flag_l7d"}, {"dim": "flag_l4w"}],
       "visuals": [
           _table("t1", "Every store and category, biggest L7D shortfall to budget first", 0, 0, 12, 16,
                  ["store", "store_name", "major_category", "ach_band", "flag_l7d", "flag_l4w"],
                  ["sale_l7d", "bgt_l7d", "ach_l7d", "sale_l4w", "bgt_l4w", "ach_l4w", "growth_l7d"], "bgt_l7d", limit=2000),
       ]}])

# ---------------------------------------------------------------- ARS gap
_add("bm_ars_gap", "bm-ars-gap", "ARS gap",
     "The latest clean ARS allocation run: fill rate against MBQ before and after the run, what was allocated, and the "
     "shortfall and excess left, by zone, store, RDC and category.",
     "V_BI_BM_ARS_GAP",
     """select r.APPROVED_AT::date AS_OF, r.SESSION_ID, r.STORE_CODE, r.STORE_NAME, r.ZONE_CODE, """ + _RC + """, r.RDC, r.ST_STATUS,
       r.SEG, r.DIVISION, r.SUB_DIV, r.MAJOR_CATEGORY, r.SSN, r.MBQ, r.DISP_Q, r.STOCK_BEFORE, r.ALLOCATED, r.STOCK_AFTER,
       r.SHORT_AFTER, r.EXCESS_AFTER, least(r.STOCK_BEFORE, coalesce(r.MBQ, 0)) FILL_BEFORE, least(r.STOCK_AFTER, coalesce(r.MBQ, 0)) FILL_AFTER,
       r.SALE_Q_L7D, r.DC_STOCK_LEFT, r.HOLD_Q, iff(r.SHORT_AFTER > 0, 1, 0) SHORT_F, iff(r.EXCESS_AFTER > 0, 1, 0) EXCESS_F,
       case when coalesce(r.MBQ, 0) <= 0 then '9 · No MBQ' when r.STOCK_AFTER < 0.85 * r.MBQ then '1 · Short (<85%)'
            when r.STOCK_AFTER < r.MBQ then '2 · 85-100%' when r.STOCK_AFTER <= 1.1 * r.MBQ then '3 · In band (100-110%)'
            else '4 · Excess (>110%)' end FILL_BAND, 1 ROW_F
from V2RETAIL.PULSE.V_RPT_BM_ARS_GAP r""" + _HIER,
     [D("as_of", "Run approved", "AS_OF", "date", _T), D("ars_run", "ARS run", "SESSION_ID", group=_T)] + _store_dims()
     + [D("rdc", "RDC", "RDC", group=_S)] + _merch_dims() + [D("fill_band", "Fill band after the run", "FILL_BAND", group=_W)],
     [_ROWS, _STORES, M("mbq", "MBQ", "MBQ"), M("display_q", "Display qty", "DISP_Q"), M("stock_before", "Stock before run", "STOCK_BEFORE"),
      M("allocated", "Allocated", "ALLOCATED"), M("stock_after", "Stock after run", "STOCK_AFTER"),
      M("short_after", "Short of MBQ after run", "SHORT_AFTER"), M("excess_after", "Above 110% of MBQ after run", "EXCESS_AFTER"),
      M("fill_before_q", "Stock up to MBQ before", "FILL_BEFORE"), M("fill_after_q", "Stock up to MBQ after", "FILL_AFTER"),
      M("fill_before", "Fill rate before run", agg="ratio", num="fill_before_q", den="mbq", fmt="pct"),
      M("fill_after", "Fill rate after run", agg="ratio", num="fill_after_q", den="mbq", fmt="pct"),
      M("short_rows", "Categories still short", "SHORT_F"), M("excess_rows", "Categories in excess", "EXCESS_F"),
      M("sale_q_l7d", "Sale qty L7D", "SALE_Q_L7D"), M("hold_q", "On hold", "HOLD_Q"),
      M("dc_stock_left", "DC stock left (category)", "DC_STOCK_LEFT", agg="max")],
     "STORE_CODE", MERCH_AUDIENCE, MERCH_CATEGORY, "With each ARS run (BRONZE.ARS_GRID_MJ): the latest run without ad-hoc rows.",
     [{"id": "p1", "name": "Overview", "slicers": _LOC + [{"dim": "rdc"}, {"dim": "division"}],
       "visuals": _kpis(("Fill rate after run", "fill_after"), ("Allocated", "allocated"), ("Short of MBQ after run", "short_after"),
                        ("Above 110% of MBQ", "excess_after")) + [
           _chart("c1", "column", "Fill rate after the run by zone", 0, 2, 6, 6, "zone", "fill_after", "asc", 12),
           _chart("c2", "column", "Store x categories by fill band", 6, 2, 6, 6, "fill_band", "categories", "asc", 10, by="fill_band"),
           _table("t1", "RDCs", 0, 8, 12, 7, ["rdc"],
                  ["stores", "mbq", "stock_before", "allocated", "stock_after", "fill_before", "fill_after", "short_after", "excess_after"],
                  "short_after"),
       ]},
      {"id": "p2", "name": "Stores", "slicers": _LOC + [{"dim": "rdc"}, {"dim": "division"}],
       "visuals": [
           _chart("b1", "bar", "Stores left shortest after the run", 0, 0, 5, 10, "store_name", "short_after"),
           _table("t1", "Stores, biggest shortfall first", 5, 0, 7, 14, ["store", "store_name", "rdc"],
                  ["mbq", "allocated", "fill_before", "fill_after", "short_after", "short_rows", "excess_after", "sale_q_l7d"], "short_after"),
       ]},
      {"id": "p3", "name": "Categories", "slicers": _LOC + [{"dim": "rdc"}, {"dim": "division"}],
       "visuals": [
           _chart("b1", "bar", "Major categories left shortest", 0, 0, 5, 10, "major_category", "short_after"),
           _table("t1", "Major categories", 5, 0, 7, 14, ["division", "major_category"],
                  ["mbq", "allocated", "fill_after", "short_after", "short_rows", "excess_after", "dc_stock_left"], "short_after"),
       ]},
      {"id": "p4", "name": "Store x category", "slicers": _LOC + [{"dim": "store"}, {"dim": "rdc"}, {"dim": "division"}, {"dim": "fill_band"}],
       "visuals": [
           _table("t1", "Every store and category, biggest shortfall first", 0, 0, 12, 16,
                  ["store", "store_name", "major_category", "fill_band"],
                  ["mbq", "stock_before", "allocated", "stock_after", "short_after", "excess_after", "sale_q_l7d", "dc_stock_left"],
                  "short_after", limit=2000),
       ]}])

# ---------------------------------------------------------------- MR-ST-TTL-SCM
_add("bm_mrst_ttl_scm", "bm-mrst-ttl-scm", "MR-ST-TTL-SCM",
     "Store x major category plan and performance: sale against budget, gross margin, fixtures and density, and stock "
     "along the pipeline (floor, hub, DC, PTL). The plan month is next month.",
     "V_BI_BM_MRST_TTL_SCM",
     """select r.ANCHOR_DT AS_OF, r.STORE_CODE, r.STORE_NAME, r.ZONE_CODE, coalesce(h.REGION_CODE, 'Unmapped') REGION_CODE,
       coalesce(r.CLUSTER, h.CLUSTER_CODE, 'Unmapped') CLUSTER_CODE, r.STATE, r.RDC_NM, coalesce(r.ST_STATUS, 'Blank') ST_STATUS,
       r.SEG, r.DIVISION, r.SUB_DIV, r.MAJOR_CATEGORY, r.SSN, r.PLAN_STATUS, r.FINAL_ST_MODEL_FIX, r.PLAN_GND, r.PRV_FIX, r.LY_FIX,
       r.DISPLAY_STK, r.BGT_SALE_V, r.PLAN_BGT_SALE_V, r.BGT_DISP_Q, r.TD_SALE_V, r.MTD_SALE_V, r.MTD_GM_V, r.L7D_SALE_V,
       r.L4WK_SALE_V, r.LYMTH_SALE_V, r.K0001_Q, r.HUBPRD_Q, r.DCPRD_Q, r.PTL_Q, r.TTL_STK_Q, r.TTL_STK_V, 1 ROW_F
from V2RETAIL.PULSE.V_RPT_BM_MRST_TTL_SCM r""" + _HIER,
     [D("as_of", "As of", "AS_OF", "date", _T)] + _store_dims() + [D("state", "State", "STATE", group=_S), D("rdc", "RDC", "RDC_NM", group=_S)]
     + _merch_dims() + [D("plan_status", "Plan status", "PLAN_STATUS", group=_W)],
     [_ROWS, _STORES, M("sale_td", "Sale (day)", "TD_SALE_V", fmt="inr"), M("sale_mtd", "Sale MTD", "MTD_SALE_V", fmt="inr"),
      M("budget", "Budget sale (month)", "BGT_SALE_V", fmt="inr"), M("plan_budget", "Plan-month budget sale", "PLAN_BGT_SALE_V", fmt="inr"),
      M("ach_mtd", "MTD vs month budget", agg="ratio", num="sale_mtd", den="budget", fmt="pct"),
      M("gm_mtd", "GM MTD", "MTD_GM_V", fmt="inr"), M("gm_pct", "GM % MTD", agg="ratio", num="gm_mtd", den="sale_mtd", fmt="pct"),
      M("sale_l7d", "Sale L7D", "L7D_SALE_V", fmt="inr"), M("sale_l4w", "Sale L4W", "L4WK_SALE_V", fmt="inr"),
      M("sale_ly_month", "LY month sale", "LYMTH_SALE_V", fmt="inr"),
      M("model_fix", "Model fixtures", "FINAL_ST_MODEL_FIX"), M("plan_gnd", "Plan gondolas", "PLAN_GND"),
      M("prior_fix", "Prior fixtures", "PRV_FIX"), M("ly_fix", "LY fixtures", "LY_FIX"), M("display_stock", "Display stock", "DISPLAY_STK"),
      M("budget_disp_q", "Budget display qty", "BGT_DISP_Q"), M("floor_q", "Floor stock (0001)", "K0001_Q"),
      M("hub_prd_q", "Hub PRD", "HUBPRD_Q"), M("dc_prd_q", "DC PRD", "DCPRD_Q"), M("ptl_q", "PTL", "PTL_Q"),
      M("total_stock_q", "Total stock", "TTL_STK_Q"), M("total_stock_v", "Total stock value", "TTL_STK_V", fmt="inr"),
      M("floor_vs_display", "Floor stock vs display stock", agg="ratio", num="floor_q", den="display_stock", fmt="pct")],
     "STORE_CODE", MERCH_AUDIENCE, MERCH_CATEGORY,
     "From GOLD.MRST_SCM_MJ. Its feed has not refreshed since 8 Sep 2026; the 'As of' date shows the day the figures are from.",
     [{"id": "p1", "name": "Overview", "slicers": _LOC + [{"dim": "division"}, {"dim": "plan_status"}, {"dim": "store_status"}],
       "visuals": _kpis(("Sale MTD", "sale_mtd"), ("MTD vs month budget", "ach_mtd"), ("GM % MTD", "gm_pct"),
                        ("Total stock value", "total_stock_v")) + [
           _chart("c1", "column", "Sale MTD by zone", 0, 2, 6, 6, "zone", "sale_mtd", limit=12),
           _chart("c2", "bar", "Total stock value by division", 6, 2, 6, 6, "division", "total_stock_v", limit=12),
           _table("t1", "Divisions", 0, 8, 12, 7, ["division"],
                  ["sale_mtd", "budget", "ach_mtd", "gm_pct", "sale_l7d", "sale_ly_month", "model_fix", "display_stock", "floor_q",
                   "total_stock_q", "total_stock_v"], "sale_mtd"),
       ]},
      {"id": "p2", "name": "Stores", "slicers": _LOC + [{"dim": "division"}, {"dim": "store_status"}],
       "visuals": [
           _chart("b1", "bar", "Stores furthest behind budget", 0, 0, 5, 10, "store_name", "ach_mtd", "asc"),
           _table("t1", "Stores", 5, 0, 7, 14, ["store", "store_name", "cluster"],
                  ["sale_mtd", "budget", "ach_mtd", "gm_pct", "model_fix", "floor_vs_display", "total_stock_v"], "ach_mtd", "asc"),
       ]},
      {"id": "p3", "name": "Stock pipeline", "slicers": _LOC + [{"dim": "rdc"}, {"dim": "division"}],
       "visuals": [
           _table("t1", "Where the stock sits, by major category", 0, 0, 12, 14, ["division", "major_category"],
                  ["display_stock", "floor_q", "floor_vs_display", "hub_prd_q", "dc_prd_q", "ptl_q", "total_stock_q", "total_stock_v",
                   "sale_l4w"], "total_stock_v"),
       ]},
      {"id": "p4", "name": "Store x category", "slicers": _LOC + [{"dim": "store"}, {"dim": "division"}, {"dim": "plan_status"}],
       "visuals": [
           _table("t1", "Every store and category, biggest MTD sale first", 0, 0, 12, 16,
                  ["store", "store_name", "major_category", "plan_status"],
                  ["sale_mtd", "budget", "ach_mtd", "gm_mtd", "model_fix", "prior_fix", "display_stock", "floor_q", "total_stock_q"],
                  "sale_mtd", limit=2000),
       ]}])

# ---------------------------------------------------------------- MRDC MJ daily
_add("bm_mrdc_mj", "bm-mrdc-mj", "MRDC MJ daily",
     "The latest day's sale for every store and major category: TD, MTD, L7D, L30D and YTD, against LY same month, with "
     "gross margin, stock and the projected month.",
     "V_BI_BM_MRDC_MJ",
     """select r.REPORT_DATE AS_OF, r.STORE_CODE, r.STORE_NAME, r.ZONE_CODE, """ + _RC + """, r.STATE, r.RDC,
       r.SEGMENT SEG, r.DIVISION, r.SUB_DIVISION SUB_DIV, r.MAJOR_CATEGORY,
       r.SEASON_NAME SSN, r.TD_SALE_VAL, r.MTD_SALE_VAL, r.L7D_SALE_VAL, r.L30D_SALE_VAL, r.YTD_SALE_VAL, r.LYSM_SALE_VAL,
       r.MTD_GM_VAL, r.STK_QTY, r.STK_VAL, r.PROJ_FULL_MONTH_V, 1 ROW_F
from V2RETAIL.PULSE.V_RPT_BM_MRDC_MJ r""" + _HIER,
     [D("as_of", "As of", "AS_OF", "date", _T)] + _store_dims(None) + [D("state", "State", "STATE", group=_S), D("rdc", "RDC", "RDC", group=_S)]
     + _merch_dims(),
     [_ROWS, _STORES, M("sale_td", "Sale (day)", "TD_SALE_VAL", fmt="inr"), M("sale_mtd", "Sale MTD", "MTD_SALE_VAL", fmt="inr"),
      M("sale_l7d", "Sale L7D", "L7D_SALE_VAL", fmt="inr"), M("sale_l30d", "Sale L30D", "L30D_SALE_VAL", fmt="inr"),
      M("sale_ytd", "Sale YTD", "YTD_SALE_VAL", fmt="inr"), M("sale_ly_month", "LY same month", "LYSM_SALE_VAL", fmt="inr"),
      M("projected", "Projected month", "PROJ_FULL_MONTH_V", fmt="inr"),
      M("proj_vs_ly", "Projected month vs LY same month", agg="growth", num="projected", den="sale_ly_month", fmt="pct"),
      M("gm_mtd", "GM MTD", "MTD_GM_VAL", fmt="inr"), M("gm_pct", "GM % MTD", agg="ratio", num="gm_mtd", den="sale_mtd", fmt="pct"),
      M("stock_q", "Stock qty", "STK_QTY"), M("stock_v", "Stock value", "STK_VAL", fmt="inr"),
      M("cover", "Stock value / L30D sale", agg="ratio", num="stock_v", den="sale_l30d")],
     "STORE_CODE", MERCH_AUDIENCE, MERCH_CATEGORY,
     "Daily with GOLD.MART_MRDC_MJ_DAILY. Latest day only. The month budget is not loaded in that mart, so there is no achievement.",
     [{"id": "p1", "name": "Overview", "slicers": _LOC + [{"dim": "division"}, {"dim": "season"}],
       "visuals": _kpis(("Sale (day)", "sale_td"), ("Sale MTD", "sale_mtd"), ("Projected month vs LY", "proj_vs_ly"),
                        ("GM % MTD", "gm_pct")) + [
           _chart("c1", "column", "Sale MTD by zone", 0, 2, 6, 6, "zone", "sale_mtd", limit=12),
           _chart("c2", "bar", "Sale MTD by division", 6, 2, 6, 6, "division", "sale_mtd", limit=12),
           _table("t1", "Zones", 0, 8, 12, 7, ["zone"],
                  ["stores", "sale_td", "sale_mtd", "sale_l7d", "sale_l30d", "sale_ly_month", "projected", "proj_vs_ly", "gm_pct",
                   "stock_v", "cover"], "sale_mtd"),
       ]},
      {"id": "p2", "name": "Stores", "slicers": _LOC + [{"dim": "division"}, {"dim": "season"}],
       "visuals": [
           _chart("b1", "bar", "Stores with the weakest projected month vs LY", 0, 0, 5, 10, "store_name", "proj_vs_ly", "asc"),
           _table("t1", "Stores", 5, 0, 7, 14, ["store", "store_name", "zone"],
                  ["sale_td", "sale_mtd", "sale_ly_month", "projected", "proj_vs_ly", "gm_pct", "stock_v", "cover"], "sale_mtd"),
       ]},
      {"id": "p3", "name": "Categories", "slicers": _LOC + [{"dim": "division"}, {"dim": "sub_division"}],
       "visuals": [
           _chart("b1", "bar", "Top major categories by sale MTD", 0, 0, 5, 10, "major_category", "sale_mtd"),
           _table("t1", "Major categories", 5, 0, 7, 14, ["division", "major_category"],
                  ["sale_mtd", "sale_l30d", "sale_ytd", "sale_ly_month", "proj_vs_ly", "gm_pct", "stock_v", "cover"], "sale_mtd"),
       ]}])

# ---------------------------------------------------------------- Auto Sale
_add("bm_auto_sale", "bm-auto-sale", "Auto Sale",
     "The latest auto-sale run for the current month: budget, MTD sale, the auto and final sale plan, and how the plan "
     "compares to budget, by zone, store and category.",
     "V_BI_BM_AUTO_SALE",
     """select r.MONTH_DATE AS_OF, r.MONTH_YEAR, r.STORE_CODE, r.STORE_NAME, r.ZONE_CODE, """ + _RC + """, coalesce(r.STAT, 'Blank') ST_STATUS,
       r.SEG, r.DIVISION, r.SUB_DIV, r.MAJOR_CATEGORY, r.SSN, r.BGT_V, r.BGT_Q, r.MTD_V, r.MTD_Q, r.AUTO_V, r.AUTO_Q, r.FINAL_V,
       r.FINAL_Q, r.DISP_QTY, r.STK_0001_Q, 1 ROW_F
from V2RETAIL.PULSE.V_RPT_BM_AUTO_SALE r""" + _HIER,
     [D("as_of", "Month", "AS_OF", "date", _T)] + _store_dims() + _merch_dims(),
     [_ROWS, _STORES, M("budget", "Budget", "BGT_V", fmt="inr"), M("budget_q", "Budget qty", "BGT_Q"),
      M("mtd", "Sale MTD", "MTD_V", fmt="inr"), M("mtd_q", "Sale qty MTD", "MTD_Q"),
      M("auto", "Auto sale plan", "AUTO_V", fmt="inr"), M("auto_q", "Auto sale qty", "AUTO_Q"),
      M("final", "Final sale plan", "FINAL_V", fmt="inr"), M("final_q", "Final sale qty", "FINAL_Q"),
      M("final_vs_budget", "Final plan vs budget", agg="growth", num="final", den="budget", fmt="pct"),
      M("auto_vs_budget", "Auto plan vs budget", agg="growth", num="auto", den="budget", fmt="pct"),
      M("mtd_vs_final", "MTD vs final plan", agg="ratio", num="mtd", den="final", fmt="pct"),
      M("mtd_vs_budget", "MTD vs budget", agg="ratio", num="mtd", den="budget", fmt="pct"),
      M("display_q", "Display qty", "DISP_QTY"), M("floor_q", "Floor stock (0001)", "STK_0001_Q")],
     "STORE_CODE", MERCH_AUDIENCE, MERCH_CATEGORY, "With each auto-sale run (AKS_GOLD.VW_AUTO_SALE_LIVE): the latest run, current month.",
     [{"id": "p1", "name": "Overview", "slicers": _LOC + [{"dim": "division"}, {"dim": "store_status"}],
       "visuals": _kpis(("Budget", "budget"), ("Final sale plan", "final"), ("Final plan vs budget", "final_vs_budget"),
                        ("MTD vs final plan", "mtd_vs_final")) + [
           _chart("c1", "column", "Final plan vs budget by zone", 0, 2, 6, 6, "zone", "final_vs_budget", "asc", 12),
           _chart("c2", "column", "MTD vs final plan by store status", 6, 2, 6, 6, "store_status", "mtd_vs_final", "asc", 8),
           _table("t1", "Divisions", 0, 8, 12, 7, ["division"],
                  ["budget", "auto", "final", "final_vs_budget", "mtd", "mtd_vs_final", "mtd_vs_budget", "floor_q"], "budget"),
       ]},
      {"id": "p2", "name": "Stores", "slicers": _LOC + [{"dim": "division"}, {"dim": "store_status"}],
       "visuals": [
           _chart("b1", "bar", "Stores planned furthest below budget", 0, 0, 5, 10, "store_name", "final_vs_budget", "asc"),
           _table("t1", "Stores", 5, 0, 7, 14, ["store", "store_name", "store_status"],
                  ["budget", "auto", "final", "final_vs_budget", "mtd", "mtd_vs_final"], "final_vs_budget", "asc"),
       ]},
      {"id": "p3", "name": "Categories", "slicers": _LOC + [{"dim": "division"}, {"dim": "sub_division"}],
       "visuals": [
           _chart("b1", "bar", "Major categories planned furthest below budget", 0, 0, 5, 10, "major_category", "final_vs_budget", "asc"),
           _table("t1", "Major categories", 5, 0, 7, 14, ["division", "major_category"],
                  ["budget", "auto", "final", "final_vs_budget", "auto_vs_budget", "mtd", "mtd_vs_final", "final_q"], "budget"),
       ]}])

# ---------------------------------------------------------------- Article-colour stock
_add("bm_art_colour_stock", "bm-art-colour-stock", "Article-colour stock",
     "Stock of every article-colour on the latest day: in the stores (floor, backroom, discounted), at the DCs and in total, "
     "by division and category, and which article-colours sit only at the DC or only in stores.",
     "V_BI_BM_ART_COLOUR_STOCK",
     """select r.STOCK_DATE AS_OF, r.GEN_ART, coalesce(r.GEN_ART_DESC, r.GEN_ART) GEN_ART_DESC, r.COLOR_NAME,
       coalesce(r.SEG, 'Unmapped') SEG, coalesce(r.DIVISION, 'Unmapped') DIVISION, coalesce(r.SUB_DIV, 'Unmapped') SUB_DIV,
       coalesce(r.MAJOR_CATEGORY, 'Unmapped') MAJOR_CATEGORY, r.MRP,
       case when r.MRP is null then '9 · Unknown' when r.MRP < 300 then '1 · Under 300' when r.MRP < 600 then '2 · 300-599'
            when r.MRP < 1000 then '3 · 600-999' when r.MRP < 2000 then '4 · 1000-1999' else '5 · 2000 and more' end MRP_BAND,
       case when coalesce(r.STORE_STK_Q, 0) > 0 and coalesce(r.MSA_STK_Q, 0) > 0 then '1 · Stores and DC'
            when coalesce(r.STORE_STK_Q, 0) > 0 then '2 · Stores only' when coalesce(r.MSA_STK_Q, 0) > 0 then '3 · DC only'
            else '4 · No stock' end STOCK_WHERE,
       r.STK_0001_Q, r.STK_0002_Q, r.STK_0006_Q, r.STORE_STK_Q, r.STORE_STK_V, r.MSA_STK_Q, r.MSA_STK_V, r.CO_STK_QTY, r.CO_STK_VAL, 1 ROW_F
from V2RETAIL.PULSE.V_RPT_BM_ART_COLOUR_STOCK r""",
     [D("as_of", "As of", "AS_OF", "date", _T)] + [D("segment", "Segment", "SEG", group=_P), D("division", "Division", "DIVISION", group=_P),
        D("sub_division", "Sub-division", "SUB_DIV", group=_P), D("major_category", "Major category", "MAJOR_CATEGORY", group=_P),
        D("article", "Generic article", "GEN_ART", group="Article"), D("description", "Description", "GEN_ART_DESC", group="Article"),
        D("colour", "Colour", "COLOR_NAME", group="Article"), D("mrp_band", "MRP band", "MRP_BAND", group="Article"),
        D("stock_where", "Where the stock is", "STOCK_WHERE", group=_W)],
     [M("article_colours", "Article-colours", "ROW_F"), M("articles", "Generic articles", "GEN_ART", agg="count_distinct"),
      M("floor_q", "Store floor (0001)", "STK_0001_Q"), M("backroom_q", "Store backroom (0002)", "STK_0002_Q"),
      M("discounted_q", "Discounted (0006)", "STK_0006_Q"), M("store_q", "Store stock", "STORE_STK_Q"),
      M("store_v", "Store stock value", "STORE_STK_V", fmt="inr"), M("dc_q", "DC stock", "MSA_STK_Q"),
      M("dc_v", "DC stock value", "MSA_STK_V", fmt="inr"), M("company_q", "Company stock", "CO_STK_QTY"),
      M("company_v", "Company stock value", "CO_STK_VAL", fmt="inr"),
      M("dc_share", "Share of value at the DC", agg="ratio", num="dc_v", den="company_v", fmt="pct")],
     None, MERCH_AUDIENCE, MERCH_CATEGORY, "Daily with GOLD.ART_COLOR_STOCK_SNAPSHOT. Latest day only. Not store-scoped: it is company stock.",
     [{"id": "p1", "name": "Overview", "slicers": [{"dim": "segment"}, {"dim": "division"}, {"dim": "mrp_band"}],
       "visuals": _kpis(("Company stock value", "company_v"), ("Store stock value", "store_v"), ("DC stock value", "dc_v"),
                        ("Share of value at the DC", "dc_share")) + [
           _chart("c1", "bar", "Company stock value by division", 0, 2, 6, 6, "division", "company_v", limit=12),
           _chart("c2", "column", "Article-colours by where the stock is", 6, 2, 6, 6, "stock_where", "article_colours", "asc", 6, by="stock_where"),
           _table("t1", "Divisions", 0, 8, 12, 7, ["division"],
                  ["articles", "article_colours", "floor_q", "backroom_q", "discounted_q", "store_v", "dc_v", "company_v", "dc_share"], "company_v"),
       ]},
      {"id": "p2", "name": "Categories", "slicers": [{"dim": "segment"}, {"dim": "division"}, {"dim": "stock_where"}],
       "visuals": [
           _chart("b1", "bar", "Major categories by stock value", 0, 0, 5, 10, "major_category", "company_v"),
           _table("t1", "Major categories", 5, 0, 7, 14, ["division", "major_category"],
                  ["articles", "article_colours", "store_q", "dc_q", "company_q", "company_v", "dc_share"], "company_v"),
       ]},
      {"id": "p3", "name": "Article-colours", "slicers": [{"dim": "division"}, {"dim": "major_category"}, {"dim": "stock_where"},
                                                          {"dim": "mrp_band"}],
       "visuals": [
           _table("t1", "Article-colours, biggest stock value first", 0, 0, 12, 16,
                  ["article", "description", "colour", "major_category", "stock_where"],
                  ["floor_q", "backroom_q", "discounted_q", "store_q", "dc_q", "company_q", "company_v"], "company_v", limit=2000),
       ]}])

# ---------------------------------------------------------------- Sourcing: PPT PO Routing Pending
_SRM_DIMS = [D("division", "Division", "DIVISION", group=_P), D("sub_division", "Sub-division", "SUB_DIV", group=_P),
             D("major_category", "Major category", "MAJOR_CATEGORY", group=_P), D("vendor", "Vendor", "VENDOR_NAME", group=_V),
             D("vendor_code", "Vendor code", "VENDOR_CODE", group=_V)]
_AGE = ("case when {c} is null then '9 · Unknown' when {c} <= 7 then '1 · 0-7 days' when {c} <= 15 then '2 · 8-15 days'"
        " when {c} <= 30 then '3 · 16-30 days' when {c} <= 60 then '4 · 31-60 days' else '5 · Over 60 days' end")

_add("srm_ppt_routing_pending", "srm-ppt-routing-pending", "PPT PO Routing Pending",
     "Presentations (PPTs) still moving through routing: how many are waiting at each step, how long they have waited, "
     "and which vendors and categories hold the oldest.",
     "V_BI_SRM_PPT_PENDING",
     """select r.SNAPSHOT_DATE AS_OF, r.PPT_NUMBER, r.PPT_TYPE, r.VENDOR_CODE, r.VENDOR_NAME, r.SEGMENT SEG, r.DIVISION,
       r.SUB_DIVISION SUB_DIV, r.MAJOR_CATEGORY, lpad(r.CURRENT_PENDING_STEP_NO::string, 2, '0') || ' · ' || r.CURRENT_STATUS STEP,
       coalesce(r.SRM_STATUS, 'Blank') SRM_STATUS, r.LOT_AGEING, r.PROCESS_AGEING, r.STEPS_DONE,
       """ + _AGE.format(c="r.LOT_AGEING") + """ AGE_BAND, iff(r.LOT_AGEING > 30, 1, 0) OVER30_F,
       iff(coalesce(r.PO_NUMBER::string, '') <> '', 1, 0) PO_F, 1 ROW_F
from V2RETAIL.PULSE.V_RPT_SRM_PPT_PENDING r""",
     [D("as_of", "As of", "AS_OF", "date", _T), D("ppt", "PPT number", "PPT_NUMBER", group="PPT"), D("ppt_type", "PPT type", "PPT_TYPE", group="PPT"),
      D("step", "Pending step", "STEP", group=_W), D("srm_status", "SRM status", "SRM_STATUS", group=_W),
      D("age_band", "Lot ageing band", "AGE_BAND", group=_W), D("segment", "Segment", "SEG", group=_P)] + _SRM_DIMS,
     [M("ppts", "PPTs pending", "PPT_NUMBER", agg="count_distinct"), M("rows", "Rows", "ROW_F"),
      M("lot_age", "Avg lot ageing (days)", "LOT_AGEING", agg="avg"), M("lot_age_max", "Oldest (days)", "LOT_AGEING", agg="max"),
      M("process_age", "Avg process ageing (days)", "PROCESS_AGEING", agg="avg"), M("steps_done", "Avg steps done", "STEPS_DONE", agg="avg"),
      M("over30", "Waiting over 30 days", "OVER30_F"), M("with_po", "With a PO number", "PO_F"),
      M("over30_share", "Share over 30 days", agg="ratio", num="over30", den="rows", fmt="pct")],
     None, SRM_AUDIENCE, SRM_CATEGORY, "Daily at 07:00 with the SRM batch (GOLD.V_SRM_PPT_PENDING, latest snapshot).",
     [{"id": "p1", "name": "Overview", "slicers": [{"dim": "division"}, {"dim": "ppt_type"}, {"dim": "step"}],
       "visuals": _kpis(("PPTs pending", "ppts"), ("Avg lot ageing (days)", "lot_age"), ("Waiting over 30 days", "over30"),
                        ("Oldest (days)", "lot_age_max")) + [
           _chart("c1", "bar", "PPTs by pending step", 0, 2, 6, 6, "step", "ppts", "asc", 20, by="step"),
           _chart("c2", "column", "PPTs by lot ageing", 6, 2, 6, 6, "age_band", "ppts", "asc", 10, by="age_band"),
           V("m1", "matrix", "PPTs by division and step", 0, 8, 12, 7, dims=["division", "step"], measures=["ppts"]),
       ]},
      {"id": "p2", "name": "Vendors", "slicers": [{"dim": "division"}, {"dim": "step"}, {"dim": "age_band"}],
       "visuals": [
           _chart("b1", "bar", "Vendors with the most PPTs pending", 0, 0, 5, 10, "vendor", "ppts"),
           _table("t1", "Vendors", 5, 0, 7, 14, ["vendor", "vendor_code"],
                  ["ppts", "over30", "lot_age", "lot_age_max", "process_age", "with_po"], "ppts"),
       ]},
      {"id": "p3", "name": "Categories", "slicers": [{"dim": "division"}, {"dim": "step"}],
       "visuals": [
           _table("t1", "Major categories", 0, 0, 12, 14, ["division", "sub_division", "major_category"],
                  ["ppts", "over30", "over30_share", "lot_age", "lot_age_max", "steps_done"], "ppts"),
       ]},
      {"id": "p4", "name": "PPTs", "slicers": [{"dim": "division"}, {"dim": "vendor"}, {"dim": "step"}, {"dim": "age_band"}],
       "visuals": [
           _table("t1", "Every pending PPT, oldest first", 0, 0, 12, 16,
                  ["ppt", "ppt_type", "vendor", "major_category", "step", "srm_status"],
                  ["lot_age_max", "process_age", "steps_done"], "lot_age_max", limit=2000),
       ]}])

# ---------------------------------------------------------------- Sourcing: Ready for ASN
_add("srm_asn_ready", "srm-asn-ready", "Ready for ASN",
     "POs ready for ASN: how many, how much quantity, how long they have waited, and which vendors, cities and SAP "
     "statuses hold them.",
     "V_BI_SRM_ASN_READY",
     """select r.SNAPSHOT_DATE AS_OF, r.PO_NUMBER, r.PO_TYPE, r.VENDOR_CODE, r.VENDOR_NAME, r.VENDOR_CITY, r.DIVISION,
       r.SUB_DIVISION SUB_DIV, r.MAJOR_CATEGORY, r.TOTAL_QTY, r.ARTICLE_COUNT, coalesce(nullif(r.SAP_STATUS, ''), 'Blank') SAP_STATUS,
       coalesce(nullif(r.PACKING_LIST::string, ''), 'Blank') PACKING_LIST, r.DAYS_READY,
       case r.READY_BUCKET when '0-3 days' then '1 · 0-3 days' when '4-7 days' then '2 · 4-7 days' when '8-15 days' then '3 · 8-15 days'
            when '16+ days' then '4 · 16+ days' else coalesce(r.READY_BUCKET, '9 · Unknown') end READY_BUCKET,
       iff(r.DAYS_READY > 15, 1, 0) OVER15_F, 1 ROW_F
from V2RETAIL.PULSE.V_RPT_SRM_ASN_READY r""",
     [D("as_of", "As of", "AS_OF", "date", _T), D("po", "PO number", "PO_NUMBER", group="PO"), D("po_type", "PO type", "PO_TYPE", group="PO"),
      D("city", "Vendor city", "VENDOR_CITY", group=_V), D("sap_status", "SAP status", "SAP_STATUS", group=_W),
      D("packing_list", "SAP packing list", "PACKING_LIST", group=_W), D("bucket", "Days ready", "READY_BUCKET", group=_W)] + _SRM_DIMS,
     [M("pos", "POs ready", "PO_NUMBER", agg="count_distinct"), M("rows", "Rows", "ROW_F"), M("qty", "Quantity", "TOTAL_QTY"),
      M("articles", "Articles", "ARTICLE_COUNT"), M("days", "Avg days ready", "DAYS_READY", agg="avg"),
      M("days_max", "Longest wait (days)", "DAYS_READY", agg="max"), M("over15", "Waiting over 15 days", "OVER15_F"),
      M("over15_share", "Share over 15 days", agg="ratio", num="over15", den="rows", fmt="pct")],
     None, SRM_AUDIENCE, SRM_CATEGORY, "Daily at 07:00 with the SRM batch (GOLD.V_SRM_ASN_READY, latest snapshot).",
     [{"id": "p1", "name": "Overview", "slicers": [{"dim": "division"}, {"dim": "po_type"}, {"dim": "sap_status"}],
       "visuals": _kpis(("POs ready for ASN", "pos"), ("Quantity", "qty"), ("Waiting over 15 days", "over15"), ("Avg days ready", "days")) + [
           _chart("c1", "column", "POs by days ready", 0, 2, 6, 6, "bucket", "pos", "asc", 10, by="bucket"),
           _chart("c2", "bar", "Quantity by division", 6, 2, 6, 6, "division", "qty", limit=12),
           V("m1", "matrix", "POs by SAP status and days ready", 0, 8, 12, 6, dims=["sap_status", "bucket"], measures=["pos"]),
       ]},
      {"id": "p2", "name": "Vendors", "slicers": [{"dim": "division"}, {"dim": "city"}, {"dim": "bucket"}],
       "visuals": [
           _chart("b1", "bar", "Vendors with the most quantity waiting", 0, 0, 5, 10, "vendor", "qty"),
           _table("t1", "Vendors", 5, 0, 7, 14, ["vendor", "vendor_code", "city"], ["pos", "qty", "articles", "over15", "days", "days_max"], "qty"),
       ]},
      {"id": "p3", "name": "POs", "slicers": [{"dim": "division"}, {"dim": "vendor"}, {"dim": "sap_status"}, {"dim": "bucket"}],
       "visuals": [
           _table("t1", "Every PO ready for ASN, longest waiting first", 0, 0, 12, 16,
                  ["po", "po_type", "vendor", "city", "major_category", "sap_status", "packing_list", "bucket"],
                  ["qty", "articles", "days_max"], "days_max", limit=2000),
       ]}])

# ---------------------------------------------------------------- Sourcing: Fabric Routing Pending
_add("srm_fabric_routing_pending", "srm-fabric-routing-pending", "Fabric Routing Pending",
     "Fabric routing still open: how many designs wait at each step and with whom, how long they have waited, and the "
     "vendors and categories involved.",
     "V_BI_SRM_FABRIC_ROUTING",
     """select r.SNAPSHOT_DATE AS_OF, r.PPT_NO, r.PPT_TYPE, r.VENDOR_CODE, r.VENDOR_NAME, r.CITY, r.DIVISION, r.SUB_DIVISION SUB_DIV,
       r.MAJOR_CATEGORY, r.SSN, r.DESIGN_NO, r.FABRIC, r.PRICE, coalesce(r.ROUTING_STATUS, 'Blank') ROUTING_STATUS,
       coalesce(r.CURRENT_STEP, 'Blank') CURRENT_STEP, coalesce(r.PENDING_WITH, 'Blank') PENDING_WITH,
       coalesce(r.PENDING_PORTAL, 'Blank') PENDING_PORTAL, r.STEPS_DONE, r.TOTAL_STEPS, r.PCT_COMPLETE, r.AGEING_DAYS,
       """ + _AGE.format(c="r.AGEING_DAYS") + """ AGE_BAND, iff(r.AGEING_DAYS > 15, 1, 0) OVER15_F, 1 ROW_F
from V2RETAIL.PULSE.V_RPT_SRM_FABRIC_ROUTING r""",
     [D("as_of", "As of", "AS_OF", "date", _T), D("ppt", "PPT number", "PPT_NO", group="PPT"), D("ppt_type", "PPT type", "PPT_TYPE", group="PPT"),
      D("design", "Design", "DESIGN_NO", group="PPT"), D("fabric", "Fabric", "FABRIC", group="PPT"), D("season", "Season", "SSN", group=_P),
      D("city", "Vendor city", "CITY", group=_V), D("step", "Current step", "CURRENT_STEP", group=_W),
      D("pending_with", "Pending with", "PENDING_WITH", group=_W), D("pending_portal", "Pending portal", "PENDING_PORTAL", group=_W),
      D("routing_status", "Routing status", "ROUTING_STATUS", group=_W), D("age_band", "Ageing band", "AGE_BAND", group=_W)] + _SRM_DIMS,
     [M("lines", "Designs pending", "ROW_F"), M("ppts", "PPTs", "PPT_NO", agg="count_distinct"),
      M("age", "Avg ageing (days)", "AGEING_DAYS", agg="avg"), M("age_max", "Oldest (days)", "AGEING_DAYS", agg="max"),
      M("over15", "Waiting over 15 days", "OVER15_F"), M("price", "Avg price", "PRICE", agg="avg", fmt="inr"),
      M("pct_complete", "Avg % complete", "PCT_COMPLETE", agg="avg"), M("steps_done", "Avg steps done", "STEPS_DONE", agg="avg")],
     None, SRM_AUDIENCE, SRM_CATEGORY, "Daily at 07:00 with the SRM batch (SILVER.SRM_FABRIC_ROUTING_LINE, latest snapshot).",
     [{"id": "p1", "name": "Overview", "slicers": [{"dim": "division"}, {"dim": "ppt_type"}, {"dim": "pending_with"}],
       "visuals": _kpis(("Designs pending", "lines"), ("PPTs", "ppts"), ("Waiting over 15 days", "over15"), ("Avg ageing (days)", "age")) + [
           _chart("c1", "bar", "Designs by who they are pending with", 0, 2, 6, 6, "pending_with", "lines", limit=12),
           _chart("c2", "column", "Designs by ageing", 6, 2, 6, 6, "age_band", "lines", "asc", 10, by="age_band"),
           V("m1", "matrix", "Designs by division and current step", 0, 8, 12, 6, dims=["division", "step"], measures=["lines"]),
       ]},
      {"id": "p2", "name": "Vendors", "slicers": [{"dim": "division"}, {"dim": "pending_with"}],
       "visuals": [
           _chart("b1", "bar", "Vendors with the most designs pending", 0, 0, 5, 10, "vendor", "lines"),
           _table("t1", "Vendors", 5, 0, 7, 14, ["vendor", "vendor_code", "city"], ["lines", "ppts", "over15", "age", "age_max", "price"], "lines"),
       ]},
      {"id": "p3", "name": "Designs", "slicers": [{"dim": "division"}, {"dim": "vendor"}, {"dim": "pending_with"}, {"dim": "age_band"}],
       "visuals": [
           _table("t1", "Every pending design, oldest first", 0, 0, 12, 16,
                  ["ppt", "design", "fabric", "vendor", "major_category", "step", "pending_with"],
                  ["price", "pct_complete", "age_max"], "age_max", limit=2000),
       ]}])

# ---------------------------------------------------------------- Sourcing: Routing Summary
_add("srm_routing_summary", "srm-routing-summary", "Routing Summary",
     "PO routing by stage and delivery month: POs and quantity at each macro and micro stage, how many are over their stage "
     "time and by how many days.",
     "V_BI_SRM_ROUTING_SUMMARY",
     """select r.SNAPSHOT_DATE AS_OF, r.MACRO_SEQ || ' · ' || r.MACRO_ROUTING MACRO, upper(r.MICRO_ROUTING) MICRO,
       coalesce(nullif(r.MTH_KEY, 'N/A'), 'No date') DELIVERY_MONTH, r.NO_OF_PO, r.PO_QTY, r.OVERAGEING_NO_OF_PO, r.OVERAGEING_PO_QTY,
       r.AVG_OVERAGEING_DAYS * r.OVERAGEING_NO_OF_PO OVER_DAYS
from V2RETAIL.PULSE.V_RPT_SRM_ROUTING_SUMMARY r""",
     [D("as_of", "As of", "AS_OF", "date", _T), D("macro", "Macro stage", "MACRO", group=_W), D("micro", "Micro stage", "MICRO", group=_W),
      D("delivery_month", "Delivery month", "DELIVERY_MONTH", group=_T)],
     [M("pos", "POs", "NO_OF_PO"), M("qty", "PO qty", "PO_QTY"), M("pos_over", "POs over stage time", "OVERAGEING_NO_OF_PO"),
      M("qty_over", "Qty over stage time", "OVERAGEING_PO_QTY"), M("over_days", "Days over (total)", "OVER_DAYS"),
      M("avg_over", "Avg days over", agg="ratio", num="over_days", den="pos_over"),
      M("over_share", "Share of POs over time", agg="ratio", num="pos_over", den="pos", fmt="pct")],
     None, SRM_AUDIENCE, SRM_CATEGORY, "Daily at 07:00 with the SRM batch (GOLD.V_SRM_ROUTING_SUMMARY, latest snapshot).",
     [{"id": "p1", "name": "Overview", "slicers": [{"dim": "macro"}, {"dim": "delivery_month"}],
       "visuals": _kpis(("POs in routing", "pos"), ("PO qty", "qty"), ("POs over stage time", "pos_over"), ("Avg days over", "avg_over")) + [
           _chart("c1", "column", "POs by macro stage", 0, 2, 6, 6, "macro", "pos", "asc", 10, by="macro"),
           _chart("c2", "column", "PO qty by delivery month", 6, 2, 6, 6, "delivery_month", "qty", "asc", 24, by="delivery_month"),
           V("m1", "matrix", "POs by macro stage and delivery month", 0, 8, 12, 6, dims=["macro", "delivery_month"], measures=["pos"]),
       ]},
      {"id": "p2", "name": "Stages", "slicers": [{"dim": "macro"}, {"dim": "delivery_month"}],
       "visuals": [
           _chart("b1", "bar", "Micro stages with the most POs over time", 0, 0, 5, 10, "micro", "pos_over"),
           _table("t1", "Macro and micro stages", 5, 0, 7, 14, ["macro", "micro"],
                  ["pos", "qty", "pos_over", "qty_over", "over_share", "avg_over"], "macro", "asc", 200),
       ]}])

# ---------------------------------------------------------------- Sourcing: GRC-PEND PO-wise (T&A)
_add("srm_grc_pend_po_wise", "srm-grc-pend-po-wise", "GRC-PEND PO-wise (T&A)",
     "Open POs not yet fully received: pending quantity and value by T&A status, delivery date and owner, what is overdue, "
     "and the vendors and categories holding the most.",
     "V_BI_SRM_GRC_PEND",
     """select r.SNAPSHOT_DATE AS_OF, r.PO_NUMBER, r.SITE_CD, r.VENDOR_CODE, r.VENDOR_NAME, r.VND_CITY, coalesce(r.ZONE_HEAD, 'Blank') ZONE_HEAD,
       coalesce(r.TA_NAME, 'Blank') TA_NAME, r.SSN, r.SEG, r.DIVISION, r.SUB_DIV, r.MAJOR_CATEGORY, r.PO_TYPE,
       coalesce(r.CRNT_STAT, 'Blank') STATUS, coalesce(r.STAGE_REACHED, 0) STAGE_REACHED,
       case when r.CRNT_DEL_AGING is null then '9 · No date' when r.CRNT_DEL_AGING > 0 then '1 · Overdue'
            when r.CRNT_DEL_AGING > -8 then '2 · Due in 7 days' when r.CRNT_DEL_AGING > -31 then '3 · Due in 8-30 days'
            else '4 · Due later' end DELIVERY_BAND,
       to_char(r.CUR_DELIVERY_DATE, 'YYYY-MM') DELIVERY_MONTH, r.PO_QTY, r.PO_VAL, r.GRC_QTY, r.PEND_PO_QTY, r.PEND_PO_VAL,
       iff(r.CRNT_DEL_AGING > 0, r.PEND_PO_VAL, 0) OVERDUE_V, iff(r.CRNT_DEL_AGING > 0, r.PEND_PO_QTY, 0) OVERDUE_Q,
       iff(r.CRNT_STAT_OVER_AGING > 0, 1, 0) OVER_STAT_F, r.CRNT_STAT_AGEING, r.NO_OF_EXTENSION, 1 ROW_F
from V2RETAIL.PULSE.V_RPT_SRM_GRC_PEND r""",
     [D("as_of", "As of", "AS_OF", "date", _T), D("po", "PO number", "PO_NUMBER", group="PO"), D("po_type", "PO type", "PO_TYPE", group="PO"),
      D("site", "Site", "SITE_CD", group="PO"), D("delivery_month", "Delivery month", "DELIVERY_MONTH", group=_T),
      D("city", "Vendor city", "VND_CITY", group=_V), D("zone_head", "Zone head", "ZONE_HEAD", group="Owner"),
      D("ta_owner", "T&A owner", "TA_NAME", group="Owner"), D("status", "T&A status", "STATUS", group=_W), D("stage", "Stage reached", "STAGE_REACHED", "number", _W),
      D("delivery_band", "Delivery", "DELIVERY_BAND", group=_W), D("season", "Season", "SSN", group=_P),
      D("segment", "Segment", "SEG", group=_P)] + _SRM_DIMS,
     [M("pos", "Open POs", "PO_NUMBER", agg="count_distinct"), M("rows", "PO lines", "ROW_F"),
      M("po_qty", "PO qty", "PO_QTY"), M("po_val", "PO value", "PO_VAL", fmt="inr"), M("grc_qty", "Received (GRC) qty", "GRC_QTY"),
      M("pend_qty", "Pending qty", "PEND_PO_QTY"), M("pend_val", "Pending value", "PEND_PO_VAL", fmt="inr"),
      M("overdue_val", "Overdue pending value", "OVERDUE_V", fmt="inr"), M("overdue_qty", "Overdue pending qty", "OVERDUE_Q"),
      M("received", "Received share", agg="ratio", num="grc_qty", den="po_qty", fmt="pct"),
      M("overdue_share", "Overdue share of pending value", agg="ratio", num="overdue_val", den="pend_val", fmt="pct"),
      M("over_status", "Lines over status time", "OVER_STAT_F"), M("days_in_status", "Avg days in status", "CRNT_STAT_AGEING", agg="avg"),
      M("extensions", "Avg delivery extensions", "NO_OF_EXTENSION", agg="avg")],
     None, SRM_AUDIENCE, SRM_CATEGORY, "Daily with the T&A snapshot (PULSE.V_TNA_GRC_PEND_PO_WISE, latest snapshot).",
     [{"id": "p1", "name": "Overview", "slicers": [{"dim": "division"}, {"dim": "zone_head"}, {"dim": "po_type"}],
       "visuals": _kpis(("Open POs", "pos"), ("Pending value", "pend_val"), ("Overdue pending value", "overdue_val"),
                        ("Received share", "received")) + [
           _chart("c1", "bar", "Pending value by T&A status", 0, 2, 6, 7, "status", "pend_val", limit=30),
           _chart("c2", "column", "Pending value by delivery", 6, 2, 6, 7, "delivery_band", "pend_val", "asc", 10, by="delivery_band"),
           _table("t1", "Divisions", 0, 9, 12, 7, ["division"],
                  ["pos", "po_val", "pend_qty", "pend_val", "overdue_val", "overdue_share", "received", "over_status"], "pend_val"),
       ]},
      {"id": "p2", "name": "Owners", "slicers": [{"dim": "division"}, {"dim": "zone_head"}, {"dim": "delivery_band"}],
       "visuals": [
           _chart("b1", "bar", "T&A owners by overdue pending value", 0, 0, 5, 10, "ta_owner", "overdue_val"),
           _table("t1", "Zone heads and T&A owners", 5, 0, 7, 14, ["zone_head", "ta_owner"],
                  ["pos", "pend_val", "overdue_val", "overdue_share", "over_status", "days_in_status"], "overdue_val"),
       ]},
      {"id": "p3", "name": "Vendors", "slicers": [{"dim": "division"}, {"dim": "status"}, {"dim": "delivery_band"}],
       "visuals": [
           _chart("b1", "bar", "Vendors by pending value", 0, 0, 5, 10, "vendor", "pend_val"),
           _table("t1", "Vendors", 5, 0, 7, 14, ["vendor", "vendor_code", "city"],
                  ["pos", "pend_qty", "pend_val", "overdue_val", "received", "extensions"], "pend_val"),
       ]},
      {"id": "p4", "name": "PO lines", "slicers": [{"dim": "division"}, {"dim": "vendor"}, {"dim": "ta_owner"}, {"dim": "status"},
                                                   {"dim": "delivery_band"}],
       "visuals": [
           _table("t1", "Open PO lines, biggest pending value first", 0, 0, 12, 16,
                  ["po", "vendor", "ta_owner", "major_category", "status", "delivery_band", "delivery_month"],
                  ["po_qty", "grc_qty", "pend_qty", "pend_val", "days_in_status"], "pend_val", limit=2000),
       ]}])


def snowflake_sql():
    out = ["-- V2Pulse: dataset views behind the B&M report dashboards. Generated from pulse/modules/bm_dashboards.py.",
           "use role ACCOUNTADMIN;", "use warehouse COMPUTE_WH;", ""]
    for d in DATASETS:
        out += ["create or replace view %s.%s\n  comment = 'V2Pulse dataset: %s'\nas %s;" % (_PULSE, d["view"], d["name"], d["sql"]),
                "grant select on view %s.%s to role PULSE_SVC_ROLE;" % (_PULSE, d["view"]), ""]
    return "\n".join(out)


def seed_links(cur, rid):
    """File a link to each dashboard right after its report, in every tree folder the report sits in, once. rid(r)
    gives a dashboard's fixed id. A link already filed anywhere in that tree (moved or renamed by an admin) is left alone.
    The link takes the report's sort_order: items sort by (sort_order, created_at), so it lands just after the report
    without renumbering anything an admin arranged."""
    from . import bm_reports
    trees = {r["code"]: r.get("tree", bm_reports.TREE) for r in bm_reports.REPORTS}
    for r in REPORTS:
        code, tree = "dash-" + r["report_code"], trees[r["report_code"]]
        cur.execute("insert into rpt.report (code, name, description, layer, owner_dept, owner_name, kind, url, tags)"
                    " values (%s,%s,%s,'department',%s,'Data Platform','link',%s,%s) on conflict (code) do update set url = excluded.url",
                    (code, r["name"], r["description"], tree.split(":", 1)[1], "/reports/dynamic/%s" % rid(r), ["B&M", "dashboard"]))
        cur.execute("select id from rpt.report where code = %s", (code,))
        did = cur.fetchone()["id"]
        cur.execute("select 1 from nav.item i join nav.folder f on f.id = i.folder_id where f.tree = %s and f.section = 'reports'"
                    " and i.report_id = %s", (tree, did))
        if cur.fetchone():
            continue
        cur.execute("select i.folder_id, i.sort_order from nav.item i join nav.folder f on f.id = i.folder_id join rpt.report p"
                    " on p.id = i.report_id where f.tree = %s and f.section = 'reports' and not f.is_archived and p.code = %s",
                    (tree, r["report_code"]))
        for row in cur.fetchall():
            cur.execute("insert into nav.item (id, folder_id, kind, report_id, sort_order) values (%s,%s,'report',%s,%s)",
                        (uuid.uuid4(), row["folder_id"], did, row["sort_order"]))
