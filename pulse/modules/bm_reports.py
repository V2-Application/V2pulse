"""B&M reports that open inside the report library: the Merchandising & Planning tree and the Sourcing / SRM tree
(the one people call B&M).

The Control Tower files every scheduled B&M report into the department tree as a status card, which shows whether the
report ran but not the report. Each report here is the same output read live from Snowflake through a curated
PULSE view and opened as a table in the library itself: filters on top, sortable columns, paging and CSV export, and
the caller's store scope applied. In the B&M tree (dept:MERCH) each one takes the place of its status card, so it
opens the report. The view SQL is generated into deploy/30_bm_report_views.sql (snowflake_sql below).

After the first seed the catalog belongs to the report admins: a deploy refreshes the query definitions, adds missing
reports and files a report once; it never moves or renames what an admin has organised."""
import json
import uuid

PULSE = "V2RETAIL.PULSE"
TREE = "dept:MERCH"
FOLDER = "B&M reports"
FOLDER_DESC = "The B&M reports themselves, opened here: filter, sort, page and export. Live from Snowflake."
ZONES = ["Z1", "Z2", "Z3", "Z4", "Z5", "Z6"]
_HIER = "left join V2RETAIL.PULSE.V_STORE_HIERARCHY h on h.STORE_CODE = %s"


def P(label, type_="str", choices=None, default=None, max_len=60):
    p = {"type": type_, "label": label, "required": False, "max_len": max_len}
    if choices:
        p["choices"] = choices
    if default is not None:
        p["default"] = default
    return p


_COMMON_PARAMS = {"zone": P("Zone", choices=ZONES), "division": P("Division"), "major_category": P("Major category (contains)"),
                  "store": P("Store code", max_len=10)}
_COMMON_WHERE = ("(%(zone)s is null or ZONE_CODE = %(zone)s) and (%(division)s is null or upper(DIVISION) = upper(%(division)s))"
                 " and (%(major_category)s is null or MAJOR_CATEGORY ilike '%%' || %(major_category)s || '%%')"
                 " and (%(store)s is null or STORE_CODE = upper(%(store)s))")

