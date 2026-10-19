"""Analytics module (Hero Articles): the top-selling generic articles across locations, the merchandise hierarchy and time,
with the stock behind them, the gaps, and the signals that move next month's sale.

Two governed datasets feed five library dashboards, each aimed at an audience (roles come from HR department
auto-assignment, so "department" and "role" select the same people). Store scope is applied by the BI engine on
ST_CD, so a store manager sees their store, an area manager their cluster, and HO the company.

Snowflake side (built by SILVER.SP_HERO_BUILD, task SILVER.TASK_HERO_DAILY at 09:30 and 13:30 IST):
  SILVER.HERO_POSITION   as-of snapshot per store x generic article: sales windows, stock along the pipeline,
                         forecast, gap, ranks, gap flags (last 14 days kept)
  SILVER.HERO_SALE_DAILY day x store x generic article sale since 2025-04-01 with the 364-day-earlier twin
The views below are what V2Pulse reads; `python -m pulse.cli bi-sql` regenerates them with the other datasets.

The dashboards are seeded with fixed ids (uuid5 of their name) so the sidebar tab "Analytics" can link straight to
the Analytics dashboard, whose page tabs are the navigation. After the first seed they belong to the report designers; a deploy only adds missing ones."""
import json
import uuid

from .bi_catalog import PULSE, D, M, V

CATEGORY = "Analytics"
_NS = uuid.UUID("6f0f8b0c-3c55-4f3b-9d43-6f1e0a1d2b01")


def report_id(name: str) -> str:
    return str(uuid.uuid5(_NS, name))


def rid(r: dict) -> str:
    """A dashboard's fixed id. id_name keeps the id when a dashboard is renamed."""
    return report_id(r.get("id_name", r["name"]))


# Who sees what. HR auto-assignment maps departments to these roles (Merchandising -> MERCH, Buying -> SOURCING,
# RDC / hub -> WH_OPS, store hierarchy -> ZONAL_HEAD ... STORE_MGR).
ALL_HERO = ["EXEC", "MIS_CENTRAL", "PLATFORM", "DEPT_MIS_OWNER", "HO_STAFF", "MERCH", "SOURCING", "FINANCE",
            "ZONAL_HEAD", "REGIONAL_HEAD", "AREA_MGR", "STORE_MGR", "WH_OPS"]
STORE_OPS = ["EXEC", "MIS_CENTRAL", "PLATFORM", "ZONAL_HEAD", "REGIONAL_HEAD", "AREA_MGR", "STORE_MGR"]
SUPPLY = ["EXEC", "MIS_CENTRAL", "PLATFORM", "DEPT_MIS_OWNER", "MERCH", "SOURCING", "WH_OPS"]

_T, _S, _MC, _AR, _H, _G = "Time", "Store", "Merchandise", "Article", "Hero", "Gap & drivers"

POSITION_SQL = """select p.AS_OF_DT, p.STOCK_DT,
       p.ST_CD, coalesce(s.STORE_NAME, h.STORE_NAME) as ST_NM, coalesce(h.STATE, s.STATE) as STATE, coalesce(h.ZONE_CODE, 'Unmapped') as ZONE_CODE,
       coalesce(h.REGION_CODE, 'Unmapped') as REGION_CODE, coalesce(h.CLUSTER_CODE, 'Unmapped') as CLUSTER_CODE,
       s.OLD_NEW_FLAG as ST_STATUS, coalesce(s.RDC_CD, 'NO RDC') as RDC_CD,
       p.SEG, p.DIV, p.SUB_DIV, p.MAJ_CAT, g.MC_DESC,
       p.GEN_ART_NUMBER, g.GEN_ART_DESC, g.MRP, g.VENDOR_NAME, g.RNG_SEG, g.SSN, g.FIT, g.PATTERN, g.FABRIC,
       p.CO_RANK, p.MC_RANK, p.ST_RANK, p.HERO_TIER, p.MC_HERO, p.ST_TOP100,
       p.GAP_TYPE, p.ACTION,
       case when p.SALE_Q_P7D <= 0 and p.SALE_Q_L7D > 0 then '4 · New this week'
            when p.SALE_Q_P7D <= 0 then '5 · No sale'
            when p.SALE_Q_L7D >= 1.2 * p.SALE_Q_P7D then '1 · Rising (>+20%)'
            when p.SALE_Q_L7D <= 0.8 * p.SALE_Q_P7D then '3 · Falling (<-20%)'
            else '2 · Steady' end as MOMENTUM,
       case when p.DISC_V_L30D <= 0 or p.SALE_V_L30D + p.DISC_V_L30D <= 0 then '0 · No discount'
            when p.DISC_V_L30D / (p.SALE_V_L30D + p.DISC_V_L30D) < 0.10 then '1 · Under 10%'
            when p.DISC_V_L30D / (p.SALE_V_L30D + p.DISC_V_L30D) < 0.20 then '2 · 10-20%'
            else '3 · 20% and more' end as DISCOUNT_BAND,
       case when p.AGE_DAYS is null then '9 · Unknown' when p.AGE_DAYS <= 30 then '1 · 0-30 days'
            when p.AGE_DAYS <= 90 then '2 · 31-90 days' when p.AGE_DAYS <= 180 then '3 · 91-180 days'
            when p.AGE_DAYS <= 365 then '4 · 181-365 days' else '5 · Over a year' end as AGE_BAND,
       case when p.ROS_Q <= 0 then iff(p.FLOOR_Q + p.BACKROOM_Q > 0, '6 · No sale (dead)', '0 · No stock, no sale')
            when (p.FLOOR_Q + p.BACKROOM_Q) / p.ROS_Q < 7 then '1 · Under 7 days'
            when (p.FLOOR_Q + p.BACKROOM_Q) / p.ROS_Q < 15 then '2 · 7-15 days'
            when (p.FLOOR_Q + p.BACKROOM_Q) / p.ROS_Q < 30 then '3 · 15-30 days'
            when (p.FLOOR_Q + p.BACKROOM_Q) / p.ROS_Q < 90 then '4 · 30-90 days'
            else '5 · Over 90 days' end as COVER_BAND,
       case when p.SEASON_FACTOR >= 1.2 then '1 · Season building (>+20%)'
            when p.SEASON_FACTOR <= 0.8 then '3 · Season fading (<-20%)' else '2 · Flat' end as SEASON_OUTLOOK,
       p.SALE_V_L7D, p.SALE_Q_L7D, p.SALE_V_P7D, p.SALE_Q_P7D, p.SALE_V_L30D, p.SALE_Q_L30D, p.GM_V_L30D,
       p.DISC_V_L30D, p.SALE_V_L30D + p.DISC_V_L30D as GROSS_V_L30D, p.MRP_V_L30D, p.SALE_V_MTD,
       p.LY_SALE_V_L30D, p.LY_SALE_Q_L30D,
       p.FLOOR_Q, p.BACKROOM_Q, p.FLOOR_Q + p.BACKROOM_Q as ST_STK_Q, p.PIPELINE_Q, p.INTRANSIT_Q,
       p.PIPELINE_Q + p.INTRANSIT_Q as INBOUND_Q, p.RDC_Q, p.ALL_DC_Q,
       (p.FLOOR_Q + p.BACKROOM_Q) * p.ASP as ST_STK_V, p.SALE_Q_L30D + p.FLOOR_Q + p.BACKROOM_Q as SELL_THRU_DEN,
       p.ROS_Q, p.SEASON_FACTOR, p.FCST_30_Q, p.FCST_30_Q * p.ASP as FCST_30_V, p.AVAIL_Q, p.GAP_Q, p.GAP_V,
       p.LOST_7D_V, p.EXCESS_Q, p.EXCESS_Q * p.ASP as EXCESS_V, p.ASP,
       p.CORE_SIZES, p.CORE_SIZES_STK,
       p.SELLING_F, p.STOCKED_F, p.OOS_F, p.LOW_COVER_F, p.BROKEN_SIZE_F, p.NOT_RANGED_F, p.EXCESS_F,
       iff(p.SELLING_F = 1 or p.NOT_RANGED_F = 1, 1, 0) as SHOULD_STOCK_F,
       iff((p.SELLING_F = 1 or p.NOT_RANGED_F = 1) and p.STOCKED_F = 1, 1, 0) as IN_STOCK_F,
       p.AGE_DAYS, p.FIRST_SALE_DT, p.BUILT_AT
from V2RETAIL.SILVER.HERO_POSITION p
left join V2RETAIL.SILVER.HERO_DIM_GENART g  on g.GEN_ART_NUMBER = p.GEN_ART_NUMBER
left join V2RETAIL.PULSE.V_STORE_HIERARCHY h on h.STORE_CODE = p.ST_CD
left join V2RETAIL.GOLD.DIM_STORE_V2 s       on s.STORE_CODE = p.ST_CD"""

