-- V2Pulse B&M reports: curated views behind the table reports in the Merchandising & Planning tree.
-- Generated from pulse/modules/bm_reports.py. Run as ACCOUNTADMIN on COMPUTE_WH. Safe to re-run.
use role ACCOUNTADMIN;
use warehouse COMPUTE_WH;

create or replace view V2RETAIL.PULSE.V_RPT_BM_DISPLAY_GAP
  comment = 'V2Pulse B&M report: Display GAP'
as select g.REPORT_MONTH, g.STORE_CODE, g.STORE_NAME, g.STATE, coalesce(h.ZONE_CODE, 'Unmapped') ZONE_CODE, g.RDC,
       g.STORE_STATUS, g.SEGMENT, g.DIVISION, g.SUB_DIVISION, g.MAJOR_CATEGORY, g.SEASON, g.FIX_LAST_SEPT, g.FIX_CURRENT_OCT,
       g.FIX_LYSP, g.FIX_LYSP_AUTO, g.GAP_FIX_VS_SEPT, g.FLAG_FIX_VS_SEPT, g.GAP_FIX_VS_LYSP, g.FLAG_FIX_VS_LYSP,
       g.GAP_FIX_VS_AUTO, g.FLAG_FIX_VS_AUTO, g.MBQ_LAST_SEPT, g.MBQ_CURRENT_OCT, g.MBQ_LYSP, g.GAP_QTY_VS_LYSP,
       g.FLAG_QTY_VS_LYSP, greatest(coalesce(g.IS_GAP_FIX_VS_SEPT, 0), coalesce(g.IS_GAP_FIX_VS_LYSP, 0),
       coalesce(g.IS_GAP_FIX_VS_AUTO, 0), coalesce(g.IS_GAP_QTY_VS_LYSP, 0)) ANY_GAP, g.PLAN_MONTH, g.PRIOR_MONTH, g.BUILT_AT
from V2RETAIL.GOLD.ST_MJ_GAP g left join V2RETAIL.PULSE.V_STORE_HIERARCHY h on h.STORE_CODE = g.STORE_CODE;
grant select on view V2RETAIL.PULSE.V_RPT_BM_DISPLAY_GAP to role PULSE_SVC_ROLE;

create or replace view V2RETAIL.PULSE.V_RPT_BM_SALE_GAP
  comment = 'V2Pulse B&M report: Sale Gap'
as select g.ANCHOR_DT, g.ST_CD STORE_CODE, g.ST_NM STORE_NAME, coalesce(h.ZONE_CODE, 'Unmapped') ZONE_CODE, g.ST_STATUS,
       g.SEG, g.DIV DIVISION, g.FLR, g.SUB_DIV, g.MAJ_CAT MAJOR_CATEGORY, g.SSN, g.BK_FLD_STATUS, g.SALE_L7D, g.BGT_AVG_L7D,
       g.SALE_L4WK, g.BGT_AVG_L4WK, g.AVG_SALE_L7D, g.AVG_SALE_L7D_LYSP, g.AVG_SALE_L4WK, g.AVG_SALE_L4WK_LYSP,
       g.AVG_SALE_LYSP_SSN, g.AVG_SALE_SSN, g.GAP_L7D_ACH, g.FLAG_L7D_ACH, g.GAP_L4WK_ACH, g.FLAG_L4WK_ACH,
       g.GAP_L7D_VS_LYSP, g.FLAG_L7D_VS_LYSP, g.GAP_L4WK_VS_LYSP, g.FLAG_L4WK_VS_LYSP, g.GAP_SSN_VS_LYSP_SSN,
       g.FLAG_SSN_VS_LYSP_SSN
from V2RETAIL.GOLD.VW_SALE_GAP g left join V2RETAIL.PULSE.V_STORE_HIERARCHY h on h.STORE_CODE = g.ST_CD;
grant select on view V2RETAIL.PULSE.V_RPT_BM_SALE_GAP to role PULSE_SVC_ROLE;

create or replace view V2RETAIL.PULSE.V_RPT_BM_ARS_GAP
  comment = 'V2Pulse B&M report: ARS gap'