REPORTS = [
    dict(
        code="bm-display-gap", name="Display GAP", ct="mer-display-gap",
        description="Store x major category: this month's display plan (fixtures and MBQ) against last month, LYSP and the "
                    "auto plan, with the gap flags. Pick Gaps only = Y to see only the flagged rows.",
        view="V_RPT_BM_DISPLAY_GAP",
        view_sql="""select g.REPORT_MONTH, g.STORE_CODE, g.STORE_NAME, g.STATE, coalesce(h.ZONE_CODE, 'Unmapped') ZONE_CODE, g.RDC,
       g.STORE_STATUS, g.SEGMENT, g.DIVISION, g.SUB_DIVISION, g.MAJOR_CATEGORY, g.SEASON, g.FIX_LAST_SEPT, g.FIX_CURRENT_OCT,
       g.FIX_LYSP, g.FIX_LYSP_AUTO, g.GAP_FIX_VS_SEPT, g.FLAG_FIX_VS_SEPT, g.GAP_FIX_VS_LYSP, g.FLAG_FIX_VS_LYSP,
       g.GAP_FIX_VS_AUTO, g.FLAG_FIX_VS_AUTO, g.MBQ_LAST_SEPT, g.MBQ_CURRENT_OCT, g.MBQ_LYSP, g.GAP_QTY_VS_LYSP,
       g.FLAG_QTY_VS_LYSP, greatest(coalesce(g.IS_GAP_FIX_VS_SEPT, 0), coalesce(g.IS_GAP_FIX_VS_LYSP, 0),
       coalesce(g.IS_GAP_FIX_VS_AUTO, 0), coalesce(g.IS_GAP_QTY_VS_LYSP, 0)) ANY_GAP, g.PLAN_MONTH, g.PRIOR_MONTH, g.BUILT_AT
from V2RETAIL.GOLD.ST_MJ_GAP g """ + _HIER % "g.STORE_CODE",
        sql="""select STORE_CODE "store", STORE_NAME "store name", ZONE_CODE "zone", STATE "state", RDC "rdc",
       STORE_STATUS "status", DIVISION "division", SUB_DIVISION "sub-division", MAJOR_CATEGORY "major category",
       SEASON "season", FIX_LAST_SEPT "fixtures prior month", FIX_CURRENT_OCT "fixtures plan month", FIX_LYSP "fixtures lysp",
       FIX_LYSP_AUTO "fixtures auto", GAP_FIX_VS_SEPT "gap vs prior month", FLAG_FIX_VS_SEPT "flag vs prior month",
       GAP_FIX_VS_LYSP "gap vs lysp", FLAG_FIX_VS_LYSP "flag vs lysp", GAP_FIX_VS_AUTO "gap vs auto",
       FLAG_FIX_VS_AUTO "flag vs auto", MBQ_LAST_SEPT "mbq prior month", MBQ_CURRENT_OCT "mbq plan month",
       MBQ_LYSP "mbq lysp", GAP_QTY_VS_LYSP "qty gap vs lysp", FLAG_QTY_VS_LYSP "qty flag vs lysp",
       PLAN_MONTH "plan month", PRIOR_MONTH "prior month"
from V2RETAIL.PULSE.V_RPT_BM_DISPLAY_GAP
where {scope} and REPORT_MONTH = coalesce(%(month)s, (select max(REPORT_MONTH) from V2RETAIL.PULSE.V_RPT_BM_DISPLAY_GAP))
  and (%(gaps_only)s is null or %(gaps_only)s = 'N' or ANY_GAP = 1) and """ + _COMMON_WHERE + """
order by abs(coalesce(GAP_FIX_VS_SEPT, 0)) desc, STORE_CODE, MAJOR_CATEGORY""",
        params={"month": P("Report month (YYYY-MM)", max_len=7), "gaps_only": P("Gaps only", choices=["Y", "N"], default="Y"),
                **_COMMON_PARAMS},
        scope="STORE_CODE"),
    dict(
        code="bm-sale-gap", name="Sale Gap", ct="mer-sale-gap",
        description="Store x major category: sale against budget over the last 7 days and 4 weeks, and against last year "
                    "(same period and season), with the gap flags.",
        view="V_RPT_BM_SALE_GAP",
        view_sql="""select g.ANCHOR_DT, g.ST_CD STORE_CODE, g.ST_NM STORE_NAME, coalesce(h.ZONE_CODE, 'Unmapped') ZONE_CODE, g.ST_STATUS,
       g.SEG, g.DIV DIVISION, g.FLR, g.SUB_DIV, g.MAJ_CAT MAJOR_CATEGORY, g.SSN, g.BK_FLD_STATUS, g.SALE_L7D, g.BGT_AVG_L7D,
       g.SALE_L4WK, g.BGT_AVG_L4WK, g.AVG_SALE_L7D, g.AVG_SALE_L7D_LYSP, g.AVG_SALE_L4WK, g.AVG_SALE_L4WK_LYSP,
       g.AVG_SALE_LYSP_SSN, g.AVG_SALE_SSN, g.GAP_L7D_ACH, g.FLAG_L7D_ACH, g.GAP_L4WK_ACH, g.FLAG_L4WK_ACH,
       g.GAP_L7D_VS_LYSP, g.FLAG_L7D_VS_LYSP, g.GAP_L4WK_VS_LYSP, g.FLAG_L4WK_VS_LYSP, g.GAP_SSN_VS_LYSP_SSN,
       g.FLAG_SSN_VS_LYSP_SSN
from V2RETAIL.GOLD.VW_SALE_GAP g """ + _HIER % "g.ST_CD",
        sql="""select STORE_CODE "store", STORE_NAME "store name", ZONE_CODE "zone", ST_STATUS "status", DIVISION "division",
       SUB_DIV "sub-division", MAJOR_CATEGORY "major category", SSN "season", SALE_L7D "sale l7d", BGT_AVG_L7D "budget avg l7d",
       GAP_L7D_ACH "l7d gap to budget", FLAG_L7D_ACH "l7d flag", SALE_L4WK "sale l4w", BGT_AVG_L4WK "budget avg l4w",
       GAP_L4WK_ACH "l4w gap to budget", FLAG_L4WK_ACH "l4w flag", AVG_SALE_L7D "avg sale l7d", AVG_SALE_L7D_LYSP "avg sale l7d lysp",
       GAP_L7D_VS_LYSP "l7d gap vs lysp", FLAG_L7D_VS_LYSP "l7d lysp flag", AVG_SALE_L4WK "avg sale l4w",
       AVG_SALE_L4WK_LYSP "avg sale l4w lysp", GAP_L4WK_VS_LYSP "l4w gap vs lysp", FLAG_L4WK_VS_LYSP "l4w lysp flag",
       AVG_SALE_SSN "avg sale season", AVG_SALE_LYSP_SSN "avg sale ly season", GAP_SSN_VS_LYSP_SSN "season gap vs ly",
       FLAG_SSN_VS_LYSP_SSN "season flag", ANCHOR_DT "as of"
from V2RETAIL.PULSE.V_RPT_BM_SALE_GAP
where {scope} and """ + _COMMON_WHERE + """
order by coalesce(GAP_L7D_ACH, 0), STORE_CODE, MAJOR_CATEGORY""",
        params=dict(_COMMON_PARAMS), scope="STORE_CODE"),
    dict(
        code="bm-ars-gap", name="ARS gap", ct="mer-ars-gap",
        description="The latest clean ARS allocation run, store x major category: MBQ, stock before and after the run, what "
                    "was allocated, and the shortfall and excess (10% band) left after it, with the DC stock left.",
        view="V_RPT_BM_ARS_GAP",
        view_sql="""with ses as (select SESSION_ID from V2RETAIL.BRONZE.ARS_GRID_MJ where SESSION_ID <> '' group by 1
             having count_if(coalesce(ADHOC, '') <> '') = 0 order by max(_LOADED_AT) desc limit 1),
a as (select * from V2RETAIL.BRONZE.ARS_GRID_MJ where SESSION_ID = (select SESSION_ID from ses)
      qualify row_number() over (partition by WERKS, MAJ_CAT order by _LOADED_AT desc) = 1)
select a.SESSION_ID, try_to_timestamp(a.APPROVED_AT) APPROVED_AT, a.WERKS STORE_CODE, a.STORE_NAME,
       coalesce(h.ZONE_CODE, 'Unmapped') ZONE_CODE, a.RDC, a.ST_STATUS, a.SEG, a.DIV DIVISION, a.SUB_DIV,
       a.MAJ_CAT MAJOR_CATEGORY, a.SSN, try_to_double(a.MBQ) MBQ, try_to_double(a.DISP_Q) DISP_Q,
       greatest(0, coalesce(try_to_double(a.STK_TTL), 0) - coalesce(try_to_double(a.ALC_Q), 0)) STOCK_BEFORE,
       coalesce(try_to_double(a.ALC_Q), 0) ALLOCATED, coalesce(try_to_double(a.STK_TTL), 0) STOCK_AFTER,
       greatest(0, coalesce(try_to_double(a.MBQ), 0) - coalesce(try_to_double(a.STK_TTL), 0)) SHORT_AFTER,
       greatest(0, coalesce(try_to_double(a.STK_TTL), 0) - 1.1 * coalesce(try_to_double(a.MBQ), 0)) EXCESS_AFTER,
       try_to_double(a.L_7_DAYS_SALE_Q) SALE_Q_L7D, try_to_double(a.MSA_CL_Q) DC_STOCK_LEFT, try_to_double(a.HOLD_ALC_Q) HOLD_Q
from a """ + _HIER % "a.WERKS",
        sql="""select STORE_CODE "store", STORE_NAME "store name", ZONE_CODE "zone", RDC "rdc", ST_STATUS "status", DIVISION "division",
       SUB_DIV "sub-division", MAJOR_CATEGORY "major category", SSN "season", MBQ "mbq", DISP_Q "display qty",
       STOCK_BEFORE "stock before run", ALLOCATED "allocated", STOCK_AFTER "stock after run", SHORT_AFTER "short after run",
       EXCESS_AFTER "excess after run", SALE_Q_L7D "sale qty l7d", DC_STOCK_LEFT "dc stock left", HOLD_Q "on hold",
       SESSION_ID "ars run", APPROVED_AT "approved at"
from V2RETAIL.PULSE.V_RPT_BM_ARS_GAP
where {scope} and (%(shortfall_only)s is null or %(shortfall_only)s = 'N' or SHORT_AFTER > 0) and """ + _COMMON_WHERE + """
order by SHORT_AFTER desc, STORE_CODE""",
        params={"shortfall_only": P("Shortfall only", choices=["Y", "N"], default="Y"), **_COMMON_PARAMS}, scope="STORE_CODE"),
    dict(
        code="bm-mrst-ttl-scm", name="MR-ST-TTL-SCM", ct="mer-mrst-ttl-scm",
        description="Store x major category plan and performance: fixtures, density, display stock, budget, sale (TD, MTD, "
                    "L7D, L4W, LY month), stock along the pipeline. The plan month is next month.",
        view="V_RPT_BM_MRST_TTL_SCM",
        view_sql="""select m.ANCHOR_DT, m.ST_CD STORE_CODE, m.ST_NM STORE_NAME, coalesce(h.ZONE_CODE, 'Unmapped') ZONE_CODE, m.CLUSTER,
       m.STATE, m.RDC_NM, m.ST_STATUS, m.SEG, m.DIV DIVISION, m.FLR, m.SUB_DIV, m.MAJ_CAT MAJOR_CATEGORY, m.SSN, m.PLAN_STATUS,
       m.ST_AREA, m.FINAL_ST_MODEL_FIX, m.PLAN_GND, m.PRV_FIX, m.LY_FIX, m.MIN_FIX, m.MAX_FIX, m.FINAL_DENSITY, m.DISPLAY_STK,
       m.BGT_SALE_V, m.BGT_SALE_Q, m.PLAN_BGT_SALE_V, m.BGT_DISP_Q, m.TD_SALE_V, m.MTD_SALE_V, m.MTD_GM_V, m.L7D_SALE_V,
       m.L4WK_SALE_V, m.LYMTH_SALE_V, m.LYTD_SALE_V, m.K0001_Q, m.K0001_V, m.HUBPRD_Q, m.DCPRD_Q, m.PTL_Q, m.TTL_STK_Q,
       m.TTL_STK_V, m.STOCK_ASOF
from V2RETAIL.GOLD.MRST_SCM_MJ m """ + _HIER % "m.ST_CD",
        sql="""select STORE_CODE "store", STORE_NAME "store name", ZONE_CODE "zone", CLUSTER "cluster", STATE "state", ST_STATUS "status",
       DIVISION "division", SUB_DIV "sub-division", MAJOR_CATEGORY "major category", SSN "season", PLAN_STATUS "plan status",
       ST_AREA "store area", FINAL_ST_MODEL_FIX "model fixtures", PLAN_GND "plan gondolas", PRV_FIX "prior fixtures",
       LY_FIX "ly fixtures", FINAL_DENSITY "density", DISPLAY_STK "display stock", BGT_SALE_V "budget sale",
       PLAN_BGT_SALE_V "plan budget sale", TD_SALE_V "sale td", MTD_SALE_V "sale mtd", MTD_GM_V "gm mtd", L7D_SALE_V "sale l7d",
       L4WK_SALE_V "sale l4w", LYMTH_SALE_V "ly month sale", K0001_Q "floor stock", HUBPRD_Q "hub prd", DCPRD_Q "dc prd",
       PTL_Q "ptl", TTL_STK_Q "total stock", TTL_STK_V "total stock value", STOCK_ASOF "stock as of", ANCHOR_DT "as of"
from V2RETAIL.PULSE.V_RPT_BM_MRST_TTL_SCM
where {scope} and """ + _COMMON_WHERE + """
order by MTD_SALE_V desc nulls last, STORE_CODE""",
        params=dict(_COMMON_PARAMS), scope="STORE_CODE"),
    dict(
        code="bm-mrdc-mj", name="MRDC MJ daily", ct="mer-mrdc-mj",
        description="Store x major category for the latest day: sale TD, MTD, L7D, L30D, YTD and LY same month, gross "
                    "margin, stock, this month's budget, achievement and the projected month.",
        view="V_RPT_BM_MRDC_MJ",
        view_sql="""select m.REPORT_DATE, m.STORE_CODE, coalesce(m.STORE_NAME, h.STORE_NAME) STORE_NAME, coalesce(h.ZONE_CODE, 'Unmapped') ZONE_CODE, coalesce(m.STATE, h.STATE) STATE, m.RDC,
       m.IS_NEW_STORE, m.SEGMENT, m.DIVISION, m.SUB_DIVISION, m.MAJOR_CATEGORY, m.SEASON_NAME, m.TD_SALE_VAL, m.MTD_SALE_VAL,
       m.L7D_SALE_VAL, m.L30D_SALE_VAL, m.YTD_SALE_VAL, m.LYSM_SALE_VAL, m.MTD_GM_VAL, m.STK_QTY, m.STK_VAL, m.BGT_SALE_V_TM,
       m.ACH_PCT_MTD, m.PROJ_FULL_MONTH_V
from V2RETAIL.GOLD.MART_MRDC_MJ_DAILY m """ + _HIER % "m.STORE_CODE" + """
where m.REPORT_DATE = (select max(REPORT_DATE) from V2RETAIL.GOLD.MART_MRDC_MJ_DAILY)""",
        sql="""select STORE_CODE "store", STORE_NAME "store name", ZONE_CODE "zone", STATE "state", RDC "rdc", IS_NEW_STORE "new store",
       DIVISION "division", SUB_DIVISION "sub-division", MAJOR_CATEGORY "major category", SEASON_NAME "season",
       TD_SALE_VAL "sale td", MTD_SALE_VAL "sale mtd", L7D_SALE_VAL "sale l7d", L30D_SALE_VAL "sale l30d", YTD_SALE_VAL "sale ytd",
       LYSM_SALE_VAL "ly same month", MTD_GM_VAL "gm mtd", STK_QTY "stock qty", STK_VAL "stock value",
       BGT_SALE_V_TM "budget this month", ACH_PCT_MTD "achievement mtd", PROJ_FULL_MONTH_V "projected month",
       REPORT_DATE "as of"
from V2RETAIL.PULSE.V_RPT_BM_MRDC_MJ
where {scope} and """ + _COMMON_WHERE + """
order by MTD_SALE_VAL desc nulls last, STORE_CODE""",
        params=dict(_COMMON_PARAMS), scope="STORE_CODE"),
    dict(
        code="bm-auto-sale", name="Auto Sale", ct="mer-auto-sale",
        description="The latest auto-sale run for the current month, store x major category: budget, MTD, the auto and final "
                    "sale plan, growth, achievement over L7D / MTD / L30D and floor stock.",
        view="V_RPT_BM_AUTO_SALE",
        view_sql="""select a.RUN_ID, a.MONTH_YEAR, a.MONTH_DATE, a.ST_CD STORE_CODE, a.ST_NM STORE_NAME,
       coalesce(h.ZONE_CODE, 'Unmapped') ZONE_CODE, a.STAT, a.SEG, a.DIV DIVISION, a.SUB_DIV, a.MAJ_CAT MAJOR_CATEGORY, a.SSN,
       a.ASP, a.DISP_QTY, a.BGT_V, a.BGT_Q, a.MTD_V, a.MTD_Q, a.AUTO_V, a.AUTO_Q, a.FINAL_V, a.FINAL_Q, a.GR_PCT, a.BGT_GR_PCT,
       a.FINAL_GR_PCT, a.L7_ACH_PCT, a.MTD_ACH_PCT, a.L30_ACH_PCT, a.L7_GM_PCT, a.STK_0001_Q, a.APPROVED_BY, a.APPROVED_AT
from V2RETAIL.AKS_GOLD.VW_AUTO_SALE_LIVE a """ + _HIER % "a.ST_CD" + """
where a.RUN_ID = (select max(RUN_ID) from V2RETAIL.AKS_GOLD.VW_AUTO_SALE_LIVE)
  and a.MONTH_DATE = (select min(MONTH_DATE) from V2RETAIL.AKS_GOLD.VW_AUTO_SALE_LIVE
                      where RUN_ID = (select max(RUN_ID) from V2RETAIL.AKS_GOLD.VW_AUTO_SALE_LIVE))""",
        sql="""select STORE_CODE "store", STORE_NAME "store name", ZONE_CODE "zone", STAT "status", DIVISION "division",
       SUB_DIV "sub-division", MAJOR_CATEGORY "major category", SSN "season", BGT_V "budget", MTD_V "mtd sale",
       AUTO_V "auto sale", FINAL_V "final sale", FINAL_Q "final qty", GR_PCT "growth pct", BGT_GR_PCT "budget growth pct",
       FINAL_GR_PCT "final growth pct", L7_ACH_PCT "l7d achievement", MTD_ACH_PCT "mtd achievement", L30_ACH_PCT "l30d achievement",
       L7_GM_PCT "l7d gm pct", DISP_QTY "display qty", STK_0001_Q "floor stock", ASP "asp", MONTH_YEAR "month",
       APPROVED_BY "approved by", APPROVED_AT "approved at"
from V2RETAIL.PULSE.V_RPT_BM_AUTO_SALE
where {scope} and """ + _COMMON_WHERE + """
order by FINAL_V desc nulls last, STORE_CODE""",
        params=dict(_COMMON_PARAMS), scope="STORE_CODE"),
    dict(
        code="bm-art-colour-stock", name="Article-colour stock", ct="mer-art-color",
        description="Latest day, every article-colour: stock in the stores (floor, backroom, discounted), at the DCs (MSA) "
                    "and in total, and how many plants carry it. Pick a major category to open it.",
        view="V_RPT_BM_ART_COLOUR_STOCK",
        view_sql="""select s.STOCK_DATE, s.GEN_ART, g.GEN_ART_DESC, g.SEG, g.DIV DIVISION, g.SUB_DIV, g.MAJ_CAT MAJOR_CATEGORY, g.MRP,
       s.COLOR_NAME, s.VARIANTS, s.STK_0001_Q, s.STK_0002_Q, s.STK_0006_Q, s.STORE_STK_Q, s.STORE_STK_V, s.MSA_STK_Q,
       s.MSA_STK_V, s.CO_STK_QTY, s.CO_STK_VAL, s.CARRIED_PLANTS
from V2RETAIL.GOLD.ART_COLOR_STOCK_SNAPSHOT s
left join V2RETAIL.SILVER.HERO_DIM_GENART g on g.GEN_ART_NUMBER = try_to_number(s.GEN_ART)
where s.STOCK_DATE = (select max(STOCK_DATE) from V2RETAIL.GOLD.ART_COLOR_STOCK_SNAPSHOT)""",
        sql="""select GEN_ART "article", GEN_ART_DESC "description", COLOR_NAME "colour", DIVISION "division",
       MAJOR_CATEGORY "major category", MRP "mrp", VARIANTS "variants", STK_0001_Q "store floor", STK_0002_Q "store backroom",
       STK_0006_Q "discounted", STORE_STK_Q "store stock", STORE_STK_V "store stock value", MSA_STK_Q "dc stock",
       MSA_STK_V "dc stock value", CO_STK_QTY "company stock", CO_STK_VAL "company stock value", CARRIED_PLANTS "plants carrying",
       STOCK_DATE "as of"
from V2RETAIL.PULSE.V_RPT_BM_ART_COLOUR_STOCK
where MAJOR_CATEGORY ilike '%%' || %(major_category)s || '%%' and (%(division)s is null or upper(DIVISION) = upper(%(division)s))
  and (%(article)s is null or GEN_ART = %(article)s)
order by CO_STK_VAL desc nulls last""",
        params={"major_category": dict(P("Major category (contains)"), required=True), "division": P("Division"),
                "article": P("Generic article", max_len=20)},
        scope=None),
]