SALES_SQL = """select f.DT, f.ST_CD, coalesce(s.STORE_NAME, h.STORE_NAME) as ST_NM, coalesce(h.STATE, s.STATE) as STATE,
       coalesce(h.ZONE_CODE, 'Unmapped') as ZONE_CODE, coalesce(h.REGION_CODE, 'Unmapped') as REGION_CODE,
       coalesce(h.CLUSTER_CODE, 'Unmapped') as CLUSTER_CODE, s.OLD_NEW_FLAG as ST_STATUS,
       coalesce(s.RDC_CD, 'NO RDC') as RDC_CD,
       f.SEG, f.DIV, f.SUB_DIV, f.MAJ_CAT, g.MC_DESC,
       f.GEN_ART_NUMBER, g.GEN_ART_DESC, g.MRP, g.VENDOR_NAME, g.RNG_SEG, g.SSN, g.FIT, g.PATTERN, g.FABRIC,
       coalesce(t.HERO_TIER, iff(f.SEG in ('MKT', 'NT', 'ACC', 'FAB') or f.DIV = 'SCRAP', 'X · Non-merchandise', 'D · Rest'))
         as HERO_TIER,
       coalesce(t.MC_HERO, 'Other') as MC_HERO, t.CO_RANK,
       f.SALE_V, f.SALE_Q, f.GM_V, f.DISC_V, coalesce(f.SALE_V, 0) + coalesce(f.DISC_V, 0) as GROSS_V,
       iff(f.GM_V is not null, f.SALE_V, null) as GM_BASE_V, f.SALE_Q * g.MRP as MRP_V,
       f.LY_SALE_V, f.LY_SALE_Q, f.LY_GM_V,
       iff(f.SALE_Q > 0, f.ST_CD, null) as SELLING_ST, iff(f.SALE_Q > 0, f.DT, null) as SELLING_DT, f.SRC
from V2RETAIL.SILVER.HERO_SALE_DAILY f
left join V2RETAIL.SILVER.HERO_DIM_GENART g   on g.GEN_ART_NUMBER = f.GEN_ART_NUMBER
left join V2RETAIL.SILVER.HERO_TIER_CURRENT t on t.GEN_ART_NUMBER = f.GEN_ART_NUMBER
left join V2RETAIL.PULSE.V_STORE_HIERARCHY h  on h.STORE_CODE = f.ST_CD
left join V2RETAIL.GOLD.DIM_STORE_V2 s        on s.STORE_CODE = f.ST_CD"""


def _store_dims():
    return [D("zone", "Zone", "ZONE_CODE", group=_S), D("region", "Region", "REGION_CODE", group=_S),
            D("cluster", "Cluster", "CLUSTER_CODE", group=_S), D("state", "State", "STATE", group=_S),
            D("store", "Store code", "ST_CD", group=_S), D("store_name", "Store", "ST_NM", group=_S),
            D("store_status", "Store status (OLD / NEW)", "ST_STATUS", group=_S), D("rdc", "RDC", "RDC_CD", group=_S)]


def _product_dims():
    return [D("segment", "Segment", "SEG", group=_MC), D("division", "Division", "DIV", group=_MC),
            D("sub_division", "Sub-division", "SUB_DIV", group=_MC),
            D("major_category", "Major category", "MAJ_CAT", group=_MC), D("mc_desc", "MC description", "MC_DESC", group=_MC),
            D("gen_article", "Generic article", "GEN_ART_NUMBER", "number", _AR),
            D("gen_article_desc", "Article description", "GEN_ART_DESC", group=_AR),
            D("mrp", "MRP", "MRP", "number", _AR), D("vendor", "Vendor", "VENDOR_NAME", group=_AR),
            D("range_segment", "Range segment", "RNG_SEG", group=_AR), D("season", "Season", "SSN", group=_AR),
            D("fit", "Fit", "FIT", group=_AR), D("pattern", "Pattern", "PATTERN", group=_AR),
            D("fabric", "Fabric", "FABRIC", group=_AR)]


POSITION_DATASET = dict(
    code="hero_position", name="Hero articles - position, stock & gaps",
    description="One row per store x generic article for each day: sale over the last 7 / prior 7 / 30 days and MTD, "
                "last year's same 30 days, stock on the floor, in the backroom, picked for the store at the RDC or hub, "
                "on the road, and at the RDCs; a 30-day forecast (rate of sale x last year's seasonal shape), the gap "
                "it leaves, company / category / store ranks and gap flags. The latest day is shown unless you pick one.",
    view="V_BI_HERO_POSITION", sql=POSITION_SQL, snapshot_dim="as_of", scope="ST_CD", cache=3600, audience=ALL_HERO,
    refresh="Daily 09:30 IST (repeat 13:30) from the MIS daily sale, ET_STOCK_DATA, PRD / PTL / hub-PRD stock and hub "
            "in-transit (SILVER.SP_HERO_BUILD). Stock is as of the previous day's close.",
    dimensions=[D("as_of", "As of", "AS_OF_DT", "date", _T)] + _store_dims() + _product_dims() + [
        D("hero_tier", "Hero tier (company)", "HERO_TIER", group=_H),
        D("category_hero", "Category hero", "MC_HERO", group=_H),
        D("store_top100", "In the store's top 100", "ST_TOP100", group=_H),
        D("co_rank", "Company rank", "CO_RANK", "number", _H),
        D("store_rank", "Rank in store", "ST_RANK", "number", _H),
        D("gap_type", "Gap type", "GAP_TYPE", group=_G), D("action", "Suggested action", "ACTION", group=_G),
        D("momentum", "Momentum (L7D vs prior 7D)", "MOMENTUM", group=_G),
        D("discount_band", "Discount band", "DISCOUNT_BAND", group=_G), D("age_band", "Article age", "AGE_BAND", group=_G),
        D("cover_band", "Stock cover", "COVER_BAND", group=_G), D("season_outlook", "Season outlook", "SEASON_OUTLOOK", group=_G),
    ],
    measures=[
        M("sale_l30", "Sale L30D", "SALE_V_L30D", fmt="inr"), M("qty_l30", "Qty L30D", "SALE_Q_L30D"),
        M("sale_l7d", "Sale L7D", "SALE_V_L7D", fmt="inr"), M("qty_l7d", "Qty L7D", "SALE_Q_L7D"),
        M("sale_p7d", "Sale prior 7D", "SALE_V_P7D", fmt="inr"), M("qty_p7d", "Qty prior 7D", "SALE_Q_P7D"),
        M("sale_mtd", "Sale MTD", "SALE_V_MTD", fmt="inr"),
        # LY here is the same store x article a year earlier: like-for-like per article, but it leaves out last
        # year's articles that no longer sell, so for totals use the Sales over time dataset.
        M("ly_sale_l30", "LY sale, same article (30 days)", "LY_SALE_V_L30D", fmt="inr"),
        M("gm_l30", "GM L30D", "GM_V_L30D", fmt="inr"), M("disc_l30", "Discount L30D", "DISC_V_L30D", fmt="inr"),
        M("gross_l30", "Gross sale L30D (before discount)", "GROSS_V_L30D", fmt="inr"),
        M("mrp_l30", "Sale at MRP L30D", "MRP_V_L30D", fmt="inr"),
        M("floor_q", "Floor stock", "FLOOR_Q"), M("backroom_q", "Backroom stock", "BACKROOM_Q"),
        M("store_stock", "Store stock", "ST_STK_Q"), M("store_stock_v", "Store stock value", "ST_STK_V", fmt="inr"),
        M("pipeline_q", "Picked for store (PRD / PTL)", "PIPELINE_Q"), M("intransit_q", "On the road", "INTRANSIT_Q"),
        M("inbound", "Inbound (picked + on the road)", "INBOUND_Q"),
        # DC stock is an article-level figure repeated on every store row: max, so it is right per article.
        M("rdc_stock", "Own-RDC stock", "RDC_Q", agg="max"), M("dc_stock", "DC stock (all RDCs)", "ALL_DC_Q", agg="max"),
        M("ros", "Rate of sale / day", "ROS_Q"), M("fcst_30", "Forecast next 30 days", "FCST_30_Q"),
        M("fcst_30_v", "Forecast next 30 days (value)", "FCST_30_V", fmt="inr"),
        M("avail", "Available (store + inbound)", "AVAIL_Q"),
        M("gap_q", "Gap to forecast (units)", "GAP_Q"), M("gap_v", "Gap to forecast (value)", "GAP_V", fmt="inr"),
        M("lost_7d", "Sale at risk, 7 days (out of stock)", "LOST_7D_V", fmt="inr"),
        M("excess_q", "Excess over 90 days cover", "EXCESS_Q"), M("excess_v", "Excess value", "EXCESS_V", fmt="inr"),
        M("core_sizes", "Core sizes", "CORE_SIZES"), M("core_sizes_stk", "Core sizes in stock", "CORE_SIZES_STK"),
        M("stores_selling", "Stores selling", "SELLING_F"), M("stores_stocked", "Stores stocked", "STOCKED_F"),
        M("should_stock", "Stores that should stock", "SHOULD_STOCK_F"), M("in_stock", "Stores in stock", "IN_STOCK_F"),
        M("oos_stores", "Out of stock (stores)", "OOS_F"), M("low_cover_stores", "Low cover <7d (stores)", "LOW_COVER_F"),
        M("broken_size_stores", "Broken sizes (stores)", "BROKEN_SIZE_F"),
        M("not_ranged_stores", "Hero not ranged (stores)", "NOT_RANGED_F"), M("excess_stores", "Excess (stores)", "EXCESS_F"),
        M("pairs", "Store x article rows", "ST_CD", agg="count"),
        M("articles", "Articles", "GEN_ART_NUMBER", agg="count_distinct"), M("stores", "Stores", "ST_CD", agg="count_distinct"),
        M("best_rank", "Company rank", "CO_RANK", agg="min"), M("age", "Article age (days)", "AGE_DAYS", agg="max"),
        M("season_factor", "Season factor (avg)", "SEASON_FACTOR", agg="avg"), M("sell_thru_den", "Sale + stock", "SELL_THRU_DEN"),
        M("growth_wow", "Growth vs prior week", agg="growth", num="qty_l7d", den="qty_p7d", fmt="pct"),
        M("growth_ly", "Growth vs LY (same article)", agg="growth", num="sale_l30", den="ly_sale_l30", fmt="pct"),
        M("gm_pct", "GM %", agg="ratio", num="gm_l30", den="sale_l30", fmt="pct"),
        M("disc_pct", "Discount %", agg="ratio", num="disc_l30", den="gross_l30", fmt="pct"),
        M("realisation", "Price realisation (vs MRP)", agg="ratio", num="sale_l30", den="mrp_l30", fmt="pct"),
        M("asp", "Average selling price", agg="ratio", num="sale_l30", den="qty_l30", fmt="inr"),
        M("cover_days", "Cover (days)", agg="ratio", num="store_stock", den="ros"),
        M("sell_through", "Sell-through L30D", agg="ratio", num="qty_l30", den="sell_thru_den", fmt="pct"),
        M("availability", "Availability %", agg="ratio", num="in_stock", den="should_stock", fmt="pct"),
        M("size_health", "Core sizes in stock %", agg="ratio", num="core_sizes_stk", den="core_sizes", fmt="pct"),
        M("dc_cover_gap", "DC stock / gap", agg="ratio", num="dc_stock", den="gap_q", fmt="pct"),
    ],
)

