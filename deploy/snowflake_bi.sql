-- V2Pulse dynamic reports: PART D. Curated views in V2RETAIL.PULSE behind each dataset.
-- Generated from pulse/modules/bi_catalog.py (python -m pulse.cli bi-sql). Do not edit by hand.
-- Run as ACCOUNTADMIN or DATA_PLATFORM_ADMIN. Safe to re-run.

use role ACCOUNTADMIN;
use warehouse COMPUTE_WH;

-- Sales vs budget (sales_budget)
create or replace view V2RETAIL.PULSE.V_BI_SALES_BUDGET
  comment = 'V2Pulse dataset: Sales vs budget'
as select s.ANCHOR_DT, s.ST_CD, s.ST_NM, s.ST_STATUS, h.ZONE_CODE, h.REGION_CODE, h.CLUSTER_CODE, s.SEG, s.DIV, s.FLR, s.SUB_DIV, s.MAJ_CAT, s.SSN, s.SALE_TD, s.SALE_MTD, s.MTH_TGT, s.BGT_CURRENT_MONTH, s.BGT_UPCOMING_MONTH, s.BGT_AVG_L7D, s.BGT_AVG_L4WK, s.SALE_L7D, s.SALE_L4WK, s.AVG_SALE_L7D, s.AVG_SALE_L4WK, s.AVG_SALE_SSN, s.AVG_SALE_L7D_LYSP, s.AVG_SALE_L4WK_LYSP, s.AVG_SALE_LYSP_SSN, s.BUILT_AT from V2RETAIL.GOLD.ST_MJ_SALE s left join V2RETAIL.PULSE.V_STORE_HIERARCHY h on h.STORE_CODE = s.ST_CD;

-- DFS sale & stock (store x article) (dfs_sale)
create or replace view V2RETAIL.PULSE.V_BI_DFS_SALE
  comment = 'V2Pulse dataset: DFS sale & stock (store x article)'
as select f.DT, f.ST_CD, s.STORE_NAME as ST_NM, s.ZONE, s.REGION, s.RDC_CD, s.RDC_NAME as RDC_NM, f.SEG, a.DIV, a.SUB_DIV, f.MAJ_CAT, a.MC_CODE, a.MC_DESC, f.GEN_ART_NUMBER, f.GEN_ART_DESC, f.ARTICLE_NUMBER, a.ARTICLE_DESC, f.COLOR, f.SIZE_1, a.MRP, a.RNG_SEG as REG_SEG, a.VENDOR_CODE, a.VENDOR_NAME, a.MERGE_VENDOR_CODE, a.MERGE_VENDOR_NAME, a.M_FIT, a.M_FAB_MAIN_MVGR_1, a.M_FAB_MAIN_MVGR_2, a.M_WEAVE_01, a.M_WEAVE_02, a.M_FAB2, a.M_PATTERN, f.SALE_V, f.SALE_Q, f.GM_V, f.DISCOUNT_V from V2RETAIL.BRONZE.DAY_WISE_ST_VAR_SALE f left join V2RETAIL.GOLD.DIM_STORE_V2 s on s.STORE_CODE = f.ST_CD left join V2RETAIL.GOLD.V_VAR_ARTICLE_MASTER a on a.VAR_ARTICLE = f.ARTICLE_NUMBER;

-- RXL report (rxl_report)
create or replace view V2RETAIL.PULSE.V_BI_RXL_REPORT
  comment = 'V2Pulse dataset: RXL report'
as select s.SNAPSHOT_DATE, s.PERIOD_TYPE, s.IS_FINAL, s.STORE_CODE, s.STORE_NAME, coalesce(h.STATE, s.STATE) as STATE, h.ZONE_CODE, h.REGION_CODE, h.CLUSTER_CODE, h.CLUSTER_HEAD_NAME, s.AREA_SQFT, s.OPENING_DATE, case when s.BUDGET_PRORATED > 0 then 'BUDGETED' when s.CY_SALES > 0 then 'UNBUDGETED' else 'NO SALES' end as BUDGET_STATUS, s.CY_SALES, s.CY_GM, s.CY_QTY, s.LY_SALES, s.LY_GM, s.BUDGET_VALUE, coalesce(s.BUDGET_PRORATED, 0) as BUDGET_PRORATED, iff(s.BUDGET_PRORATED > 0, s.CY_SALES, 0) as BUDGETED_CY_SALES, iff(s.SSSG_PCT is not null, s.CY_SALES, 0) as SSSG_CY_SALES, iff(s.SSSG_PCT is not null, s.LY_SALES, 0) as SSSG_LY_SALES, s.CY_PSF, s.CY_GP_PSF, s.BUDGET_PSF, s.PRORATED_ACHIEVEMENT_PCT as ACH_PCT, s.BUDGET_ACHIEVEMENT_PCT as ACH_FULL_PCT, s.SSSG_PCT, s.GP_GROWTH_PCT, s.MTD_SSSG, s.YTD_SSSG, s.GM_MTD_SSSG, s.GM_YTD_SSSG, s.M5_PSF, s.M4_PSF, s.M3_PSF, s.M2_PSF, s.M1_PSF, s.CURRENT_PSF, s.DATA_AS_OF, s.SNAPSHOT_TAKEN_AT from V2RETAIL.GOLD.MIS_STORE_REPORT_SNAPSHOT s left join V2RETAIL.PULSE.V_STORE_HIERARCHY h on h.STORE_CODE = s.STORE_CODE;

grant select on all views in schema V2RETAIL.PULSE to role PULSE_SVC_ROLE;
