"""Starting datasets for dynamic reports, and the official report seeded on each. The same definitions
generate deploy/snowflake_bi.sql (Part D), so the view contract and the dataset cannot drift apart.
After the first seed, datasets and library reports belong to the admins; a deploy only adds missing ones."""

PULSE = "V2RETAIL.PULSE"


def D(key, label, col, typ="text", group="General", grain=None):
    d = {"key": key, "label": label, "col": col, "type": typ, "group": group}
    if grain:
        d["grain"] = grain
    return d


def M(key, label, col=None, agg="sum", fmt="num", time_agg="last", **kw):
    """time_agg: how the measure rolls up over time. 'last' = an as-of figure (MTD, target, rolling averages):
    a month shows its last date's value. 'sum' = a flow (the day's sale): a month adds up its days."""
    m = {"key": key, "label": label, "col": col, "agg": agg, "format": fmt, **kw}
    if agg not in ("ratio", "growth"):
        m["time_agg"] = time_agg
    return m


DATASETS = [
    dict(
        code="sales_budget", name="Sales vs budget",
        description="Daily store × merchandise sales against monthly target and budget, with last-year same-period "
                    "averages. Each date is a snapshot: MTD and rolling figures are as of that date.",
        view="V_BI_SALES_BUDGET",
        sql=("select s.ANCHOR_DT, s.ST_CD, s.ST_NM, s.ST_STATUS, h.ZONE_CODE, h.REGION_CODE, h.CLUSTER_CODE, s.SEG, s.DIV,"
             " s.FLR, s.SUB_DIV, s.MAJ_CAT, s.SSN, s.SALE_TD, s.SALE_MTD, s.MTH_TGT, s.BGT_CURRENT_MONTH,"
             " s.BGT_UPCOMING_MONTH, s.BGT_AVG_L7D, s.BGT_AVG_L4WK, s.SALE_L7D, s.SALE_L4WK, s.AVG_SALE_L7D,"
             " s.AVG_SALE_L4WK, s.AVG_SALE_SSN, s.AVG_SALE_L7D_LYSP, s.AVG_SALE_L4WK_LYSP, s.AVG_SALE_LYSP_SSN, s.BUILT_AT"
             " from V2RETAIL.GOLD.ST_MJ_SALE s"
             " left join V2RETAIL.PULSE.V_STORE_HIERARCHY h on h.STORE_CODE = s.ST_CD"),
        snapshot_dim="date", scope="ST_CD",
        refresh="Rebuilt daily from SAP sales and the merchandise budget (GOLD.ST_MJ_SALE).",
        audience=["EXEC", "MIS_CENTRAL", "PLATFORM", "DEPT_MIS_OWNER", "HO_STAFF", "MERCH", "FINANCE", "ZONAL_HEAD",
                  "REGIONAL_HEAD", "AREA_MGR", "STORE_MGR"],
        dimensions=[
            # Time hierarchy on the snapshot date. Fiscal year runs April-March.
            D("fiscal_year", "Fiscal year", "ANCHOR_DT", "date", "Time", "fy"),
            D("fiscal_quarter", "Fiscal quarter", "ANCHOR_DT", "date", "Time", "fq"),
            D("year", "Year", "ANCHOR_DT", "date", "Time", "year"),
            D("quarter", "Quarter", "ANCHOR_DT", "date", "Time", "quarter"),
            D("month", "Month", "ANCHOR_DT", "date", "Time", "month"),
            D("week", "Week", "ANCHOR_DT", "date", "Time", "week"),
            D("weekday", "Day of week", "ANCHOR_DT", "number", "Time", "dow"),
            D("date", "Date", "ANCHOR_DT", "date", "Time"),
            D("zone", "Zone", "ZONE_CODE", group="Store"), D("region", "Region", "REGION_CODE", group="Store"),
            D("cluster", "Cluster", "CLUSTER_CODE", group="Store"), D("store", "Store code", "ST_CD", group="Store"),
            D("store_name", "Store", "ST_NM", group="Store"), D("store_status", "Store status", "ST_STATUS", group="Store"),
            D("segment", "Segment", "SEG", group="Merchandise"), D("division", "Division", "DIV", group="Merchandise"),
            D("sub_division", "Sub-division", "SUB_DIV", group="Merchandise"),
            D("major_category", "Major category", "MAJ_CAT", group="Merchandise"),
            D("floor", "Floor", "FLR", group="Merchandise"), D("season", "Season", "SSN", group="Merchandise"),
        ],
        measures=[
            M("sale_today", "Sale (day)", "SALE_TD", time_agg="sum"), M("sale_mtd", "Sale MTD", "SALE_MTD"),
            M("month_target", "Month target", "MTH_TGT"), M("budget_month", "Budget (month)", "BGT_CURRENT_MONTH"),
            M("budget_next_month", "Budget (next month)", "BGT_UPCOMING_MONTH"),
            M("sale_l7d", "Sale L7D", "SALE_L7D"), M("sale_l4w", "Sale L4W", "SALE_L4WK"),
            M("avg_l7d", "Avg daily sale L7D", "AVG_SALE_L7D"), M("avg_l4w", "Avg daily sale L4W", "AVG_SALE_L4WK"),
            M("avg_l7d_ly", "Avg daily sale L7D (LY)", "AVG_SALE_L7D_LYSP"),
            M("avg_l4w_ly", "Avg daily sale L4W (LY)", "AVG_SALE_L4WK_LYSP"),
            M("budget_avg_l7d", "Budget avg L7D", "BGT_AVG_L7D"), M("budget_avg_l4w", "Budget avg L4W", "BGT_AVG_L4WK"),
            M("achievement", "MTD vs month target", agg="ratio", num="sale_mtd", den="month_target", fmt="pct"),
            M("l7d_vs_budget", "L7D vs budget", agg="ratio", num="avg_l7d", den="budget_avg_l7d", fmt="pct"),
            M("growth_l7d", "L7D growth vs LY", agg="growth", num="avg_l7d", den="avg_l7d_ly", fmt="pct"),
            M("growth_l4w", "L4W growth vs LY", agg="growth", num="avg_l4w", den="avg_l4w_ly", fmt="pct"),
            M("stores", "Stores", "ST_CD", agg="count_distinct", time_agg="sum"),
        ],
    ),
]