SALES_DATASET = dict(
    code="hero_sales", name="Hero articles - sales over time",
    description="Daily sale by store and generic article since April 2025, with the same store and article 364 days "
                "earlier (weekday aligned) alongside, and today's hero tier on every row. Figures add up over the dates "
                "you pick, so any period of the time hierarchy can be ranked.",
    view="V_BI_HERO_SALES", sql=SALES_SQL, snapshot_dim=None, scope="ST_CD", cache=3600, audience=ALL_HERO,
    refresh="Daily 09:30 IST (repeat 13:30): the last 4 days are re-stated. Apr-Jun 2025 and Oct 2025-Jan 2026 come "
            "from GOLD.FACT_STORE_STOCK_SALE_DAILY (no GM or discount, ~4% above the MIS feed); LY before 1 Apr 2025 "
            "does not exist.",
    dimensions=[D("fiscal_year", "Fiscal year", "DT", "date", _T, "fy"),
                D("fiscal_quarter", "Fiscal quarter", "DT", "date", _T, "fq"),
                D("month", "Month", "DT", "date", _T, "month"), D("week", "Week", "DT", "date", _T, "week"),
                D("weekday", "Day of week", "DT", "number", _T, "dow"), D("date", "Date", "DT", "date", _T)]
               + _store_dims() + _product_dims() + [
        D("hero_tier", "Hero tier (today)", "HERO_TIER", group=_H), D("category_hero", "Category hero (today)", "MC_HERO", group=_H),
        D("source", "Sale feed", "SRC", group=_T)],
    measures=[
        M("sale_v", "Sale value", "SALE_V", fmt="inr", time_agg="sum"), M("sale_q", "Sale qty", "SALE_Q", time_agg="sum"),
        M("gm_v", "Gross margin", "GM_V", fmt="inr", time_agg="sum"), M("disc_v", "Discount", "DISC_V", fmt="inr", time_agg="sum"),
        M("gross_v", "Gross sale (before discount)", "GROSS_V", fmt="inr", time_agg="sum"),
        M("gm_base_v", "Sale with GM known", "GM_BASE_V", fmt="inr", time_agg="sum"),
        M("mrp_v", "Sale at MRP", "MRP_V", fmt="inr", time_agg="sum"),
        M("ly_sale_v", "LY sale", "LY_SALE_V", fmt="inr", time_agg="sum"), M("ly_sale_q", "LY qty", "LY_SALE_Q", time_agg="sum"),
        M("stores_selling", "Stores selling", "SELLING_ST", agg="count_distinct", time_agg="sum"),
        M("selling_days", "Selling days", "SELLING_DT", agg="count_distinct", time_agg="sum"),
        M("articles", "Articles", "GEN_ART_NUMBER", agg="count_distinct", time_agg="sum"),
        M("growth_ly", "Growth vs LY", agg="growth", num="sale_v", den="ly_sale_v", fmt="pct"),
        M("gm_pct", "GM %", agg="ratio", num="gm_v", den="gm_base_v", fmt="pct"),
        M("disc_pct", "Discount %", agg="ratio", num="disc_v", den="gross_v", fmt="pct"),
        M("asp", "Average selling price", agg="ratio", num="sale_v", den="sale_q", fmt="inr"),
        M("realisation", "Price realisation (vs MRP)", agg="ratio", num="sale_v", den="mrp_v", fmt="pct"),
        M("avg_daily", "Avg daily sale", agg="ratio", num="sale_v", den="selling_days", fmt="inr"),
        M("sale_per_store", "Sale per selling store", agg="ratio", num="sale_v", den="stores_selling", fmt="inr"),
    ],
)

DATASETS = [POSITION_DATASET, SALES_DATASET]

TOP100 = [{"dim": "hero_tier", "op": "in", "values": ["A · Top 100"]}]
TOP500 = [{"dim": "hero_tier", "op": "in", "values": ["A · Top 100", "B · 101-500"]}]
NON_MERCH = [{"dim": "hero_tier", "op": "not_in", "values": ["X · Non-merchandise"]}]
ACTIONABLE = [{"dim": "gap_type", "op": "in",
               "values": ["1 · Out of stock", "2 · Hero not ranged", "3 · Low cover (<7 days)", "4 · Broken sizes"]}]

_HERO_LIST = ["best_rank", "sale_l30", "qty_l30", "growth_wow", "stores_selling", "availability", "oos_stores",
              "low_cover_stores", "not_ranged_stores", "store_stock", "inbound", "dc_stock", "fcst_30", "gap_q",
              "cover_days", "disc_pct", "gm_pct", "size_health"]