# ---------------------------------------------------------------- Sourcing / SRM tree (the tree people call B&M)
_SRM_PARAMS = {"division": P("Division"), "vendor": P("Vendor (name or code contains)"), "major_category": P("Major category (contains)")}
_SRM_WHERE = ("(%(division)s is null or upper(DIVISION) = upper(%(division)s))"
              " and (%(vendor)s is null or VENDOR_NAME ilike '%%' || %(vendor)s || '%%' or VENDOR_CODE ilike '%%' || %(vendor)s || '%%')"
              " and (%(major_category)s is null or MAJOR_CATEGORY ilike '%%' || %(major_category)s || '%%')")
SRM_TREE = "dept:SOURCING"

REPORTS += [
    dict(
        code="srm-ppt-routing-pending", name="PPT PO Routing Pending", ct="srm-ppt-routing", tree=SRM_TREE,
        description="Every presentation (PPT) still moving through routing: vendor, category, the current pending step, steps "
                    "done, lot and process ageing. Latest SRM snapshot, oldest first.",
        view="V_RPT_SRM_PPT_PENDING",
        view_sql="""select SNAPSHOT_DATE, "PPT Number" PPT_NUMBER, "PPT Type" PPT_TYPE, "Vendor Code" VENDOR_CODE, "Vendor Name" VENDOR_NAME,
       SEGMENT, "Division" DIVISION, "Sub Division" SUB_DIVISION, "Major Category" MAJOR_CATEGORY, "PPT Rec. Date" PPT_REC_DATE,
       "Last Done" LAST_DONE, "Current Status" CURRENT_STATUS, "Steps Done" STEPS_DONE, CURRENT_PENDING_STEP_NO,
       "Lot Ageing (days)" LOT_AGEING, "Process Ageing (days)" PROCESS_AGEING, "Last Update" LAST_UPDATE, SRM_STATUS, PO_NUMBER
from V2RETAIL.GOLD.V_SRM_PPT_PENDING where IS_LATEST_SNAPSHOT""",
        sql="""select PPT_NUMBER "ppt number", PPT_TYPE "ppt type", VENDOR_CODE "vendor code", VENDOR_NAME "vendor", DIVISION "division",
       SUB_DIVISION "sub-division", MAJOR_CATEGORY "major category", PPT_REC_DATE "ppt received", CURRENT_STATUS "current status",
       CURRENT_PENDING_STEP_NO "pending step", STEPS_DONE "steps done", LAST_DONE "last done", LOT_AGEING "lot ageing (days)",
       PROCESS_AGEING "process ageing (days)", SRM_STATUS "srm status", PO_NUMBER "po number", LAST_UPDATE "last update",
       SNAPSHOT_DATE "as of"
from V2RETAIL.PULSE.V_RPT_SRM_PPT_PENDING
where (%(status)s is null or CURRENT_STATUS ilike '%%' || %(status)s || '%%') and """ + _SRM_WHERE + """
order by LOT_AGEING desc nulls last""",
        params={"status": P("Current status (contains)"), **_SRM_PARAMS}, scope=None),
    dict(
        code="srm-asn-ready", name="Ready for ASN", ct="srm-asn-ready", tree=SRM_TREE,
        description="POs ready for ASN: vendor and city, category, quantity and articles, SAP status and packing list, and how "
                    "many days each has been waiting. Latest SRM snapshot, longest waiting first.",
        view="V_RPT_SRM_ASN_READY",
        view_sql="""select SNAPSHOT_DATE, "PO Number" PO_NUMBER, PO_TYPE, "Vendor Code" VENDOR_CODE, "Vendor Name" VENDOR_NAME,
       "Vendor City" VENDOR_CITY, "Division" DIVISION, "Subdivision" SUB_DIVISION, "Major Category" MAJOR_CATEGORY,
       "Total Qty" TOTAL_QTY, "Article Count" ARTICLE_COUNT, "SAP Status" SAP_STATUS, "SAP Packing List Status" PACKING_LIST,
       "Created At" CREATED_AT, "RFQC Date" RFQC_DATE, DAYS_READY, READY_BUCKET
from V2RETAIL.GOLD.V_SRM_ASN_READY where IS_LATEST_SNAPSHOT""",
        sql="""select PO_NUMBER "po number", PO_TYPE "po type", VENDOR_CODE "vendor code", VENDOR_NAME "vendor", VENDOR_CITY "city",
       DIVISION "division", SUB_DIVISION "sub-division", MAJOR_CATEGORY "major category", TOTAL_QTY "total qty",
       ARTICLE_COUNT "articles", SAP_STATUS "sap status", PACKING_LIST "sap packing list", RFQC_DATE "rfqc date",
       DAYS_READY "days ready", READY_BUCKET "ageing bucket", CREATED_AT "created", SNAPSHOT_DATE "as of"
from V2RETAIL.PULSE.V_RPT_SRM_ASN_READY
where (%(bucket)s is null or READY_BUCKET ilike '%%' || %(bucket)s || '%%') and """ + _SRM_WHERE + """
order by DAYS_READY desc nulls last""",
        params={"bucket": P("Ageing bucket (contains)"), **_SRM_PARAMS}, scope=None),
    dict(
        code="srm-fabric-routing-pending", name="Fabric Routing Pending", ct="srm-fabric-routing", tree=SRM_TREE,
        description="Fabric routing still open: presentation, vendor, design and fabric, price, the current step and who it is "
                    "pending with, steps done and ageing. Latest SRM snapshot, oldest first.",
        view="V_RPT_SRM_FABRIC_ROUTING",
        view_sql="""select SNAPSHOT_DATE, PPT_NO, PPT_TYPE, VENDOR_CODE, VENDOR_NAME, CITY, DIVISION, SUB_DIVISION, MAJOR_CATEGORY, SSN,
       DESIGN_NO, FABRIC, PRICE, ROI, PRES_STATUS, ROUTING_STATUS, CURRENT_STEP, CURRENT_PENDING_STEP_NO, STEPS_DONE, TOTAL_STEPS,
       PCT_COMPLETE, PENDING_WITH, PENDING_PORTAL, CREATED_DATE, LAST_DONE_DATE, AGEING_DAYS, AGEING_BUCKET
from V2RETAIL.SILVER.SRM_FABRIC_ROUTING_LINE where IS_LATEST_SNAPSHOT""",
        sql="""select PPT_NO "ppt number", PPT_TYPE "ppt type", VENDOR_CODE "vendor code", VENDOR_NAME "vendor", CITY "city",
       DIVISION "division", SUB_DIVISION "sub-division", MAJOR_CATEGORY "major category", SSN "season", DESIGN_NO "design",
       FABRIC "fabric", PRICE "price", ROI "roi", CURRENT_STEP "current step", PENDING_WITH "pending with",
       PENDING_PORTAL "pending portal", STEPS_DONE "steps done", TOTAL_STEPS "total steps", PCT_COMPLETE "complete %%",
       ROUTING_STATUS "routing status", CREATED_DATE "created", LAST_DONE_DATE "last done", AGEING_DAYS "ageing (days)",
       AGEING_BUCKET "ageing bucket", SNAPSHOT_DATE "as of"
from V2RETAIL.PULSE.V_RPT_SRM_FABRIC_ROUTING
where (%(pending_with)s is null or PENDING_WITH ilike '%%' || %(pending_with)s || '%%') and """ + _SRM_WHERE + """
order by AGEING_DAYS desc nulls last""",
        params={"pending_with": P("Pending with (contains)"), **_SRM_PARAMS}, scope=None),
    dict(
        code="srm-routing-summary", name="Routing Summary", ct="srm-routing-summary", tree=SRM_TREE,
        description="PO routing by macro and micro stage and delivery month: number of POs and quantity, how many are over "
                    "their stage time, and the average days over. Latest SRM snapshot.",
        view="V_RPT_SRM_ROUTING_SUMMARY",
        view_sql="""select SNAPSHOT_DATE, MACRO_SEQ, MACRO_ROUTING, MICRO_ROUTING, MTH_KEY, MTH_LABEL, DELIVERY_MONTH, NO_OF_PO, PO_QTY,
       OVERAGEING_NO_OF_PO, OVERAGEING_PO_QTY, AVG_OVERAGEING_DAYS
from V2RETAIL.GOLD.V_SRM_ROUTING_SUMMARY where IS_LATEST_SNAPSHOT""",
        sql="""select MACRO_SEQ "seq", MACRO_ROUTING "macro stage", MICRO_ROUTING "micro stage", MTH_LABEL "delivery month",
       NO_OF_PO "pos", PO_QTY "po qty", OVERAGEING_NO_OF_PO "pos over time", OVERAGEING_PO_QTY "qty over time",
       AVG_OVERAGEING_DAYS "avg days over", SNAPSHOT_DATE "as of"
from V2RETAIL.PULSE.V_RPT_SRM_ROUTING_SUMMARY
where (%(stage)s is null or MACRO_ROUTING ilike '%%' || %(stage)s || '%%' or MICRO_ROUTING ilike '%%' || %(stage)s || '%%')
order by MACRO_SEQ, MICRO_ROUTING, DELIVERY_MONTH""",
        params={"stage": P("Stage (contains)")}, scope=None),
    dict(
        code="srm-grc-pend-po-wise", name="GRC-PEND PO-wise (T&A)", ct="srm-grc-pend-po", tree=SRM_TREE,
        description="Open POs not yet fully received (GRC): vendor, T&A owner, category and article, quantities and value "
                    "pending, delivery dates and extensions, the current T&A status and how long it has been there.",
        view="V_RPT_SRM_GRC_PEND",
        view_sql="""select SNAPSHOT_DATE, PO_NUMBER, CLR, SITE_CD, VND_CD VENDOR_CODE, VND_NM VENDOR_NAME, VND_CITY, ZONE_HEAD, TA_NAME,
       SSN, SEG, DIV DIVISION, SUB_DIV, MAJ_CAT MAJOR_CATEGORY, GEN_ART, GEN_ART_DESC, MRP, PO_TYPE, DOC_DT, CUR_DELIVERY_DATE,
       DELIVERY_COUNT, NO_OF_EXTENSION, PO_QTY, PO_VAL, PEND_PO_QTY, PEND_PO_VAL, GRC_QTY, PO_AGING, CRNT_DEL_AGING, CRNT_STAT,
       CRNT_STAT_AGEING, CRNT_STAT_OVER_AGING, ASN_REMARKS, STAGE_REACHED, LR_NO, TRANSPORTER_NAME
from V2RETAIL.PULSE.V_TNA_GRC_PEND_PO_WISE
where SNAPSHOT_DATE = (select max(SNAPSHOT_DATE) from V2RETAIL.PULSE.V_TNA_GRC_PEND_PO_WISE)""",
        sql="""select PO_NUMBER "po number", CLR "colour", SITE_CD "site", VENDOR_CODE "vendor code", VENDOR_NAME "vendor",
       VND_CITY "city", ZONE_HEAD "zone head", TA_NAME "t&a owner", SSN "season", DIVISION "division", SUB_DIV "sub-division",
       MAJOR_CATEGORY "major category", GEN_ART "article", GEN_ART_DESC "description", MRP "mrp", PO_TYPE "po type",
       DOC_DT "po date", CUR_DELIVERY_DATE "delivery date", NO_OF_EXTENSION "extensions", PO_QTY "po qty", PO_VAL "po value",
       GRC_QTY "grc qty", PEND_PO_QTY "pending qty", PEND_PO_VAL "pending value", PO_AGING "po ageing",
       CRNT_DEL_AGING "delivery ageing", CRNT_STAT "current status", CRNT_STAT_AGEING "days in status",
       CRNT_STAT_OVER_AGING "days over", STAGE_REACHED "stage", ASN_REMARKS "asn remarks", LR_NO "lr no",
       TRANSPORTER_NAME "transporter", SNAPSHOT_DATE "as of"
from V2RETAIL.PULSE.V_RPT_SRM_GRC_PEND
where (%(status)s is null or CRNT_STAT ilike '%%' || %(status)s || '%%')
  and (%(ta_owner)s is null or TA_NAME ilike '%%' || %(ta_owner)s || '%%') and """ + _SRM_WHERE + """
order by PEND_PO_VAL desc nulls last""",
        params={"status": P("Current status (contains)"), "ta_owner": P("T&A owner (contains)"), **_SRM_PARAMS}, scope=None),
]