def V(id_, type_, title, x, y, w, h, dims=(), measures=(), **kw):
    return {"id": id_, "type": type_, "title": title, "dims": list(dims), "measures": list(measures),
            "x": x, "y": y, "w": w, "h": h, **kw}


REPORTS = [
    dict(
        name="Sales vs budget — overview", category="Sales", dataset="sales_budget",
        description="Today, month-to-date and target achievement for your stores, by zone, division and major category.",
        definition={
            "version": 1,
            # Operating stores only: OLD and NEW. Upcoming (UPC) and blank-status stores are left out.
            "filters": [{"dim": "store_status", "op": "in", "values": ["OLD", "NEW"]}],
            "pages": [
                {"id": "p1", "name": "Overview",
                 "slicers": [{"dim": "zone"}, {"dim": "region"}, {"dim": "division"}, {"dim": "season"}],
                 "visuals": [
                     V("k1", "kpi", "Sale (day)", 0, 0, 3, 2, measures=["sale_today"]),
                     V("k2", "kpi", "Sale MTD", 3, 0, 3, 2, measures=["sale_mtd"]),
                     V("k3", "kpi", "Month target", 6, 0, 3, 2, measures=["month_target"]),
                     V("k4", "kpi", "MTD vs month target", 9, 0, 3, 2, measures=["achievement"]),
                     V("v1", "column", "Sale MTD by zone", 0, 2, 6, 5, dims=["zone"], measures=["sale_mtd"],
                       sort={"by": "sale_mtd", "dir": "desc"}),
                     V("v2", "bar", "Sale MTD by division", 6, 2, 6, 5, dims=["division"], measures=["sale_mtd"],
                       sort={"by": "sale_mtd", "dir": "desc"}, limit=12),
                     V("v3", "table", "Major categories", 0, 7, 12, 6, dims=["division", "major_category"],
                       measures=["sale_today", "sale_mtd", "month_target", "achievement", "growth_l7d"],
                       sort={"by": "sale_mtd", "dir": "desc"}, limit=50),
                 ]},
                {"id": "p2", "name": "Trend",
                 "slicers": [{"dim": "zone"}, {"dim": "division"}],
                 "visuals": [
                     V("t1", "line", "Daily sale, last 30 days", 0, 0, 12, 6, dims=["date"], measures=["sale_today"],
                       sort={"by": "date", "dir": "asc"}),
                     V("t2", "line", "Average daily sale L7D vs last year", 0, 6, 12, 6, dims=["date"],
                       measures=["avg_l7d", "avg_l7d_ly"], sort={"by": "date", "dir": "asc"}),
                 ]},
                {"id": "p3", "name": "Stores",
                 "slicers": [{"dim": "zone"}, {"dim": "region"}, {"dim": "cluster"}],
                 "visuals": [
                     V("s1", "bar", "Top 15 stores by Sale MTD", 0, 0, 5, 9, dims=["store_name"], measures=["sale_mtd"],
                       sort={"by": "sale_mtd", "dir": "desc"}, limit=15),
                     V("s2", "table", "All stores", 5, 0, 7, 9, dims=["store", "store_name"],
                       measures=["sale_today", "sale_mtd", "month_target", "achievement", "growth_l7d"],
                       sort={"by": "sale_mtd", "dir": "desc"}, limit=1000),
                 ]},
            ],
        },
    ),
]