COMMAND_CENTRE = dict(
    name="Hero articles · Top 100 command centre", category=CATEGORY, dataset="hero_position", audience=ALL_HERO,
    description="The company's 100 best-selling generic articles (last 30 days), how many of your stores carry each, "
                "where they are out of stock, what is on the way and in the RDCs, and the 30-day gap. Sister "
                "dashboards in Analytics: Explorer, Store action list, Replenishment & buying, Sales over time.",
    definition={"version": 1, "filters": TOP100, "pages": [
        {"id": "p1", "name": "Overview",
         "slicers": [{"dim": "zone"}, {"dim": "region"}, {"dim": "division"}, {"dim": "major_category"}],
         "visuals": [
             V("k1", "kpi", "Hero sale L30D", 0, 0, 2, 2, measures=["sale_l30"]),
             V("k2", "kpi", "Qty vs prior week", 2, 0, 2, 2, measures=["growth_wow"]),
             V("k3", "kpi", "Availability %", 4, 0, 2, 2, measures=["availability"]),
             V("k4", "kpi", "Out of stock (store x hero)", 6, 0, 2, 2, measures=["oos_stores"]),
             V("k5", "kpi", "Sale at risk, next 7 days", 8, 0, 2, 2, measures=["lost_7d"]),
             V("k6", "kpi", "30-day gap (value)", 10, 0, 2, 2, measures=["gap_v"]),
             V("k7", "kpi", "Store stock (units)", 0, 2, 2, 2, measures=["store_stock"]),
             V("k8", "kpi", "Inbound (units)", 2, 2, 2, 2, measures=["inbound"]),
             V("k9", "kpi", "Forecast next 30 days", 4, 2, 2, 2, measures=["fcst_30"]),
             V("k10", "kpi", "Cover (days)", 6, 2, 2, 2, measures=["cover_days"]),
             V("k11", "kpi", "GM %", 8, 2, 2, 2, measures=["gm_pct"]),
             V("k12", "kpi", "Discount %", 10, 2, 2, 2, measures=["disc_pct"]),
             V("v1", "bar", "Hero sale L30D by division", 0, 4, 6, 6, dims=["division"], measures=["sale_l30"],
               sort={"by": "sale_l30", "dir": "desc"}, limit=15),
             V("v2", "donut", "Store x hero rows by gap type", 6, 4, 6, 6, dims=["gap_type"], measures=["pairs"],
               sort={"by": "gap_type", "dir": "asc"}),
             V("v3", "column", "Availability % by zone", 0, 10, 6, 5, dims=["zone"], measures=["availability"],
               sort={"by": "zone", "dir": "asc"}),
             V("v4", "column", "Sale at risk (7 days) by zone", 6, 10, 6, 5, dims=["zone"], measures=["lost_7d"],
               sort={"by": "lost_7d", "dir": "desc"}),
         ]},
        {"id": "p2", "name": "Top 100 list",
         "slicers": [{"dim": "zone"}, {"dim": "region"}, {"dim": "cluster"}, {"dim": "division"}],
         "visuals": [
             V("t1", "table", "The top 100 hero articles", 0, 0, 12, 14,
               dims=["gen_article", "gen_article_desc", "major_category", "mrp"], measures=_HERO_LIST,
               sort={"by": "sale_l30", "dir": "desc"}, limit=100),
         ]},
        {"id": "p3", "name": "Gaps by location",
         "slicers": [{"dim": "zone"}, {"dim": "region"}, {"dim": "division"}, {"dim": "gap_type"}],
         "visuals": [
             V("m1", "matrix", "Store x hero rows: zone by gap type", 0, 0, 12, 5, dims=["zone", "gap_type"],
               measures=["pairs"]),
             V("t1", "table", "Regions", 0, 5, 12, 6, dims=["zone", "region"],
               measures=["sale_l30", "availability", "oos_stores", "low_cover_stores", "not_ranged_stores", "lost_7d",
                         "gap_v", "store_stock", "inbound"], sort={"by": "lost_7d", "dir": "desc"}, limit=200),
             V("b1", "bar", "Top 15 stores by sale at risk", 0, 11, 5, 9, dims=["store_name"], measures=["lost_7d"],
               sort={"by": "lost_7d", "dir": "desc"}, limit=15),
             V("t2", "table", "Stores", 5, 11, 7, 9, dims=["store", "store_name", "cluster"],
               measures=["sale_l30", "availability", "oos_stores", "low_cover_stores", "not_ranged_stores",
                         "broken_size_stores", "lost_7d", "gap_v"], sort={"by": "lost_7d", "dir": "desc"}, limit=1000),
         ]},
        {"id": "p4", "name": "Gaps by product",
         "slicers": [{"dim": "zone"}, {"dim": "division"}, {"dim": "sub_division"}],
         "visuals": [
             V("b1", "bar", "30-day gap (value) by major category", 0, 0, 6, 7, dims=["major_category"],
               measures=["gap_v"], sort={"by": "gap_v", "dir": "desc"}, limit=20),
             V("b2", "bar", "Out of stock (store x hero) by major category", 6, 0, 6, 7, dims=["major_category"],
               measures=["oos_stores"], sort={"by": "oos_stores", "dir": "desc"}, limit=20),
             V("t1", "table", "Major categories", 0, 7, 12, 8, dims=["division", "sub_division", "major_category"],
               measures=["articles", "sale_l30", "growth_wow", "availability", "oos_stores",
                         "not_ranged_stores", "broken_size_stores", "store_stock", "inbound", "gap_q", "gap_v"],
               sort={"by": "sale_l30", "dir": "desc"}, limit=500),
         ]},
        {"id": "p5", "name": "What drives next month",
         "slicers": [{"dim": "zone"}, {"dim": "division"}, {"dim": "major_category"}],
         "visuals": [
             V("c1", "column", "Sale L30D by momentum", 0, 0, 4, 5, dims=["momentum"], measures=["sale_l30"],
               sort={"by": "momentum", "dir": "asc"}),
             V("c2", "column", "Sale L30D by discount band", 4, 0, 4, 5, dims=["discount_band"], measures=["sale_l30"],
               sort={"by": "discount_band", "dir": "asc"}),
             V("c3", "column", "Sale L30D by article age", 8, 0, 4, 5, dims=["age_band"], measures=["sale_l30"],
               sort={"by": "age_band", "dir": "asc"}),
             V("c4", "column", "Forecast vs available by season outlook", 0, 5, 6, 5, dims=["season_outlook"],
               measures=["fcst_30", "avail"], sort={"by": "season_outlook", "dir": "asc"}),
             V("c5", "column", "Store x hero rows by stock cover", 6, 5, 6, 5, dims=["cover_band"], measures=["pairs"],
               sort={"by": "cover_band", "dir": "asc"}),
             V("t1", "table", "Drivers per hero", 0, 10, 12, 10, dims=["gen_article", "gen_article_desc", "major_category"],
               measures=["sale_l30", "growth_wow", "growth_ly", "season_factor", "fcst_30", "avail", "gap_q",
                         "disc_pct", "realisation", "gm_pct", "asp", "age", "size_health", "availability",
                         "sell_through"], sort={"by": "sale_l30", "dir": "desc"}, limit=100),
         ]},
    ]},
)

EXPLORER = dict(
    name="Hero articles · Explorer", category=CATEGORY, dataset="hero_position", audience=ALL_HERO,
    description="Rank any slice: pick a zone, region, store, division or category and the tables show the top 100 "
                "articles of that selection (not just the company top 100), with their stock and gaps.",
    definition={"version": 1, "filters": NON_MERCH, "pages": [
        {"id": "p1", "name": "Top 100 in your selection",
         "slicers": [{"dim": "hero_tier"}, {"dim": "zone"}, {"dim": "region"}, {"dim": "division"},
                     {"dim": "major_category"}],
         "visuals": [
             V("k1", "kpi", "Sale L30D", 0, 0, 3, 2, measures=["sale_l30"]),
             V("k2", "kpi", "Qty vs prior week", 3, 0, 3, 2, measures=["growth_wow"]),
             V("k3", "kpi", "Availability %", 6, 0, 3, 2, measures=["availability"]),
             V("k4", "kpi", "30-day gap (value)", 9, 0, 3, 2, measures=["gap_v"]),
             V("t1", "table", "Top 100 articles in this selection", 0, 2, 12, 14,
               dims=["gen_article", "gen_article_desc", "major_category", "hero_tier"],
               measures=["best_rank", "sale_l30", "qty_l30", "growth_wow", "growth_ly", "stores_selling", "availability",
                         "oos_stores", "store_stock", "inbound", "dc_stock", "fcst_30", "gap_q", "cover_days",
                         "disc_pct", "gm_pct"], sort={"by": "sale_l30", "dir": "desc"}, limit=100),
         ]},
        {"id": "p2", "name": "Category heroes",
         "slicers": [{"dim": "category_hero"}, {"dim": "division"}, {"dim": "major_category"}, {"dim": "zone"}],
         "visuals": [
             V("t1", "table", "Heroes by major category (pick 'Top 10 in category' above)", 0, 0, 12, 14,
               dims=["division", "major_category", "gen_article", "gen_article_desc", "category_hero"],
               measures=["sale_l30", "qty_l30", "growth_wow", "availability", "oos_stores", "not_ranged_stores",
                         "store_stock", "dc_stock", "gap_q"], sort={"by": "sale_l30", "dir": "desc"}, limit=2000),
         ]},
        {"id": "p3", "name": "Store heroes",
         "slicers": [{"dim": "store"}, {"dim": "store_top100"}, {"dim": "division"}, {"dim": "gap_type"}],
         "visuals": [
             V("t1", "table", "Each store's best sellers (pick a store, or 'Y' for its top 100)", 0, 0, 12, 14,
               dims=["store", "store_name", "store_rank", "gen_article", "gen_article_desc", "major_category", "gap_type"],
               measures=["sale_l30", "qty_l30", "store_stock", "inbound", "rdc_stock", "fcst_30", "gap_q", "cover_days"],
               sort={"by": "sale_l30", "dir": "desc"}, limit=2000),
         ]},
    ]},
)

STORE_ACTIONS = dict(
    name="Hero articles · Store action list", category=CATEGORY, dataset="hero_position", audience=STORE_OPS,
    description="What to do in each store today: heroes out of stock, not ranged, on low cover or with broken sizes, "
                "with the suggested action (expedite, replenish from RDC, allocate, fill sizes) and the sale at risk.",
    definition={"version": 1, "filters": ACTIONABLE + NON_MERCH, "pages": [
        {"id": "p1", "name": "Actions",
         "slicers": [{"dim": "store"}, {"dim": "gap_type"}, {"dim": "hero_tier"}, {"dim": "division"}],
         "visuals": [
             V("k1", "kpi", "Sale at risk, next 7 days", 0, 0, 3, 2, measures=["lost_7d"]),
             V("k2", "kpi", "30-day gap (value)", 3, 0, 3, 2, measures=["gap_v"]),
             V("k3", "kpi", "Out of stock (store x article)", 6, 0, 3, 2, measures=["oos_stores"]),
             V("k4", "kpi", "Heroes not ranged", 9, 0, 3, 2, measures=["not_ranged_stores"]),
             V("t1", "table", "Action list, biggest gap first", 0, 2, 12, 14,
               dims=["store", "store_name", "gen_article", "gen_article_desc", "major_category", "hero_tier", "gap_type",
                     "action"],
               measures=["sale_l30", "ros", "store_stock", "inbound", "rdc_stock", "fcst_30", "gap_q", "gap_v", "lost_7d"],
               sort={"by": "gap_v", "dir": "desc"}, limit=2000),
         ]},
        {"id": "p2", "name": "By store",
         "slicers": [{"dim": "zone"}, {"dim": "region"}, {"dim": "cluster"}, {"dim": "hero_tier"}],
         "visuals": [
             V("b1", "bar", "Top 15 stores by sale at risk", 0, 0, 5, 9, dims=["store_name"], measures=["lost_7d"],
               sort={"by": "lost_7d", "dir": "desc"}, limit=15),
             V("t1", "table", "Stores", 5, 0, 7, 9, dims=["store", "store_name", "cluster"],
               measures=["oos_stores", "not_ranged_stores", "low_cover_stores", "broken_size_stores", "gap_q", "gap_v",
                         "lost_7d"], sort={"by": "gap_v", "dir": "desc"}, limit=1000),
         ]},
        {"id": "p3", "name": "By action",
         "slicers": [{"dim": "zone"}, {"dim": "store"}, {"dim": "hero_tier"}],
         "visuals": [
             V("d1", "donut", "Rows by suggested action", 0, 0, 6, 7, dims=["action"], measures=["pairs"],
               sort={"by": "pairs", "dir": "desc"}),
             V("b1", "bar", "Gap value by suggested action", 6, 0, 6, 7, dims=["action"], measures=["gap_v"],
               sort={"by": "gap_v", "dir": "desc"}),
         ]},
    ]},
)