def view_sql(r):
    return "create or replace view %s.%s\n  comment = 'V2Pulse B&M report: %s'\nas %s;" % (PULSE, r["view"], r["name"], r["view_sql"])


def snowflake_sql():
    out = ["-- V2Pulse B&M reports: curated views behind the table reports in the Merchandising & Planning tree.",
           "-- Generated from pulse/modules/bm_reports.py. Run as ACCOUNTADMIN on COMPUTE_WH. Safe to re-run.",
           "use role ACCOUNTADMIN;", "use warehouse COMPUTE_WH;", ""]
    for r in REPORTS:
        out += [view_sql(r), "grant select on view %s.%s to role PULSE_SVC_ROLE;" % (PULSE, r["view"]), ""]
    return "\n".join(out)


def seed(cur):
    """Refresh the query definitions, make sure each report exists, then put each report where its Control Tower status
    card is filed in the B&M tree (same folder, same position), so that clicking it opens the report itself. A report
    with no card is filed in 'Scheduled reports'. The separate folder an earlier version created is retired."""
    for r in REPORTS:
        cur.execute("insert into data.query_def (code, name, sql_template, params, scope_column, permission, cache_seconds, max_rows)"
                    " values (%s,%s,%s,%s,%s,'reports.view',1800,100000) on conflict (code) do update set name = excluded.name,"
                    " sql_template = excluded.sql_template, params = excluded.params, scope_column = excluded.scope_column",
                    (r["code"], r["name"], r["sql"], json.dumps(r["params"]), r["scope"]))
        tree = r.get("tree", TREE)
        cur.execute("insert into rpt.report (code, name, description, layer, owner_dept, owner_name, kind, query_code, tags)"
                    " values (%s,%s,%s,'department',%s,'Data Platform','table',%s,%s) on conflict (code) do nothing",
                    (r["code"], r["name"], r["description"], tree.split(":", 1)[1], r["code"], ["B&M", "live"]))
    for r in REPORTS:
        tree = r.get("tree", TREE)
        cur.execute("select 1 from iam.org_node where node_type = 'dept' and code = %s", (tree.split(":", 1)[1],))
        if not cur.fetchone():
            continue
        cur.execute("select id from rpt.report where code = %s", (r["code"],))
        rid = cur.fetchone()["id"]
        cur.execute("select id from rpt.report where code = %s", ("ct-" + r["ct"],))
        ct = cur.fetchone()
        if ct:
            # Swap the status card for the report wherever the card is filed in this tree.
            cur.execute("update nav.item i set report_id = %s from nav.folder f where f.id = i.folder_id and f.tree in (%s, 'custom:scheduledreports')"
                        " and f.section = 'reports' and i.report_id = %s and not exists (select 1 from nav.item j"
                        " where j.folder_id = i.folder_id and j.report_id = %s)", (rid, tree, ct["id"], rid))
        cur.execute("select 1 from nav.item i join nav.folder f on f.id = i.folder_id where f.tree in (%s, 'custom:scheduledreports') and f.section = 'reports'"
                    " and not f.is_archived and i.report_id = %s and lower(f.name) <> lower(%s)", (tree, rid, FOLDER))
        if not cur.fetchone():
            from .sched_folder import scheduled_folder
            fid = scheduled_folder(cur, tree, r["name"])
            cur.execute("insert into nav.item (folder_id, kind, report_id, sort_order) values (%s,'report',%s,"
                        " (select coalesce(max(sort_order), 0) + 10 from nav.item where folder_id = %s))", (fid, rid, fid))
    # Retire the separate folder of the first version, if it holds nothing but these reports.
    cur.execute("select id from nav.folder where section = 'reports' and tree = %s and lower(name) = lower(%s)"
                " and description = %s and not is_archived", (TREE, FOLDER, FOLDER_DESC))
    old = cur.fetchone()
    if old:
        cur.execute("select count(*) n from nav.item i left join rpt.report r on r.id = i.report_id where i.folder_id = %s"
                    " and (r.code is null or r.code not like 'bm-%%')", (old["id"],))
        if cur.fetchone()["n"] == 0:
            cur.execute("select count(*) n from nav.folder where parent_id = %s and not is_archived", (old["id"],))
            if cur.fetchone()["n"] == 0:
                cur.execute("delete from nav.item where folder_id = %s", (old["id"],))
                cur.execute("update nav.folder set is_archived = true, updated_at = now() where id = %s", (old["id"],))