# ---------------------------------------------------------------- DFS reports (replicated from BRONZE.MIS_REPORT_DEF)
# DFS report definitions are saved pivots over the daily store x article sale fact. We expose that fact as one
# governed dataset (joined to the article master and store dimension for the attributes DFS groups by), and
# generate one Central dynamic report per live DFS definition. The fact is additive, so there is no snapshot
# dimension: a month's figure is the sum of its days, and the time hierarchy gives TD/MTD/QTR/YTD windows.
import json as _json
import pathlib as _pathlib

_T, _S, _MC, _AR = "Time", "Store", "Merchandise", "Article"

DFS_DATASET = dict(
    code="dfs_sale", name="DFS sale & stock (store x article)",
    description="Daily sale, quantity, gross margin and discount by store and article, with full merchandise and "
                "article attributes. The source of the DFS planning reports; figures add up across the period you pick.",
    view="V_BI_DFS_SALE", snapshot_dim=None, scope="ST_CD", cache=1800,
    refresh="Rebuilt daily from the MIS daily sale fact (BRONZE.DAY_WISE_ST_VAR_SALE).",
    audience=["EXEC", "MIS_CENTRAL", "PLATFORM", "DEPT_MIS_OWNER", "HO_STAFF", "MERCH", "SOURCING", "FINANCE",
              "ZONAL_HEAD", "REGIONAL_HEAD", "AREA_MGR", "STORE_MGR"],
    sql=("select f.DT, f.ST_CD, s.STORE_NAME as ST_NM, s.ZONE, s.REGION, s.RDC_CD, s.RDC_NAME as RDC_NM,"
         " f.SEG, a.DIV, a.SUB_DIV, f.MAJ_CAT, a.MC_CODE, a.MC_DESC,"
         " f.GEN_ART_NUMBER, f.GEN_ART_DESC, f.ARTICLE_NUMBER, a.ARTICLE_DESC, f.COLOR, f.SIZE_1, a.MRP, a.RNG_SEG as REG_SEG,"
         " a.VENDOR_CODE, a.VENDOR_NAME, a.MERGE_VENDOR_CODE, a.MERGE_VENDOR_NAME,"
         " a.M_FIT, a.M_FAB_MAIN_MVGR_1, a.M_FAB_MAIN_MVGR_2, a.M_WEAVE_01, a.M_WEAVE_02, a.M_FAB2, a.M_PATTERN,"
         " f.SALE_V, f.SALE_Q, f.GM_V, f.DISCOUNT_V"
         " from V2RETAIL.BRONZE.DAY_WISE_ST_VAR_SALE f"
         " left join V2RETAIL.GOLD.DIM_STORE_V2 s on s.STORE_CODE = f.ST_CD"
         " left join V2RETAIL.GOLD.V_VAR_ARTICLE_MASTER a on a.VAR_ARTICLE = f.ARTICLE_NUMBER"),
    dimensions=[
        D("fiscal_year", "Fiscal year", "DT", "date", _T, "fy"), D("fiscal_quarter", "Fiscal quarter", "DT", "date", _T, "fq"),
        D("year", "Year", "DT", "date", _T, "year"), D("quarter", "Quarter", "DT", "date", _T, "quarter"),
        D("month", "Month", "DT", "date", _T, "month"), D("week", "Week", "DT", "date", _T, "week"),
        D("weekday", "Day of week", "DT", "number", _T, "dow"), D("date", "Date", "DT", "date", _T),
        D("store", "Store code", "ST_CD", "text", _S), D("store_name", "Store", "ST_NM", "text", _S),
        D("zone", "Zone", "ZONE", "text", _S), D("region", "Region", "REGION", "text", _S),
        D("rdc", "RDC code", "RDC_CD", "text", _S), D("rdc_name", "RDC", "RDC_NM", "text", _S),
        D("segment", "Segment", "SEG", "text", _MC), D("division", "Division", "DIV", "text", _MC),
        D("sub_division", "Sub-division", "SUB_DIV", "text", _MC), D("major_category", "Major category", "MAJ_CAT", "text", _MC),
        D("mc_code", "MC code", "MC_CODE", "text", _MC), D("mc_desc", "MC description", "MC_DESC", "text", _MC),
        D("gen_article", "Generic article", "GEN_ART_NUMBER", "number", _AR), D("gen_article_desc", "Generic description", "GEN_ART_DESC", "text", _AR),
        D("article", "Article", "ARTICLE_NUMBER", "number", _AR), D("article_desc", "Article description", "ARTICLE_DESC", "text", _AR),
        D("colour", "Colour", "COLOR", "text", _AR), D("size", "Size", "SIZE_1", "text", _AR),
        D("mrp", "MRP", "MRP", "text", _AR), D("range_segment", "Range segment", "REG_SEG", "text", _AR),
        D("vendor", "Vendor code", "VENDOR_CODE", "text", _AR), D("vendor_name", "Vendor", "VENDOR_NAME", "text", _AR),
        D("merge_vendor", "Merge vendor code", "MERGE_VENDOR_CODE", "text", _AR), D("merge_vendor_name", "Merge vendor", "MERGE_VENDOR_NAME", "text", _AR),
        D("fit", "Fit", "M_FIT", "text", _AR), D("fabric_mvgr1", "Fabric MVGR 1", "M_FAB_MAIN_MVGR_1", "text", _AR),
        D("fabric_mvgr2", "Fabric MVGR 2", "M_FAB_MAIN_MVGR_2", "text", _AR), D("weave1", "Weave 1", "M_WEAVE_01", "text", _AR),
        D("weave2", "Weave 2", "M_WEAVE_02", "text", _AR), D("fabric2", "Fabric 2", "M_FAB2", "text", _AR),
        D("pattern", "Pattern", "M_PATTERN", "text", _AR),
    ],
    measures=[
        M("sale_v", "Sale value", "SALE_V", "sum", "inr"), M("sale_q", "Sale qty", "SALE_Q", "sum", "num"),
        M("gm_v", "Gross margin", "GM_V", "sum", "inr"), M("discount_v", "Discount value", "DISCOUNT_V", "sum", "inr"),
        M("sale_days", "Selling days", "DT", "count_distinct", "num"),
        M("avg_sale_v", "Avg daily sale value", agg="ratio", num="sale_v", den="sale_days", fmt="inr"),
        M("avg_sale_q", "Avg daily sale qty", agg="ratio", num="sale_q", den="sale_days", fmt="num"),
        M("avg_gm_v", "Avg daily gross margin", agg="ratio", num="gm_v", den="sale_days", fmt="inr"),
        M("gm_pct", "GM %", agg="ratio", num="gm_v", den="sale_v", fmt="pct"),
        M("disc_pct", "Discount %", agg="ratio", num="discount_v", den="sale_v", fmt="pct"),
    ],
)
DATASETS.append(DFS_DATASET)