SUPPLY_VIEW = dict(
    name="Hero articles · Replenishment & buying", category=CATEGORY, dataset="hero_position", audience=SUPPLY,
    description="For merchandising, buying and the RDCs: the top 500 heroes' 30-day gap against DC stock, by RDC and "
                "by vendor, and where excess in some stores could cover stock-outs in others.",
    definition={"version": 1, "filters": TOP500, "pages": [
        {"id": "p1", "name": "DC backing",
         "slicers": [{"dim": "hero_tier"}, {"dim": "division"}, {"dim": "major_category"}, {"dim": "vendor"}],
         "visuals": [
             V("k1", "kpi", "30-day gap (units)", 0, 0, 3, 2, measures=["gap_q"]),
             V("k2", "kpi", "30-day gap (value)", 3, 0, 3, 2, measures=["gap_v"]),
             V("k3", "kpi", "Inbound (units)", 6, 0, 3, 2, measures=["inbound"]),
             V("k4", "kpi", "Out of stock (store x hero)", 9, 0, 3, 2, measures=["oos_stores"]),
             V("t1", "table", "Gap vs DC stock per article (DC stock / gap under 100% = reorder)", 0, 2, 12, 14,
               dims=["gen_article", "gen_article_desc", "major_category", "vendor", "hero_tier"],
               measures=["sale_l30", "qty_l30", "fcst_30", "store_stock", "inbound", "gap_q", "dc_stock", "dc_cover_gap",
                         "oos_stores", "not_ranged_stores", "size_health"], sort={"by": "gap_q", "dir": "desc"}, limit=500),
         ]},
        {"id": "p2", "name": "By RDC",
         "slicers": [{"dim": "rdc"}, {"dim": "division"}, {"dim": "major_category"}],
         "visuals": [
             V("c1", "column", "30-day gap (units) by RDC", 0, 0, 5, 6, dims=["rdc"], measures=["gap_q"],
               sort={"by": "gap_q", "dir": "desc"}),
             V("t1", "table", "RDC x article", 5, 0, 7, 14, dims=["rdc", "gen_article", "gen_article_desc"],
               measures=["gap_q", "rdc_stock", "inbound", "oos_stores", "not_ranged_stores"],
               sort={"by": "gap_q", "dir": "desc"}, limit=1000),
         ]},
        {"id": "p3", "name": "By vendor",
         "slicers": [{"dim": "division"}, {"dim": "major_category"}],
         "visuals": [
             V("b1", "bar", "Top 15 vendors by 30-day gap (value)", 0, 0, 5, 9, dims=["vendor"], measures=["gap_v"],
               sort={"by": "gap_v", "dir": "desc"}, limit=15),
             V("t1", "table", "Vendors", 5, 0, 7, 9, dims=["vendor"],
               measures=["articles", "sale_l30", "growth_wow", "availability", "oos_stores", "gap_q", "gap_v"],
               sort={"by": "gap_v", "dir": "desc"}, limit=500),
         ]},
        {"id": "p4", "name": "Excess to re-deploy",
         "slicers": [{"dim": "zone"}, {"dim": "division"}, {"dim": "major_category"}],
         "visuals": [
             V("t1", "table", "Heroes with excess in some stores and stock-outs in others", 0, 0, 12, 14,
               dims=["gen_article", "gen_article_desc", "major_category"],
               measures=["excess_stores", "excess_q", "excess_v", "oos_stores", "low_cover_stores", "gap_q", "dc_stock"],
               sort={"by": "excess_v", "dir": "desc"}, limit=500),
         ]},
    ]},
)

SALES_OVER_TIME = dict(
    name="Hero articles · Sales over time", category=CATEGORY, dataset="hero_sales", audience=ALL_HERO,
    description="The top 100 articles of any period - a day, week, month, quarter or the fiscal year - for your stores, "
                "against last year, with the trend and the time x merchandise grid.",
    definition={"version": 1, "filters": NON_MERCH, "pages": [
        {"id": "p1", "name": "Top 100 for the period",
         "slicers": [{"dim": "date", "mode": "range", "preset": "mtd"}, {"dim": "hero_tier"}, {"dim": "zone"},
                     {"dim": "division"}, {"dim": "major_category"}],
         "visuals": [
             V("k1", "kpi", "Sale", 0, 0, 3, 2, measures=["sale_v"]),
             V("k2", "kpi", "Growth vs LY", 3, 0, 3, 2, measures=["growth_ly"]),
             V("k3", "kpi", "GM %", 6, 0, 3, 2, measures=["gm_pct"]),
             V("k4", "kpi", "Discount %", 9, 0, 3, 2, measures=["disc_pct"]),
             V("t1", "table", "Top 100 articles in the period", 0, 2, 12, 14,
               dims=["gen_article", "gen_article_desc", "major_category", "hero_tier"],
               measures=["sale_v", "sale_q", "ly_sale_v", "growth_ly", "gm_pct", "disc_pct", "asp", "realisation",
                         "stores_selling", "selling_days", "sale_per_store"], sort={"by": "sale_v", "dir": "desc"},
               limit=100),
         ]},
        {"id": "p2", "name": "Trend",
         "slicers": [{"dim": "date", "mode": "range", "preset": "fytd"}, {"dim": "hero_tier"}, {"dim": "zone"},
                     {"dim": "division"}],
         "visuals": [
             V("l1", "line", "Weekly sale vs last year", 0, 0, 12, 6, dims=["week"], measures=["sale_v", "ly_sale_v"],
               sort={"by": "week", "dir": "asc"}),
             V("c1", "column", "Monthly sale", 0, 6, 6, 6, dims=["month"], measures=["sale_v"],
               sort={"by": "month", "dir": "asc"}),
             V("c2", "column", "Sale by day of week", 6, 6, 6, 6, dims=["weekday"], measures=["avg_daily"],
               sort={"by": "weekday", "dir": "asc"}),
         ]},
        {"id": "p3", "name": "Time x merchandise",
         "slicers": [{"dim": "date", "mode": "range", "preset": "fytd"}, {"dim": "hero_tier"}, {"dim": "zone"},
                     {"dim": "segment"}],
         "visuals": [
             V("m1", "matrix", "Sale by division and month", 0, 0, 12, 7, dims=["division", "month"], measures=["sale_v"]),
             V("t1", "table", "Fiscal quarters and months", 0, 7, 12, 7, dims=["fiscal_quarter", "month"],
               measures=["sale_v", "ly_sale_v", "growth_ly", "gm_pct", "disc_pct", "articles", "stores_selling"],
               sort={"by": "month", "dir": "asc"}, limit=60),
         ]},
        {"id": "p4", "name": "Locations",
         "slicers": [{"dim": "date", "mode": "range", "preset": "mtd"}, {"dim": "hero_tier"}, {"dim": "division"},
                     {"dim": "major_category"}],
         "visuals": [
             V("c1", "column", "Sale by zone", 0, 0, 6, 6, dims=["zone"], measures=["sale_v", "ly_sale_v"],
               sort={"by": "sale_v", "dir": "desc"}),
             V("b1", "bar", "Top 15 stores", 6, 0, 6, 6, dims=["store_name"], measures=["sale_v"],
               sort={"by": "sale_v", "dir": "desc"}, limit=15),
             V("t1", "table", "Stores", 0, 6, 12, 8, dims=["zone", "region", "store", "store_name"],
               measures=["sale_v", "ly_sale_v", "growth_ly", "gm_pct", "disc_pct", "articles"],
               sort={"by": "sale_v", "dir": "desc"}, limit=1000),
         ]},
    ]},
)


# ---------------------------------------------------------------- Analytics: one navigation dashboard
# The sidebar tab "Analytics" opens a single dashboard whose page tabs are the navigation. A dashboard has one
# dataset, so "analytics" stacks the latest position snapshot (ROW_KIND 'POS', dated its as-of day) on the daily
# sale rows (ROW_KIND 'SALE'). Every measure reads columns that only one kind fills, so the two never mix:
# position measures sum one snapshot, sale measures add up over the dates picked. T100_* columns are the company
# top 100 only, so the Overview page shows the heroes without a report-wide filter.
_SHARED = ["ST_CD", "ST_NM", "STATE", "ZONE_CODE", "REGION_CODE", "CLUSTER_CODE", "ST_STATUS", "RDC_CD", "SEG", "DIV",
           "SUB_DIV", "MAJ_CAT", "MC_DESC", "GEN_ART_NUMBER", "GEN_ART_DESC", "MRP", "VENDOR_NAME", "RNG_SEG", "SSN",
           "FIT", "PATTERN", "FABRIC", "HERO_TIER", "MC_HERO"]
_POS_ONLY = ["ST_TOP100", "CO_RANK", "ST_RANK", "GAP_TYPE", "ACTION", "MOMENTUM", "DISCOUNT_BAND", "AGE_BAND",
             "COVER_BAND", "SEASON_OUTLOOK",
             "SALE_V_L7D", "SALE_Q_L7D", "SALE_V_P7D", "SALE_Q_P7D", "SALE_V_L30D", "SALE_Q_L30D", "GM_V_L30D",
             "DISC_V_L30D", "GROSS_V_L30D", "MRP_V_L30D", "SALE_V_MTD", "LY_SALE_V_L30D",
             "FLOOR_Q", "BACKROOM_Q", "ST_STK_Q", "ST_STK_V", "PIPELINE_Q", "INTRANSIT_Q", "INBOUND_Q", "RDC_Q",
             "ALL_DC_Q", "SELL_THRU_DEN", "ROS_Q", "SEASON_FACTOR", "FCST_30_Q", "FCST_30_V", "AVAIL_Q", "GAP_Q",
             "GAP_V", "LOST_7D_V", "EXCESS_Q", "EXCESS_V", "CORE_SIZES", "CORE_SIZES_STK", "SELLING_F", "STOCKED_F",
             "OOS_F", "LOW_COVER_F", "BROKEN_SIZE_F", "NOT_RANGED_F", "EXCESS_F", "SHOULD_STOCK_F", "IN_STOCK_F",
             "AGE_DAYS"]