as with ses as (select SESSION_ID from V2RETAIL.BRONZE.ARS_GRID_MJ where SESSION_ID <> '' group by 1
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
from a left join V2RETAIL.PULSE.V_STORE_HIERARCHY h on h.STORE_CODE = a.WERKS;
grant select on view V2RETAIL.PULSE.V_RPT_BM_ARS_GAP to role PULSE_SVC_ROLE;

create or replace view V2RETAIL.PULSE.V_RPT_BM_MRST_TTL_SCM
  comment = 'V2Pulse B&M report: MR-ST-TTL-SCM'
as select m.ANCHOR_DT, m.ST_CD STORE_CODE, m.ST_NM STORE_NAME, coalesce(h.ZONE_CODE, 'Unmapped') ZONE_CODE, m.CLUSTER,
       m.STATE, m.RDC_NM, m.ST_STATUS, m.SEG, m.DIV DIVISION, m.FLR, m.SUB_DIV, m.MAJ_CAT MAJOR_CATEGORY, m.SSN, m.PLAN_STATUS,
       m.ST_AREA, m.FINAL_ST_MODEL_FIX, m.PLAN_GND, m.PRV_FIX, m.LY_FIX, m.MIN_FIX, m.MAX_FIX, m.FINAL_DENSITY, m.DISPLAY_STK,
       m.BGT_SALE_V, m.BGT_SALE_Q, m.PLAN_BGT_SALE_V, m.BGT_DISP_Q, m.TD_SALE_V, m.MTD_SALE_V, m.MTD_GM_V, m.L7D_SALE_V,
       m.L4WK_SALE_V, m.LYMTH_SALE_V, m.LYTD_SALE_V, m.K0001_Q, m.K0001_V, m.HUBPRD_Q, m.DCPRD_Q, m.PTL_Q, m.TTL_STK_Q,
       m.TTL_STK_V, m.STOCK_ASOF
from V2RETAIL.GOLD.MRST_SCM_MJ m left join V2RETAIL.PULSE.V_STORE_HIERARCHY h on h.STORE_CODE = m.ST_CD;
grant select on view V2RETAIL.PULSE.V_RPT_BM_MRST_TTL_SCM to role PULSE_SVC_ROLE;

create or replace view V2RETAIL.PULSE.V_RPT_BM_MRDC_MJ
  comment = 'V2Pulse B&M report: MRDC MJ daily'
as select m.REPORT_DATE, m.STORE_CODE, coalesce(m.STORE_NAME, h.STORE_NAME) STORE_NAME, coalesce(h.ZONE_CODE, 'Unmapped') ZONE_CODE, coalesce(m.STATE, h.STATE) STATE, m.RDC,
       m.IS_NEW_STORE, m.SEGMENT, m.DIVISION, m.SUB_DIVISION, m.MAJOR_CATEGORY, m.SEASON_NAME, m.TD_SALE_VAL, m.MTD_SALE_VAL,
       m.L7D_SALE_VAL, m.L30D_SALE_VAL, m.YTD_SALE_VAL, m.LYSM_SALE_VAL, m.MTD_GM_VAL, m.STK_QTY, m.STK_VAL, m.BGT_SALE_V_TM,
       m.ACH_PCT_MTD, m.PROJ_FULL_MONTH_V
from V2RETAIL.GOLD.MART_MRDC_MJ_DAILY m left join V2RETAIL.PULSE.V_STORE_HIERARCHY h on h.STORE_CODE = m.STORE_CODE
where m.REPORT_DATE = (select max(REPORT_DATE) from V2RETAIL.GOLD.MART_MRDC_MJ_DAILY);
grant select on view V2RETAIL.PULSE.V_RPT_BM_MRDC_MJ to role PULSE_SVC_ROLE;

create or replace view V2RETAIL.PULSE.V_RPT_BM_AUTO_SALE
  comment = 'V2Pulse B&M report: Auto Sale'
as select a.RUN_ID, a.MONTH_YEAR, a.MONTH_DATE, a.ST_CD STORE_CODE, a.ST_NM STORE_NAME,
       coalesce(h.ZONE_CODE, 'Unmapped') ZONE_CODE, a.STAT, a.SEG, a.DIV DIVISION, a.SUB_DIV, a.MAJ_CAT MAJOR_CATEGORY, a.SSN,
       a.ASP, a.DISP_QTY, a.BGT_V, a.BGT_Q, a.MTD_V, a.MTD_Q, a.AUTO_V, a.AUTO_Q, a.FINAL_V, a.FINAL_Q, a.GR_PCT, a.BGT_GR_PCT,
       a.FINAL_GR_PCT, a.L7_ACH_PCT, a.MTD_ACH_PCT, a.L30_ACH_PCT, a.L7_GM_PCT, a.STK_0001_Q, a.APPROVED_BY, a.APPROVED_AT
from V2RETAIL.AKS_GOLD.VW_AUTO_SALE_LIVE a left join V2RETAIL.PULSE.V_STORE_HIERARCHY h on h.STORE_CODE = a.ST_CD
where a.RUN_ID = (select max(RUN_ID) from V2RETAIL.AKS_GOLD.VW_AUTO_SALE_LIVE)
  and a.MONTH_DATE = (select min(MONTH_DATE) from V2RETAIL.AKS_GOLD.VW_AUTO_SALE_LIVE
                      where RUN_ID = (select max(RUN_ID) from V2RETAIL.AKS_GOLD.VW_AUTO_SALE_LIVE));
grant select on view V2RETAIL.PULSE.V_RPT_BM_AUTO_SALE to role PULSE_SVC_ROLE;

create or replace view V2RETAIL.PULSE.V_RPT_BM_ART_COLOUR_STOCK
  comment = 'V2Pulse B&M report: Article-colour stock'
as select s.STOCK_DATE, s.GEN_ART, g.GEN_ART_DESC, g.SEG, g.DIV DIVISION, g.SUB_DIV, g.MAJ_CAT MAJOR_CATEGORY, g.MRP,
       s.COLOR_NAME, s.VARIANTS, s.STK_0001_Q, s.STK_0002_Q, s.STK_0006_Q, s.STORE_STK_Q, s.STORE_STK_V, s.MSA_STK_Q,
       s.MSA_STK_V, s.CO_STK_QTY, s.CO_STK_VAL, s.CARRIED_PLANTS
from V2RETAIL.GOLD.ART_COLOR_STOCK_SNAPSHOT s
left join V2RETAIL.SILVER.HERO_DIM_GENART g on g.GEN_ART_NUMBER = try_to_number(s.GEN_ART)
where s.STOCK_DATE = (select max(STOCK_DATE) from V2RETAIL.GOLD.ART_COLOR_STOCK_SNAPSHOT);
grant select on view V2RETAIL.PULSE.V_RPT_BM_ART_COLOUR_STOCK to role PULSE_SVC_ROLE;

create or replace view V2RETAIL.PULSE.V_RPT_SRM_PPT_PENDING
  comment = 'V2Pulse B&M report: PPT PO Routing Pending'
as select SNAPSHOT_DATE, "PPT Number" PPT_NUMBER, "PPT Type" PPT_TYPE, "Vendor Code" VENDOR_CODE, "Vendor Name" VENDOR_NAME,
       SEGMENT, "Division" DIVISION, "Sub Division" SUB_DIVISION, "Major Category" MAJOR_CATEGORY, "PPT Rec. Date" PPT_REC_DATE,
       "Last Done" LAST_DONE, "Current Status" CURRENT_STATUS, "Steps Done" STEPS_DONE, CURRENT_PENDING_STEP_NO,
       "Lot Ageing (days)" LOT_AGEING, "Process Ageing (days)" PROCESS_AGEING, "Last Update" LAST_UPDATE, SRM_STATUS, PO_NUMBER
from V2RETAIL.GOLD.V_SRM_PPT_PENDING where IS_LATEST_SNAPSHOT;
grant select on view V2RETAIL.PULSE.V_RPT_SRM_PPT_PENDING to role PULSE_SVC_ROLE;

create or replace view V2RETAIL.PULSE.V_RPT_SRM_ASN_READY
  comment = 'V2Pulse B&M report: Ready for ASN'
as select SNAPSHOT_DATE, "PO Number" PO_NUMBER, PO_TYPE, "Vendor Code" VENDOR_CODE, "Vendor Name" VENDOR_NAME,
       "Vendor City" VENDOR_CITY, "Division" DIVISION, "Subdivision" SUB_DIVISION, "Major Category" MAJOR_CATEGORY,
       "Total Qty" TOTAL_QTY, "Article Count" ARTICLE_COUNT, "SAP Status" SAP_STATUS, "SAP Packing List Status" PACKING_LIST,
       "Created At" CREATED_AT, "RFQC Date" RFQC_DATE, DAYS_READY, READY_BUCKET
from V2RETAIL.GOLD.V_SRM_ASN_READY where IS_LATEST_SNAPSHOT;
grant select on view V2RETAIL.PULSE.V_RPT_SRM_ASN_READY to role PULSE_SVC_ROLE;

create or replace view V2RETAIL.PULSE.V_RPT_SRM_FABRIC_ROUTING
  comment = 'V2Pulse B&M report: Fabric Routing Pending'
as select SNAPSHOT_DATE, PPT_NO, PPT_TYPE, VENDOR_CODE, VENDOR_NAME, CITY, DIVISION, SUB_DIVISION, MAJOR_CATEGORY, SSN,
       DESIGN_NO, FABRIC, PRICE, ROI, PRES_STATUS, ROUTING_STATUS, CURRENT_STEP, CURRENT_PENDING_STEP_NO, STEPS_DONE, TOTAL_STEPS,
       PCT_COMPLETE, PENDING_WITH, PENDING_PORTAL, CREATED_DATE, LAST_DONE_DATE, AGEING_DAYS, AGEING_BUCKET
from V2RETAIL.SILVER.SRM_FABRIC_ROUTING_LINE where IS_LATEST_SNAPSHOT;
grant select on view V2RETAIL.PULSE.V_RPT_SRM_FABRIC_ROUTING to role PULSE_SVC_ROLE;

create or replace view V2RETAIL.PULSE.V_RPT_SRM_ROUTING_SUMMARY
  comment = 'V2Pulse B&M report: Routing Summary'
as select SNAPSHOT_DATE, MACRO_SEQ, MACRO_ROUTING, MICRO_ROUTING, MTH_KEY, MTH_LABEL, DELIVERY_MONTH, NO_OF_PO, PO_QTY,
       OVERAGEING_NO_OF_PO, OVERAGEING_PO_QTY, AVG_OVERAGEING_DAYS
from V2RETAIL.GOLD.V_SRM_ROUTING_SUMMARY where IS_LATEST_SNAPSHOT;
grant select on view V2RETAIL.PULSE.V_RPT_SRM_ROUTING_SUMMARY to role PULSE_SVC_ROLE;

create or replace view V2RETAIL.PULSE.V_RPT_SRM_GRC_PEND
  comment = 'V2Pulse B&M report: GRC-PEND PO-wise (T&A)'
as select SNAPSHOT_DATE, PO_NUMBER, CLR, SITE_CD, VND_CD VENDOR_CODE, VND_NM VENDOR_NAME, VND_CITY, ZONE_HEAD, TA_NAME,
       SSN, SEG, DIV DIVISION, SUB_DIV, MAJ_CAT MAJOR_CATEGORY, GEN_ART, GEN_ART_DESC, MRP, PO_TYPE, DOC_DT, CUR_DELIVERY_DATE,
       DELIVERY_COUNT, NO_OF_EXTENSION, PO_QTY, PO_VAL, PEND_PO_QTY, PEND_PO_VAL, GRC_QTY, PO_AGING, CRNT_DEL_AGING, CRNT_STAT,
       CRNT_STAT_AGEING, CRNT_STAT_OVER_AGING, ASN_REMARKS, STAGE_REACHED, LR_NO, TRANSPORTER_NAME
from V2RETAIL.PULSE.V_TNA_GRC_PEND_PO_WISE
where SNAPSHOT_DATE = (select max(SNAPSHOT_DATE) from V2RETAIL.PULSE.V_TNA_GRC_PEND_PO_WISE);
grant select on view V2RETAIL.PULSE.V_RPT_SRM_GRC_PEND to role PULSE_SVC_ROLE;