DFS_DIM_MAP = {
    "ST_CD": "store", "ST_NM": "store_name", "ZONE": "zone", "REG": "region", "REGION": "region",
    "RDC_CD": "rdc", "RDC_NM": "rdc_name", "SEG": "segment", "DIV": "division", "SUB_DIV": "sub_division",
    "MAJ_CAT": "major_category", "MC_CODE": "mc_code", "MC_DESC": "mc_desc", "GEN_ART_NUMBER": "gen_article",
    "GEN_ART_DESC": "gen_article_desc", "ARTICLE_NUMBER": "article", "ARTICLE_DESC": "article_desc",
    "NEW_COLOR_CODE": "colour", "COLOR": "colour", "NEW_SIZE_DESC": "size", "SIZE_1": "size", "MRP": "mrp",
    "REG_SEG": "range_segment", "VENDOR_CODE": "vendor", "VENDOR_NAME": "vendor_name",
    "MERGE_VENDOR_CODE": "merge_vendor", "MERGE_VENDOR_NAME": "merge_vendor_name", "M_FIT": "fit",
    "M_FAB_MAIN_MVGR_1": "fabric_mvgr1", "M_FAB_MAIN_MVGR_2": "fabric_mvgr2", "M_WEAVE_01": "weave1",
    "M_WEAVE_02": "weave2", "M_FAB2": "fabric2", "M_PATTERN": "pattern",
}
DFS_MEASURE_MAP = {
    "SALE_V": "sale_v", "SALE_Q": "sale_q", "GM_V": "gm_v", "DISCOUNT_V": "discount_v", "AVG_SALE_V": "avg_sale_v",
    "AVG_SALE_Q": "avg_sale_q", "AVG_GM_V": "avg_gm_v", "SAL_DAYS": "sale_days", "GM_DAYS": "sale_days", "DATA_DAYS": "sale_days",
}
DFS_SLICER_PRIORITY = ["zone", "region", "rdc", "division", "segment", "sub_division", "major_category", "range_segment"]