_T100 = ["SALE_V_L30D", "SALE_Q_L7D", "SALE_Q_P7D", "GM_V_L30D", "DISC_V_L30D", "GROSS_V_L30D", "ST_STK_Q",
         "INBOUND_Q", "FCST_30_Q", "ROS_Q", "GAP_V", "LOST_7D_V", "OOS_F", "SHOULD_STOCK_F", "IN_STOCK_F"]
_SALE_ONLY = ["SALE_V", "SALE_Q", "GM_V", "DISC_V", "GROSS_V", "GM_BASE_V", "MRP_V", "LY_SALE_V", "LY_SALE_Q",
              "SELLING_ST", "SELLING_DT"]
_ACTIONABLE_TYPES = "('1 · Out of stock', '2 · Hero not ranged', '3 · Low cover (<7 days)', '4 · Broken sizes')"


def _analytics_sql():
    pos = (["'POS' as ROW_KIND", "p.AS_OF_DT as DT"] + ["p.%s" % c for c in _SHARED + _POS_ONLY]
           + ["iff(p.HERO_TIER = 'A · Top 100', p.%s, null) as T100_%s" % (c, c) for c in _T100]
           + ["iff(p.HERO_TIER = 'A · Top 100', 1, null) as T100_ROW",
              "iff(p.GAP_TYPE in %s, p.GAP_V, null) as ACTION_GAP_V" % _ACTIONABLE_TYPES,
              "1 as POS_ROW", "p.GEN_ART_NUMBER as POS_GA", "p.ST_CD as POS_ST_CD", "p.ST_NM as POS_ST_NM"]
           + ["null as %s" % c for c in _SALE_ONLY] + ["null as SALE_GA"])
    sale = (["'SALE'", "s.DT"] + ["s.%s" % c for c in _SHARED] + ["null"] * len(_POS_ONLY)
            + ["null"] * (len(_T100) + 6) + ["s.%s" % c for c in _SALE_ONLY] + ["s.GEN_ART_NUMBER"])
    return ("select %s\nfrom %s.V_BI_HERO_POSITION p\nwhere p.AS_OF_DT = (select max(AS_OF_DT) from V2RETAIL.SILVER.HERO_POSITION)\n"
            "union all\nselect %s\nfrom %s.V_BI_HERO_SALES s" % (",\n       ".join(pos), PULSE, ",\n       ".join(sale), PULSE))


ANALYTICS_SOURCE_SQL = _analytics_sql()   # -> view SILVER.V_HERO_ANALYTICS_SRC (snowflake/04_*.sql)
_pos_dims = [dict(d) for d in POSITION_DATASET["dimensions"] if d["key"] != "as_of"]
for _d in _pos_dims:   # store-level fields are position-only: sale rows then fold into a few groups per article
    _d["col"] = {"ST_CD": "POS_ST_CD", "ST_NM": "POS_ST_NM"}.get(_d["col"], _d["col"])
_sale_time = [d for d in SALES_DATASET["dimensions"] if d["group"] == _T and d["key"] != "source"]
_pos_measures = [dict(m) for m in POSITION_DATASET["measures"] if m["key"] != "stores"]
for _m in _pos_measures:            # one snapshot only, so no as-of roll-up
    _m.pop("time_agg", None)
    if _m["key"] == "pairs":
        _m.update(col="POS_ROW", agg="sum")
    if _m["key"] == "articles":
        _m.update(col="POS_GA")
_t100_measures = [
    M("t100_sale", "Top 100 sale L30D", "T100_SALE_V_L30D", fmt="inr"), M("t100_q7", "Top 100 qty L7D", "T100_SALE_Q_L7D"),
    M("t100_qp7", "Top 100 qty prior 7D", "T100_SALE_Q_P7D"), M("t100_gm", "Top 100 GM L30D", "T100_GM_V_L30D", fmt="inr"),
    M("t100_disc", "Top 100 discount L30D", "T100_DISC_V_L30D", fmt="inr"),
    M("t100_gross", "Top 100 gross sale L30D", "T100_GROSS_V_L30D", fmt="inr"),
    M("t100_stock", "Top 100 store stock", "T100_ST_STK_Q"), M("t100_inbound", "Top 100 inbound", "T100_INBOUND_Q"),
    M("t100_fcst", "Top 100 forecast next 30 days", "T100_FCST_30_Q"), M("t100_ros", "Top 100 rate of sale", "T100_ROS_Q"),
    M("t100_gap_v", "Top 100 30-day gap (value)", "T100_GAP_V", fmt="inr"),
    M("t100_lost", "Top 100 sale at risk, 7 days", "T100_LOST_7D_V", fmt="inr"),
    M("t100_oos", "Top 100 out of stock (store x hero)", "T100_OOS_F"),
    M("t100_should", "Top 100 stores that should stock", "T100_SHOULD_STOCK_F"),
    M("t100_instock", "Top 100 stores in stock", "T100_IN_STOCK_F"), M("t100_rows", "Top 100 store x hero rows", "T100_ROW"),
    M("action_gap_v", "Gap on actionable rows (value)", "ACTION_GAP_V", fmt="inr"),
    M("t100_growth_wow", "Top 100 qty vs prior week", agg="growth", num="t100_q7", den="t100_qp7", fmt="pct"),
    M("t100_avail", "Top 100 availability %", agg="ratio", num="t100_instock", den="t100_should", fmt="pct"),
    M("t100_cover", "Top 100 cover (days)", agg="ratio", num="t100_stock", den="t100_ros"),
    M("t100_gm_pct", "Top 100 GM %", agg="ratio", num="t100_gm", den="t100_sale", fmt="pct"),
    M("t100_disc_pct", "Top 100 discount %", agg="ratio", num="t100_disc", den="t100_gross", fmt="pct"),
]
_sale_measures = [
    M("sale_v", "Sale value", "SALE_V", fmt="inr"), M("sale_q", "Sale qty", "SALE_Q"),
    M("gm_v", "Gross margin", "GM_V", fmt="inr"), M("disc_v", "Discount", "DISC_V", fmt="inr"),
    M("gross_v", "Gross sale (before discount)", "GROSS_V", fmt="inr"),
    M("gm_base_v", "Sale with GM known", "GM_BASE_V", fmt="inr"), M("mrp_v", "Sale at MRP", "MRP_V", fmt="inr"),
    M("ly_sale_v", "LY sale", "LY_SALE_V", fmt="inr"), M("ly_sale_q", "LY qty", "LY_SALE_Q"),
    M("sale_stores", "Stores selling", "SELLING_ST", agg="count_distinct"),
    M("selling_days", "Selling days", "SELLING_DT", agg="count_distinct"),
    M("sale_articles", "Articles sold", "SALE_GA", agg="count_distinct"),
    M("sale_growth_ly", "Growth vs LY", agg="growth", num="sale_v", den="ly_sale_v", fmt="pct"),
    M("sale_gm_pct", "GM %", agg="ratio", num="gm_v", den="gm_base_v", fmt="pct"),
    M("sale_disc_pct", "Discount %", agg="ratio", num="disc_v", den="gross_v", fmt="pct"),
    M("sale_asp", "Average selling price", agg="ratio", num="sale_v", den="sale_q", fmt="inr"),
    M("sale_realisation", "Price realisation (vs MRP)", agg="ratio", num="sale_v", den="mrp_v", fmt="pct"),
    M("sale_avg_daily", "Avg daily sale", agg="ratio", num="sale_v", den="selling_days", fmt="inr"),
    M("sale_per_store", "Sale per selling store", agg="ratio", num="sale_v", den="sale_stores", fmt="inr"),
]
for _m in _t100_measures + _sale_measures:
    _m.pop("time_agg", None)

ANALYTICS_DATASET = dict(
    code="analytics", name="Analytics - hero articles, stock, gaps and sales",
    description="Today's position of every store x generic article (sales windows, stock along the pipeline, forecast, "
                "gap, ranks) together with daily sale since April 2025 and its last-year twin. Position figures are as "
                "of the latest day; sale figures add up over the dates you pick.",
    # Queries read a table that SILVER.SP_HERO_BUILD materialises from ANALYTICS_SOURCE_SQL (the stacked rows) after
    # each build, so a page does not re-join 157M sale rows to the store and article masters on every open.
    view="V_BI_ANALYTICS", sql="select * from V2RETAIL.SILVER.HERO_ANALYTICS", snapshot_dim=None, scope="ST_CD", cache=3600, audience=ALL_HERO,
    refresh=POSITION_DATASET["refresh"] + " " + SALES_DATASET["refresh"],
    dimensions=_sale_time + [D("row_kind", "Row type (POS / SALE)", "ROW_KIND", group=_T)] + _pos_dims,
    measures=_pos_measures + _t100_measures + _sale_measures,
)
DATASETS.append(ANALYTICS_DATASET)