def _dedupe(seq):
    seen, out = set(), []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def dfs_report_defs():
    """One Central dynamic report per live DFS definition (snapshot in dfs_defs.json), shaped like REPORTS."""
    defs = _json.loads(_pathlib.Path(__file__).with_name("dfs_defs.json").read_text(encoding="utf-8"))
    out = []
    for d in defs:
        dims = _dedupe(DFS_DIM_MAP.get(g) for g in d["group_by"])[:20] or ["store", "major_category"]
        # A definition with no mapped measures gets the default set (gm_pct used to be added first, so the
        # fallback never fired and ST-MJ-VERTICAL showed only GM%).
        meas = _dedupe(DFS_MEASURE_MAP.get(m) for m in d["measures"]) or ["sale_v", "sale_q", "gm_v"]
        if "gm_pct" not in meas:
            meas.append("gm_pct")
        meas = meas[:10]
        slug = d["slug"]
        grain = "RDC" if slug.startswith("rdc") else "DC" if slug.startswith("dc") else "Store"
        # The period is a date-range slicer only: a fixed report filter (last 31 days) was ANDed with the slicer,
        # so any date older than 31 days showed "No data".
        slicers = [{"dim": "date", "mode": "range", "preset": "mtd"}] + \
                  [{"dim": k} for k in DFS_SLICER_PRIORITY if k in dims][:4]
        table = {"id": "t1", "type": "table", "title": d["name"], "dims": dims, "measures": meas,
                 "sort": {"by": meas[0], "dir": "desc"}, "limit": 1000, "x": 0, "y": 0, "w": 12, "h": 14}
        out.append(dict(
            name="DFS · %s" % d["name"], category="DFS — %s" % grain, dataset="dfs_sale",
            description=(d["description"] or "Replicated from the DFS planning report '%s'." % d["name"])
                        + " Opens on this month; pick any dates with the Date slicer.",
            definition={"version": 1, "filters": [],
                        "pages": [{"id": "p1", "name": "Detail", "slicers": slicers, "visuals": [table]}]},
        ))
    return out