_HERO_TABLE = ["best_rank", "sale_l30", "qty_l30", "growth_wow", "stores_selling", "availability", "oos_stores",
               "not_ranged_stores", "store_stock", "inbound", "dc_stock", "fcst_30", "gap_q", "cover_days", "disc_pct",
               "gm_pct", "size_health"]
_MTD = {"dim": "date", "mode": "range", "preset": "mtd"}
_FYTD = {"dim": "date", "mode": "range", "preset": "fytd"}

ANALYTICS = dict(
    # Was called "Analytics" until the Analytics tab became a landing page; id_name keeps its id and links.
    id_name="Analytics", name="Hero articles · All pages", category=CATEGORY, dataset="analytics", audience=ALL_HERO,
    description="One dashboard, twelve pages: the company's hero articles, their stock and gaps by location and product, "
                "sales over any period, store actions, replenishment and the signals for next month. Use the page tabs "
                "to move between them.",
    definition={"version": 1, "filters": NON_MERCH, "pages": [
        {"id": "p1", "name": "Overview",
         "slicers": [{"dim": "zone"}, {"dim": "region"}, {"dim": "division"}, {"dim": "major_category"}],
         "visuals": [
             V("k1", "kpi", "Top 100 sale L30D", 0, 0, 2, 2, measures=["t100_sale"]),
             V("k2", "kpi", "Qty vs prior week", 2, 0, 2, 2, measures=["t100_growth_wow"]),
             V("k3", "kpi", "Availability %", 4, 0, 2, 2, measures=["t100_avail"]),
             V("k4", "kpi", "Out of stock (store x hero)", 6, 0, 2, 2, measures=["t100_oos"]),
             V("k5", "kpi", "Sale at risk, next 7 days", 8, 0, 2, 2, measures=["t100_lost"]),
             V("k6", "kpi", "30-day gap (value)", 10, 0, 2, 2, measures=["t100_gap_v"]),
             V("k7", "kpi", "Store stock (units)", 0, 2, 2, 2, measures=["t100_stock"]),
             V("k8", "kpi", "Inbound (units)", 2, 2, 2, 2, measures=["t100_inbound"]),
             V("k9", "kpi", "Forecast next 30 days", 4, 2, 2, 2, measures=["t100_fcst"]),
             V("k10", "kpi", "Cover (days)", 6, 2, 2, 2, measures=["t100_cover"]),
             V("k11", "kpi", "GM %", 8, 2, 2, 2, measures=["t100_gm_pct"]),
             V("k12", "kpi", "Discount %", 10, 2, 2, 2, measures=["t100_disc_pct"]),
             V("v1", "bar", "Top 100 sale L30D by division", 0, 4, 6, 6, dims=["division"], measures=["t100_sale"],
               sort={"by": "t100_sale", "dir": "desc"}, limit=15),
             V("v2", "donut", "Top 100 store x hero rows by gap type", 6, 4, 6, 6, dims=["gap_type"],
               measures=["t100_rows"], sort={"by": "t100_rows", "dir": "desc"}),
             V("v3", "column", "Top 100 availability % by zone", 0, 10, 6, 5, dims=["zone"], measures=["t100_avail"],
               sort={"by": "zone", "dir": "asc"}),
             V("v4", "column", "Top 100 sale at risk by zone", 6, 10, 6, 5, dims=["zone"], measures=["t100_lost"],
               sort={"by": "t100_lost", "dir": "desc"}),
         ]},
        {"id": "p2", "name": "Top 100",
         "slicers": [{"dim": "hero_tier"}, {"dim": "zone"}, {"dim": "region"}, {"dim": "division"}, {"dim": "major_category"}],
         "visuals": [
             V("t1", "table", "Top 100 articles for your stores and selection (last 30 days)", 0, 0, 12, 15,
               dims=["gen_article", "gen_article_desc", "major_category", "hero_tier"], measures=_HERO_TABLE,
               sort={"by": "sale_l30", "dir": "desc"}, limit=100),
         ]},
        {"id": "p3", "name": "Locations",
         "slicers": [{"dim": "hero_tier"}, {"dim": "zone"}, {"dim": "region"}, {"dim": "division"}],
         "visuals": [
             V("m1", "matrix", "Store x article rows: zone by gap type", 0, 0, 12, 5, dims=["zone", "gap_type"],
               measures=["pairs"]),
             V("t1", "table", "Regions", 0, 5, 12, 6, dims=["zone", "region"],
               measures=["sale_l30", "availability", "oos_stores", "low_cover_stores", "not_ranged_stores", "lost_7d",
                         "gap_v", "store_stock", "inbound"], sort={"by": "sale_l30", "dir": "desc"}, limit=200),
             V("b1", "bar", "Top 15 stores by sale at risk", 0, 11, 5, 9, dims=["store_name"], measures=["lost_7d"],
               sort={"by": "lost_7d", "dir": "desc"}, limit=15),
             V("t2", "table", "Stores", 5, 11, 7, 9, dims=["store", "store_name", "cluster"],
               measures=["sale_l30", "availability", "oos_stores", "not_ranged_stores", "broken_size_stores", "lost_7d",
                         "gap_v"], sort={"by": "lost_7d", "dir": "desc"}, limit=1000),
         ]},
        {"id": "p4", "name": "Products",
         "slicers": [{"dim": "hero_tier"}, {"dim": "zone"}, {"dim": "division"}, {"dim": "sub_division"}],
         "visuals": [
             V("b1", "bar", "30-day gap (value) by major category", 0, 0, 6, 7, dims=["major_category"],
               measures=["gap_v"], sort={"by": "gap_v", "dir": "desc"}, limit=20),
             V("b2", "bar", "Sale L30D by major category", 6, 0, 6, 7, dims=["major_category"],
               measures=["sale_l30"], sort={"by": "sale_l30", "dir": "desc"}, limit=20),
             V("t1", "table", "Merchandise hierarchy", 0, 7, 12, 9, dims=["division", "sub_division", "major_category"],
               measures=["articles", "sale_l30", "growth_wow", "availability", "oos_stores", "not_ranged_stores",
                         "broken_size_stores", "store_stock", "inbound", "gap_q", "gap_v", "sell_through"],
               sort={"by": "sale_l30", "dir": "desc"}, limit=500),
         ]},
        {"id": "p5", "name": "Period top 100",
         "slicers": [_MTD, {"dim": "hero_tier"}, {"dim": "zone"}, {"dim": "division"}, {"dim": "major_category"}],
         "visuals": [
             V("k1", "kpi", "Sale", 0, 0, 3, 2, measures=["sale_v"]),
             V("k2", "kpi", "Growth vs LY", 3, 0, 3, 2, measures=["sale_growth_ly"]),
             V("k3", "kpi", "GM %", 6, 0, 3, 2, measures=["sale_gm_pct"]),
             V("k4", "kpi", "Discount %", 9, 0, 3, 2, measures=["sale_disc_pct"]),
             V("t1", "table", "Top 100 articles in the dates picked", 0, 2, 12, 14,
               dims=["gen_article", "gen_article_desc", "major_category", "hero_tier"],
               measures=["sale_v", "sale_q", "ly_sale_v", "sale_growth_ly", "sale_gm_pct", "sale_disc_pct", "sale_asp",
                         "sale_realisation", "sale_stores", "selling_days", "sale_per_store"],
               sort={"by": "sale_v", "dir": "desc"}, limit=100),
         ]},
        {"id": "p6", "name": "Trend",
         "slicers": [_FYTD, {"dim": "hero_tier"}, {"dim": "zone"}, {"dim": "division"}],
         "visuals": [
             V("l1", "line", "Weekly sale vs last year", 0, 0, 12, 6, dims=["week"], measures=["sale_v", "ly_sale_v"],
               sort={"by": "week", "dir": "asc"}),
             V("c1", "column", "Monthly sale", 0, 6, 6, 6, dims=["month"], measures=["sale_v"],
               sort={"by": "month", "dir": "asc"}),
             V("c2", "column", "Average daily sale by day of week", 6, 6, 6, 6, dims=["weekday"],
               measures=["sale_avg_daily"], sort={"by": "weekday", "dir": "asc"}),
         ]},
        {"id": "p7", "name": "Time x merchandise",
         "slicers": [_FYTD, {"dim": "hero_tier"}, {"dim": "zone"}, {"dim": "segment"}],
         "visuals": [
             V("m1", "matrix", "Sale by division and month", 0, 0, 12, 7, dims=["division", "month"], measures=["sale_v"]),
             V("t1", "table", "Fiscal quarters and months", 0, 7, 12, 7, dims=["fiscal_quarter", "month"],
               measures=["sale_v", "ly_sale_v", "sale_growth_ly", "sale_gm_pct", "sale_disc_pct", "sale_articles",
                         "sale_stores"], sort={"by": "month", "dir": "asc"}, limit=60),
         ]},
        {"id": "p8", "name": "Store actions",
         "slicers": [{"dim": "store"}, {"dim": "gap_type"}, {"dim": "hero_tier"}, {"dim": "division"}],
         "visuals": [
             V("k1", "kpi", "Sale at risk, next 7 days", 0, 0, 3, 2, measures=["lost_7d"]),
             V("k2", "kpi", "Gap on actionable rows", 3, 0, 3, 2, measures=["action_gap_v"]),
             V("k3", "kpi", "Out of stock (store x article)", 6, 0, 3, 2, measures=["oos_stores"]),
             V("k4", "kpi", "Heroes not ranged", 9, 0, 3, 2, measures=["not_ranged_stores"]),
             V("t1", "table", "Action list, biggest gap first", 0, 2, 12, 14,
               dims=["store", "store_name", "gen_article", "gen_article_desc", "major_category", "hero_tier", "gap_type",
                     "action"],
               measures=["action_gap_v", "lost_7d", "sale_l30", "ros", "store_stock", "inbound", "rdc_stock", "fcst_30",
                         "gap_q"], sort={"by": "action_gap_v", "dir": "desc"}, limit=2000),
         ]},
        {"id": "p9", "name": "Store heroes",
         "slicers": [{"dim": "store"}, {"dim": "store_top100"}, {"dim": "category_hero"}, {"dim": "division"}],
         "visuals": [
             V("t1", "table", "Best sellers by store (pick a store, or 'Y' for its own top 100)", 0, 0, 12, 15,
               dims=["store", "store_name", "store_rank", "gen_article", "gen_article_desc", "major_category",
                     "category_hero", "gap_type"],
               measures=["sale_l30", "qty_l30", "store_stock", "inbound", "rdc_stock", "fcst_30", "gap_q", "cover_days"],
               sort={"by": "sale_l30", "dir": "desc"}, limit=2000),
         ]},
        {"id": "p10", "name": "Replenishment",
         "slicers": [{"dim": "hero_tier"}, {"dim": "division"}, {"dim": "major_category"}, {"dim": "vendor"}],
         "visuals": [
             V("k1", "kpi", "30-day gap (units)", 0, 0, 3, 2, measures=["gap_q"]),
             V("k2", "kpi", "30-day gap (value)", 3, 0, 3, 2, measures=["gap_v"]),
             V("k3", "kpi", "Inbound (units)", 6, 0, 3, 2, measures=["inbound"]),
             V("k4", "kpi", "Out of stock (store x article)", 9, 0, 3, 2, measures=["oos_stores"]),
             V("t1", "table", "Gap vs DC stock per article (DC stock / gap under 100% = reorder)", 0, 2, 12, 14,
               dims=["gen_article", "gen_article_desc", "major_category", "vendor", "hero_tier"],
               measures=["sale_l30", "fcst_30", "store_stock", "inbound", "gap_q", "dc_stock", "dc_cover_gap",
                         "oos_stores", "not_ranged_stores", "excess_stores", "excess_q"],
               sort={"by": "gap_q", "dir": "desc"}, limit=500),
         ]},
        {"id": "p11", "name": "RDC & vendor",
         "slicers": [{"dim": "hero_tier"}, {"dim": "rdc"}, {"dim": "division"}, {"dim": "major_category"}],
         "visuals": [
             V("c1", "column", "30-day gap (units) by RDC", 0, 0, 4, 6, dims=["rdc"], measures=["gap_q"],
               sort={"by": "gap_q", "dir": "desc"}),
             V("b1", "bar", "Top 15 vendors by 30-day gap (value)", 4, 0, 8, 6, dims=["vendor"], measures=["gap_v"],
               sort={"by": "gap_v", "dir": "desc"}, limit=15),
             V("t1", "table", "Vendors", 0, 6, 12, 9, dims=["vendor"],
               measures=["articles", "sale_l30", "growth_wow", "availability", "oos_stores", "gap_q", "gap_v"],
               sort={"by": "gap_v", "dir": "desc"}, limit=500),
         ]},
        {"id": "p12", "name": "Drivers",
         "slicers": [{"dim": "hero_tier"}, {"dim": "zone"}, {"dim": "division"}, {"dim": "major_category"}],
         "visuals": [
             V("c1", "column", "Sale L30D by momentum", 0, 0, 4, 5, dims=["momentum"], measures=["sale_l30"],
               sort={"by": "momentum", "dir": "asc"}),
             V("c2", "column", "Sale L30D by discount band", 4, 0, 4, 5, dims=["discount_band"], measures=["sale_l30"],
               sort={"by": "discount_band", "dir": "asc"}),
             V("c3", "column", "Sale L30D by article age", 8, 0, 4, 5, dims=["age_band"], measures=["sale_l30"],
               sort={"by": "age_band", "dir": "asc"}),
             V("c4", "column", "Forecast vs available by season outlook", 0, 5, 6, 5, dims=["season_outlook"],
               measures=["fcst_30", "avail"], sort={"by": "season_outlook", "dir": "asc"}),
             V("c5", "column", "Store x article rows by stock cover", 6, 5, 6, 5, dims=["cover_band"], measures=["pairs"],
               sort={"by": "cover_band", "dir": "asc"}),
             V("t1", "table", "Drivers per article", 0, 10, 12, 10, dims=["gen_article", "gen_article_desc", "major_category"],
               measures=["sale_l30", "growth_wow", "growth_ly", "season_factor", "fcst_30", "avail", "gap_q",
                         "disc_pct", "realisation", "gm_pct", "asp", "age", "size_health", "availability",
                         "sell_through"], sort={"by": "sale_l30", "dir": "desc"}, limit=100),
         ]},
    ]},
)