# --- RXL report: MIS store performance (added 2026-10-14 by Shubham / Claude). Replaces the v2-insight /mis-report page.
# Snowflake: GOLD.MIS_STORE_REPORT_SNAPSHOT via task GOLD.TASK_MIS_STORE_REPORT_DAILY (05:30 IST).
RXL_REPORT_DATASET = dict(
    code="rxl_report", name="RXL report",
    description="Store achievement against pro-rated budget, PSF, GP PSF, last-year growth and same-store growth. "
                "One frozen copy per day for TD, MTD and YTD (fiscal year April-March). SAP billing, net of returns.",
    view="V_BI_RXL_REPORT",
    sql=("select s.SNAPSHOT_DATE, s.PERIOD_TYPE, s.IS_FINAL, s.STORE_CODE, s.STORE_NAME, coalesce(h.STATE, s.STATE) as STATE,"
         " h.ZONE_CODE, h.REGION_CODE, h.CLUSTER_CODE, h.CLUSTER_HEAD_NAME, s.AREA_SQFT, s.OPENING_DATE,"
         " case when s.BUDGET_PRORATED > 0 then 'BUDGETED' when s.CY_SALES > 0 then 'UNBUDGETED' else 'NO SALES' end as BUDGET_STATUS,"
         " s.CY_SALES, s.CY_GM, s.CY_QTY, s.LY_SALES, s.LY_GM, s.BUDGET_VALUE, coalesce(s.BUDGET_PRORATED, 0) as BUDGET_PRORATED,"
         " iff(s.BUDGET_PRORATED > 0, s.CY_SALES, 0) as BUDGETED_CY_SALES,"
         " iff(s.SSSG_PCT is not null, s.CY_SALES, 0) as SSSG_CY_SALES, iff(s.SSSG_PCT is not null, s.LY_SALES, 0) as SSSG_LY_SALES,"
         " s.CY_PSF, s.CY_GP_PSF, s.BUDGET_PSF, s.PRORATED_ACHIEVEMENT_PCT as ACH_PCT, s.BUDGET_ACHIEVEMENT_PCT as ACH_FULL_PCT,"
         " s.SSSG_PCT, s.GP_GROWTH_PCT, s.MTD_SSSG, s.YTD_SSSG, s.GM_MTD_SSSG, s.GM_YTD_SSSG,"
         " s.M5_PSF, s.M4_PSF, s.M3_PSF, s.M2_PSF, s.M1_PSF, s.CURRENT_PSF, s.DATA_AS_OF, s.SNAPSHOT_TAKEN_AT"
         " from V2RETAIL.GOLD.MIS_STORE_REPORT_SNAPSHOT s"
         " left join V2RETAIL.PULSE.V_STORE_HIERARCHY h on h.STORE_CODE = s.STORE_CODE"),
    snapshot_dim="date", scope="STORE_CODE",
    refresh="Daily 05:30 IST (retry 07:30) after the SAP billing load. The latest day is provisional and is "
            "re-stated as final the next morning (SAP bills day D through D+1).",
    audience=["EXEC", "MIS_CENTRAL", "PLATFORM", "DEPT_MIS_OWNER", "HO_STAFF", "FINANCE", "ZONAL_HEAD",
              "REGIONAL_HEAD", "AREA_MGR", "STORE_MGR"],
    dimensions=[
        D("fiscal_year", "Fiscal year", "SNAPSHOT_DATE", "date", "Time", "fy"),
        D("month", "Month", "SNAPSHOT_DATE", "date", "Time", "month"),
        D("week", "Week", "SNAPSHOT_DATE", "date", "Time", "week"),
        D("date", "Date", "SNAPSHOT_DATE", "date", "Time"),
        D("period", "Period", "PERIOD_TYPE", group="Time"),
        D("is_final", "Final", "IS_FINAL", group="Time"),
        D("zone", "Zone", "ZONE_CODE", group="Store"), D("region", "Region", "REGION_CODE", group="Store"),
        D("cluster", "Cluster", "CLUSTER_CODE", group="Store"),
        D("cluster_head", "Cluster head", "CLUSTER_HEAD_NAME", group="Store"),
        D("state", "State", "STATE", group="Store"),
        D("store", "Store code", "STORE_CODE", group="Store"), D("store_name", "Store", "STORE_NAME", group="Store"),
        D("budget_status", "Budget status", "BUDGET_STATUS", group="Store"),
        D("opening_date", "Opening date", "OPENING_DATE", "date", "Store"),
    ],
    measures=[
        M("budget", "Budget", "BUDGET_VALUE", fmt="inr"),
        M("prorated", "Prorated budget", "BUDGET_PRORATED", fmt="inr"),
        M("actual", "Actual", "CY_SALES", fmt="inr"),
        M("actual_budgeted", "Actual (budgeted stores)", "BUDGETED_CY_SALES", fmt="inr"),
        M("ach", "Ach%", agg="ratio", num="actual_budgeted", den="prorated", fmt="pct"),
        M("area", "Area (sqft)", "AREA_SQFT"),
        M("b_psf", "B.PSF", agg="ratio", num="budget", den="area"),
        M("psf", "PSF", agg="ratio", num="actual", den="area"),
        M("gm", "GM", "CY_GM", fmt="inr"),
        M("gp_psf", "GP PSF", agg="ratio", num="gm", den="area"),
        M("lysp", "LYSP", "LY_SALES", fmt="inr"),
        M("ly_gm", "LY GM", "LY_GM", fmt="inr"),
        M("sales_growth", "S.Gr%", agg="growth", num="actual", den="lysp", fmt="pct"),
        M("gp_growth", "GP Gr%", agg="growth", num="gm", den="ly_gm", fmt="pct"),
        M("sssg_cy", "Same-store actual", "SSSG_CY_SALES", fmt="inr"),
        M("sssg_ly", "Same-store LY", "SSSG_LY_SALES", fmt="inr"),
        M("sssg", "SSSG%", agg="growth", num="sssg_cy", den="sssg_ly", fmt="pct"),
        M("mtd_sssg", "MTD SSSG% (store)", "MTD_SSSG", agg="avg"),
        M("ytd_sssg", "YTD SSSG% (store)", "YTD_SSSG", agg="avg"),
        M("qty", "Qty", "CY_QTY"),
        M("stores", "Stores", "STORE_CODE", agg="count_distinct"),
    ],
)