REPORTS = [ANALYTICS, COMMAND_CENTRE, EXPLORER, STORE_ACTIONS, SUPPLY_VIEW, SALES_OVER_TIME]
# Karma productivity lives in its own catalog and is seeded with the hero dashboards.
from . import karma_catalog as _karma  # noqa: E402
DATASETS.extend(_karma.DATASETS)
REPORTS.extend(_karma.REPORTS)
from . import ai_catalog as _ai  # noqa: E402
DATASETS.extend(_ai.DATASETS)
REPORTS.extend(_ai.REPORTS)
# The allocation gap reports have their own library category but ride the same seed.
# Guarded: alc_gap_catalog.py is an optional pack; if it is not deployed, skip it so the whole
# portal still starts instead of crashing on import (restored 2026-10-17).
try:
    from . import alc_gap_catalog as _alc  # noqa: E402
    DATASETS.extend(_alc.DATASETS)
    REPORTS.extend(_alc.REPORTS)
    _ALC_OK = True
except Exception as _alc_err:  # noqa: BLE001
    import logging as _logging
    _logging.getLogger('pulse').warning('alc_gap pack not loaded (missing alc_gap_catalog?): %s', _alc_err)
    _ALC_OK = False
# A dashboard for each B&M report (Merchandising and Sourcing trees), in their own library categories.
from . import bm_dashboards as _bmd  # noqa: E402
DATASETS.extend(_bmd.DATASETS)
REPORTS.extend(_bmd.REPORTS)
from . import karma_sop as _ksop  # noqa: E402
DATASETS.extend(_ksop.DATASETS)
REPORTS.extend(_ksop.REPORTS)
from . import karma_yn as _kyn  # noqa: E402
DATASETS.extend(_kyn.DATASETS)
REPORTS.extend(_kyn.REPORTS)
from . import fixgap as _fgap  # noqa: E402
DATASETS.extend(_fgap.DATASETS)
REPORTS.extend(_fgap.REPORTS)
from . import karma_sm as _ksm  # noqa: E402
DATASETS.extend(_ksm.DATASETS)
REPORTS.extend(_ksm.REPORTS)
for _r in REPORTS:   # visual ids must be unique across the whole report: prefix each with its page id
    for _p in _r["definition"]["pages"]:
        for _v in _p["visuals"]:
            _v["id"] = "%s_%s" % (_p["id"], _v["id"])
HOME_REPORT_ID = rid(ANALYTICS)


def seed(cur):
    """Insert the hero datasets and dashboards if missing (runs before bi.seed, so ours carry cache and audience)."""
    for d in DATASETS:
        cur.execute("insert into bi.dataset (code, name, description, source_object, dimensions, measures, snapshot_dim,"
                    " scope_column, refresh_note, audience_roles, cache_seconds) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                    " on conflict (code) do nothing",
                    (d["code"], d["name"], d["description"], "%s.%s" % (PULSE, d["view"]), json.dumps(d["dimensions"]),
                     json.dumps(d["measures"]), d.get("snapshot_dim"), d.get("scope"), d.get("refresh", ""),
                     d.get("audience", []), d.get("cache", 900)))
    for r in REPORTS:
        cur.execute("insert into bi.report (id, visibility, name, description, category, dataset_code, definition,"
                    " status, audience_roles) values (%s,'library',%s,%s,%s,%s,%s,'published',%s) on conflict (id) do nothing",
                    (rid(r), r["name"], r["description"], r["category"], r["dataset"],
                     json.dumps(r["definition"]), r.get("audience", [])))
    # The first release filed these dashboards under "Hero Articles"; they now sit with Analytics.
    cur.execute("update bi.report set category = %s, updated_at = now() where id = any(%s::uuid[]) and category = 'Hero Articles'",
                (CATEGORY, [rid(r) for r in REPORTS]))
    cur.execute("update bi.report set name = %s, updated_at = now() where id = %s and name = 'Analytics'",
                (ANALYTICS["name"], rid(ANALYTICS)))
    # The B&M reports that open as tables in the Merchandising & Planning tree.
    from . import bm_reports
    bm_reports.seed(cur)
    # A link to each B&M report's dashboard, filed next to the report in its tree.
    _bmd.seed_links(cur, rid)
    _ksop.seed(cur, rid)
    _kyn.seed(cur, rid)
    _fgap.seed(cur, rid)
    _ksm.seed(cur, rid)
 # The 22 allocation gap formats as library tables, in the Allocation folder with the gap dashboards.
    if _ALC_OK:
        from . import alc_gap_reports
        alc_gap_reports.seed(cur, rid)