RXL_REPORT = dict(
    name="RXL report", category="Sales", dataset="rxl_report",
    description="Achievement vs pro-rated budget for your stores: TD, MTD or YTD, with PSF, GP PSF, LY and SSSG. "
                "Replaces the v2-insight MIS page.",
    definition={
        "version": 1,
        # Rows for TD / MTD / YTD sit side by side; one period must always be selected.
        "filters": [{"dim": "period", "op": "in", "values": ["MTD"]}],
        "pages": [
            {"id": "p1", "name": "Overview",
             "slicers": [{"dim": "period"}, {"dim": "zone"}, {"dim": "region"}, {"dim": "state"}],
             "visuals": [
                 V("k1", "kpi", "Prorated budget", 0, 0, 3, 2, measures=["prorated"]),
                 V("k2", "kpi", "Actual", 3, 0, 3, 2, measures=["actual"]),
                 V("k3", "kpi", "Ach%", 6, 0, 3, 2, measures=["ach"]),
                 V("k4", "kpi", "SSSG%", 9, 0, 3, 2, measures=["sssg"]),
                 V("v1", "table", "By state", 0, 2, 12, 6, dims=["state"],
                   measures=["budget", "prorated", "actual", "ach", "psf", "gp_psf", "lysp", "sales_growth", "sssg"],
                   sort={"by": "actual", "dir": "desc"}, limit=50),
                 V("v2", "column", "Ach% by zone", 0, 8, 12, 5, dims=["zone"], measures=["ach"],
                   sort={"by": "ach", "dir": "desc"}),
             ]},
            {"id": "p2", "name": "Stores",
             "slicers": [{"dim": "period"}, {"dim": "zone"}, {"dim": "state"}, {"dim": "budget_status"}],
             "visuals": [
                 V("s1", "table", "Store performance", 0, 0, 12, 12, dims=["store", "store_name", "state"],
                   measures=["budget", "prorated", "actual", "ach", "b_psf", "psf", "gp_psf", "lysp",
                             "sales_growth", "gp_growth", "mtd_sssg", "ytd_sssg"],
                   sort={"by": "actual", "dir": "desc"}, limit=1000),
             ]},
            {"id": "p3", "name": "Trend",
             "slicers": [{"dim": "zone"}, {"dim": "state"}],
             "visuals": [
                 V("t1", "line", "MTD Ach% by day", 0, 0, 12, 6, dims=["date"], measures=["ach"],
                   sort={"by": "date", "dir": "asc"}),
                 V("t2", "line", "MTD actual vs prorated budget", 0, 6, 12, 6, dims=["date"],
                   measures=["actual", "prorated"], sort={"by": "date", "dir": "asc"}),
             ]},
        ],
    },
)

DATASETS.append(RXL_REPORT_DATASET)
REPORTS.append(RXL_REPORT)

def view_sql(d: dict) -> str:
    return ("create or replace view %s.%s\n  comment = 'V2Pulse dataset: %s'\nas %s;"
            % (PULSE, d["view"], d["name"].replace("'", "''"), d["sql"]))


def snowflake_sql() -> str:
    parts = ["-- V2Pulse dynamic reports: PART D. Curated views in V2RETAIL.PULSE behind each dataset.",
             "-- Generated from pulse/modules/bi_catalog.py (python -m pulse.cli bi-sql). Do not edit by hand.",
             "-- Run as ACCOUNTADMIN or DATA_PLATFORM_ADMIN. Safe to re-run.", "",
             "use role ACCOUNTADMIN;", "use warehouse COMPUTE_WH;", ""]
    from .hero_catalog import DATASETS as hero_datasets   # imported here: hero_catalog imports this module
    for d in DATASETS + hero_datasets:
        parts += ["-- %s (%s)" % (d["name"], d["code"]), view_sql(d), ""]
    parts += ["grant select on all views in schema V2RETAIL.PULSE to role PULSE_SVC_ROLE;", ""]
    return "\n".join(parts)
