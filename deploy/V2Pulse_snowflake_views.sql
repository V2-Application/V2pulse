-- V2Pulse: all new Snowflake views (Masters = Part C, Dynamic reports = Part D).
-- Paste into a Snowsight worksheet and press Ctrl+Shift+Enter (Run All). Safe to re-run.

-- V2Pulse masters: PART C. Curated views in V2RETAIL.PULSE that the Masters library reads.
-- Generated from pulse/modules/masters_catalog.py (python -m pulse.cli masters-sql). Do not edit by hand.
-- Run as a role that can read the source tables and create views in V2RETAIL.PULSE
-- (DATA_PLATFORM_ADMIN or ACCOUNTADMIN). Safe to re-run.

use role ACCOUNTADMIN;
use warehouse COMPUTE_WH;

-- Store master (store)
create or replace view V2RETAIL.PULSE.V_MST_STORE
  comment = 'V2Pulse master: Store master'
as select
  STORE_CODE,
  STORE_NAME,
  STORE_FULL_NAME,
  STATE,
  ZONE,
  REGION,
  AREA,
  STORE_TYPE,
  STORE_FIXTURE,
  STATUS,
  IS_ACTIVE,
  IS_NEW_STORE,
  IS_UPCOMING,
  IS_CLOSED,
  OPEN_DATE,
  CLOSE_DATE,
  FLOOR_AREA_SQFT,
  ST_AREA_LABEL,
  FLOORS,
  AREA_PER_FIXTURE,
  AVG_PSF,
  IN_TRANSIT_DAYS,
  OLD_NEW_FLAG,
  FESTIVAL_STATUS,
  RDC_CD,
  RDC_NAME,
  HUB_CD,
  HUB_NAME,
  REF_STORE_CODE,
  REF_STORE_NAME,
  REF_GROUP_NEW,
  REF_GROUP_OLD,
  SALE_GROUP,
  BUILT_AT
from V2RETAIL.GOLD.DIM_STORE_V2;

-- Store geo-location (store-location)
create or replace view V2RETAIL.PULSE.V_MST_STORE_LOCATION
  comment = 'V2Pulse master: Store geo-location'
as select
  STORE_CODE,
  CITY,
  PIN_CODE,
  LATITUDE,
  LONGITUDE,
  UPDATED_AT
from V2RETAIL.RETAIL_HUB.STORE_LOCATION_MASTER;

-- Storage locations (SLOC) (storage-location)
create or replace view V2RETAIL.PULSE.V_MST_STORAGE_LOCATION
  comment = 'V2Pulse master: Storage locations (SLOC)'
as select
  STORE_CODE,
  STORAGE_LOC_CODE,
  STORAGE_LOC_NAME,
  DIVISION,
  EXTRACTED_AT
from V2RETAIL.SILVER.DIM_STORAGE_LOCATION;

-- RDC master (rdc)
create or replace view V2RETAIL.PULSE.V_MST_RDC
  comment = 'V2Pulse master: RDC master'
as select
  RDC_CD,
  RDC_NAME,
  RDC_CITY,
  REGION_BLOCK,
  TOTAL_STORES_IN_RDC,
  ACTIVE_STORES_IN_RDC,
  BUILT_AT
from V2RETAIL.GOLD.DIM_RDC;

-- Article master (article)
create or replace view V2RETAIL.PULSE.V_MST_ARTICLE
  comment = 'V2Pulse master: Article master'
as select
  VAR_ARTICLE,
  ARTICLE_DESC,
  GEN_ART_NUMBER,
  GEN_ART_DESC,
  MTART,
  MEINS,
  COLOR,
  SIZE_1,
  MC_CODE,
  MC_DESC,
  SEG,
  DIV,
  SUB_DIV,
  MAJ_CAT,
  SSN,
  MRP,
  RNG_SEG,
  VENDOR_CODE,
  VENDOR_NAME,
  VENDOR_DESIGN_NO,
  PAK_SZ,
  ARTICLE_STATUS,
  IS_ACTIVE,
  MSTAE_BLOCKED,
  CREATE_DATE,
  MODIFY_DATE
from V2RETAIL.GOLD.V_VAR_ARTICLE_MASTER;

-- Merchandise hierarchy (merch-hierarchy)
create or replace view V2RETAIL.PULSE.V_MST_MERCH_HIERARCHY
  comment = 'V2Pulse master: Merchandise hierarchy'
as select
  MATKL as MC_CODE,
  WGBEZ as MC_DESC,
  MAJ_CATEGORY_CODE,
  MAJ_CATEGORY,
  SUB_DIVISION_CODE,
  SUB_DIVISION,
  DIVISION_CODE,
  DIVISION,
  SEGMENT_CODE,
  SEGMENT
from V2RETAIL.SILVER.DIM_MERCH_HIERARCHY_CURRENT;

-- MC master (ZMC) (mc-master)
create or replace view V2RETAIL.PULSE.V_MST_MC
  comment = 'V2Pulse master: MC master (ZMC)'
as select
  MATKL as MC_CODE,
  MC_DESC,
  MC_DESC2,
  MTART as ARTICLE_TYPE,
  SEG_CD,
  SEG,
  DIV_CD,
  DIVISION,
  SUB_DIV_CD,
  SUB_DIVISION,
  MAJ_CAT_CD,
  MAJ_CAT,
  MAJ_CAT_STAT,
  SUB_CAT_CD,
  SUB_CAT_DESC,
  SEASON,
  STEUC as HSN_CODE,
  SAP_GL_CODE,
  ZPICK_STAT as PICK_STATUS,
  EXTRACT_DATE
from V2RETAIL.GOLD.ZMC_MASTER;

-- Major category & season (major-category)
create or replace view V2RETAIL.PULSE.V_MST_MAJOR_CATEGORY
  comment = 'V2Pulse master: Major category & season'
as select
  MAJOR_CATEGORY,
  SUB_DIVISION,
  DIVISION,
  SEGMENT,
  SEASON_CODE,
  SEASON_NAME,
  BUILT_AT
from V2RETAIL.GOLD.DIM_PRODUCT_HIERARCHY;

-- Season master (season)
create or replace view V2RETAIL.PULSE.V_MST_SEASON
  comment = 'V2Pulse master: Season master'
as select
  SEASON_CODE,
  SEASON_NAME,
  MAJCAT_COUNT,
  BUILT_AT
from V2RETAIL.GOLD.DIM_SEASON;

-- Colour master (colour)
create or replace view V2RETAIL.PULSE.V_MST_COLOUR
  comment = 'V2Pulse master: Colour master'
as select
  CHILD_COLOR,
  CHILD_COL_SHORT_NAME,
  FATHER_COLOR,
  FATHER_COLOR_SHORT_FORM,
  GRNAD_FATHER as GRANDFATHER_COLOR,
  HEX_CODE,
  SUGGESTED_TCX,
  SHADE,
  SOURCE_LOADED_AT
from V2RETAIL.SILVER.VW_SRM_COLOUR_MASTER;

-- Vendor master (vendor)
create or replace view V2RETAIL.PULSE.V_MST_VENDOR
  comment = 'V2Pulse master: Vendor master'
as select
  VENDOR_CODE,
  VENDOR_NAME,
  COMPANY_NAME,
  VENDOR_CITY,
  VENDOR_STATE,
  VENDOR_REGION,
  VENDOR_COUNTRY,
  VENDOR_PINCODE,
  ACCOUNT_GROUP_DESC,
  GSTIN,
  PAYMENT_TERMS,
  VENDOR_CREATED_ON,
  IS_MERGED,
  MERGE_VENDOR_CODE,
  MERGE_VENDOR_NAME,
  PURCHASING_BLOCK,
  POSTING_BLOCK,
  cast(DELETION_FLAG as varchar) as DELETION_FLAG
from V2RETAIL.SILVER.VENDOR_MASTER;

-- MVGR attribute families (mvgr-family)
create or replace view V2RETAIL.PULSE.V_MST_MVGR_FAMILY
  comment = 'V2Pulse master: MVGR attribute families'
as select
  MAJ_CAT_CODE,
  MAJ_CAT_NAME,
  DIV_CODE,
  CANON_MAJ_CAT_CODE,
  CANON_DIV_CODE,
  ROW_COUNT as VALUE_COUNT,
  MERCH_REVIEW_FLAG,
  RENAME_REASON,
  LOAD_TS
from V2RETAIL.GOLD.DIM_MVGR_FAMILY;

-- MVGR attribute values (mvgr-value)
create or replace view V2RETAIL.PULSE.V_MST_MVGR_VALUE
  comment = 'V2Pulse master: MVGR attribute values'
as select
  MVGR_KEY,
  MAJ_CAT_CODE,
  DIV_CODE,
  GRID_ID,
  FG_CODE,
  MVGR_VALUE,
  FULL_FORM,
  SHORT_CODE,
  LABEL_EN,
  LABEL_HI,
  STATUS,
  LIFECYCLE,
  EFFECTIVE_FROM,
  EFFECTIVE_TO,
  VERSION_NO,
  UNIT,
  MIN_VAL,
  MAX_VAL,
  FIBER_1,
  PCT_1,
  FIBER_2,
  PCT_2,
  FIBER_3,
  PCT_3,
  COMPOSITION_FINISH,
  APPROVED_BY,
  APPROVED_AT,
  LOAD_TS
from V2RETAIL.GOLD.DIM_MVGR_VALUE;

-- Segment MRP bands (mrp-band)
create or replace view V2RETAIL.PULSE.V_MST_MRP_BAND
  comment = 'V2Pulse master: Segment MRP bands'
as select
  MAJ_CATEGORY,
  SEG,
  DIV,
  SUB_DIV,
  SSN,
  BAND,
  BAND_SEQ,
  MIN_VAL,
  MAX_VAL,
  IS_OPEN_MAX,
  LOADED_AT,
  LOADED_BY
from V2RETAIL.MERCH_MASTER.SEG_MRP_BAND_MASTER;

-- Fabric base articles (fabric-base-article)
create or replace view V2RETAIL.PULSE.V_MST_FABRIC_BASE_ARTICLE
  comment = 'V2Pulse master: Fabric base articles'
as select
  BASE_FABRIC_ARTICLE_NO,
  BASE_FABRIC_ARTICLE_DESC,
  DIV,
  YARN,
  FABRIC_MAJ_CAT,
  MC_CD,
  MC_DESC,
  BASE_FABRIC_REF_ARTICLE,
  BASE_FABRIC_REF_ARTICLE_DESC,
  FAB_MAIN_MVGR_2,
  LOADED_AT,
  LOADED_BY
from V2RETAIL.MERCH_MASTER.FABRIC_BASE_ARTICLE_MASTER;

-- Bin capacity (bin-capacity)
create or replace view V2RETAIL.PULSE.V_MST_BIN_CAPACITY
  comment = 'V2Pulse master: Bin capacity'
as select
  MAJ_CAT,
  BIN_CAP,
  BIN_CAP_DC_TEAM
from V2RETAIL.GOLD.MASTER_BIN_CAPACITY;

-- Picker master (picker)
create or replace view V2RETAIL.PULSE.V_MST_PICKER
  comment = 'V2Pulse master: Picker master'
as select
  PICKER_ID,
  PICKER_NM as PICKER_NAME,
  DC,
  EMP_CODE,
  IS_HUMAN,
  FIRST_SEEN_ON,
  LAST_SEEN_ON,
  UPDATED_AT
from V2RETAIL.MIS_REPORTS.WM_PICKER_MASTER;

-- PTL zones (ptl-zone)
create or replace view V2RETAIL.PULSE.V_MST_PTL_ZONE
  comment = 'V2Pulse master: PTL zones'
as select
  ZONE_CD,
  ZONE_NO,
  ZONE_NM as ZONE_NAME,
  STORAGE_TYPE,
  BIN_PATTERN,
  IS_MSA,
  REMARK,
  UPDATED_AT
from V2RETAIL.MIS_REPORTS.WM_PTL_ZONE_MASTER;

-- Allocation SLOC mapping (alloc-sloc)
create or replace view V2RETAIL.PULSE.V_MST_ALLOC_SLOC
  comment = 'V2Pulse master: Allocation SLOC mapping'
as select
  ST_CD as STORE_CODE,
  ALLOCATION_PURPOSE,
  SLOC_RAW,
  SLOC_DESC_ALLOC as SLOC_DESC,
  SLOC_NORM
from V2RETAIL.SILVER.ALLOC_SLOC_MASTER;

-- Store transit days (transit-days)
create or replace view V2RETAIL.PULSE.V_MST_TRANSIT_DAYS
  comment = 'V2Pulse master: Store transit days'
as select
  ST_CD as STORE_CODE,
  ST_NM as STORE_NAME,
  ST_FULL_NM as STORE_FULL_NAME,
  ST_TYP as STORE_TYPE,
  IN_TR_DAYS as IN_TRANSIT_DAYS,
  EXCESS_DAYS,
  ST_STAT as STORE_STATUS,
  STATE,
  UPDATED_AT
from V2RETAIL.MIS_REPORTS.INTRANSIT_PLANT_MASTER;

-- Store reporting lines (store-assignment)
create or replace view V2RETAIL.PULSE.V_MST_STORE_ASSIGNMENT
  comment = 'V2Pulse master: Store reporting lines'
as select
  STORE_CODE,
  ZM_USER_ID,
  RM_USER_ID,
  CM_USER_ID,
  UPDATED_AT,
  UPDATED_BY
from V2RETAIL.RETAIL_HUB.STORE_ASSIGNMENT_MASTER;

-- SOMPS processes (somps-process)
create or replace view V2RETAIL.PULSE.V_MST_SOMPS_PROCESS
  comment = 'V2Pulse master: SOMPS processes'
as select
  NAME as PROCESS_NAME,
  SUB_PROCESS,
  CATEGORY,
  BENCHMARK_UNIT,
  EFFECTIVE_DATE,
  ACTIVE,
  UPDATED_AT,
  UPDATED_BY
from V2RETAIL.RETAIL_HUB.SOMPS_PROCESS_MASTER;

-- SOMPS benchmarks (somps-benchmark)
create or replace view V2RETAIL.PULSE.V_MST_SOMPS_BENCHMARK
  comment = 'V2Pulse master: SOMPS benchmarks'
as select
  p.NAME as PROCESS_NAME,
  p.SUB_PROCESS as SUB_PROCESS,
  b.VALUE as VALUE,
  b.UNIT as UNIT,
  b.EFFECTIVE_DATE as EFFECTIVE_DATE,
  b.NOTES as NOTES,
  b.UPDATED_AT as UPDATED_AT
from V2RETAIL.RETAIL_HUB.SOMPS_BENCHMARK_MASTER b left join V2RETAIL.RETAIL_HUB.SOMPS_PROCESS_MASTER p on p.ID = b.PROCESS_ID;

-- FA sets (fa-set)
create or replace view V2RETAIL.PULSE.V_MST_FA_SET
  comment = 'V2Pulse master: FA sets'
as select
  FA_SET,
  STATE,
  SR_NO
from V2RETAIL.NSO_MARKETING.FA_SET_MASTER;

-- FA tracker (fa-tracker)
create or replace view V2RETAIL.PULSE.V_MST_FA_TRACKER
  comment = 'V2Pulse master: FA tracker'
as select
  STORE_CODE,
  STORE_NAME,
  STATE,
  OPENING_DATE,
  STORE_STATUS,
  FA_STATUS,
  FA_SET,
  FROM_SITE,
  CURRENT_SITE,
  RECEIVED_DATE,
  START_DATE,
  END_DATE,
  AT_STORE_DAYS,
  AGEING_DAYS,
  VENDOR_COST,
  NET_LOSS,
  NET_SAVING,
  ACTION,
  ROUTED_STATUS,
  UPDATED_AT
from V2RETAIL.NSO_MARKETING.FA_MASTER_TRACKER;

-- SRM vendors (srm-vendor)
create or replace view V2RETAIL.PULSE.V_MST_SRM_VENDOR
  comment = 'V2Pulse master: SRM vendors'
as select
  VENDOR_CODE,
  VENDOR_NAME,
  COMPANY_NAME,
  VENDOR_CITY,
  VENDOR_REGION,
  IS_MERGED,
  MERGE_VENDOR_CODE,
  MERGE_VENDOR_NAME,
  MERGE_VENDOR_CITY,
  SOURCE_LOADED_AT_UTC
from V2RETAIL.SILVER.VW_SRM_VENDOR_MASTER;

-- SRM articles (srm-article)
create or replace view V2RETAIL.PULSE.V_MST_SRM_ARTICLE
  comment = 'V2Pulse master: SRM articles'
as select
  GEN_ART as GENERIC_ARTICLE,
  COLOR,
  VENDOR_CODE,
  VENDOR_NAME,
  DIVISION,
  SUBDIVISION as SUB_DIVISION,
  MAJOR_CATEGORY,
  BRAND,
  MRP,
  UNIT,
  RNG_SEG as RANGE_SEGMENT,
  NOA
from V2RETAIL.GOLD.SRM_ARTICLE_MASTER;

-- SAP RFC interfaces (sap-rfc)
create or replace view V2RETAIL.PULSE.V_MST_SAP_RFC
  comment = 'V2Pulse master: SAP RFC interfaces'
as select
  RFC_CODE,
  DISPLAY_NAME,
  RFC_FUNCTION_NAME,
  DESCRIPTION,
  DEPARTMENT,
  SUB_DEPARTMENT,
  SAP_MODULE,
  SOURCE_SYSTEM,
  TARGET_SYSTEM,
  TARGET_SCHEMA,
  TARGET_TABLE,
  EXECUTION_TYPE,
  SCHEDULE_DESC,
  STATUS,
  OWNER,
  LOAD_MODE,
  WRITE_MODE,
  UPDATED_DT
from V2RETAIL.GOLD.RFC_MASTER
where coalesce(IS_DELETED, false) = false;

-- PULSE_SVC_ROLE already has select on future views in V2RETAIL.PULSE; this covers views that existed.
grant select on all views in schema V2RETAIL.PULSE to role PULSE_SVC_ROLE;

-- V2Pulse dynamic reports: PART D. Curated views in V2RETAIL.PULSE behind each dataset.
-- Generated from pulse/modules/bi_catalog.py (python -m pulse.cli bi-sql). Do not edit by hand.
-- Run as ACCOUNTADMIN or DATA_PLATFORM_ADMIN. Safe to re-run.


-- Sales vs budget (sales_budget)
create or replace view V2RETAIL.PULSE.V_BI_SALES_BUDGET
  comment = 'V2Pulse dataset: Sales vs budget'
as select s.ANCHOR_DT, s.ST_CD, s.ST_NM, s.ST_STATUS, h.ZONE_CODE, h.REGION_CODE, h.CLUSTER_CODE, s.SEG, s.DIV, s.FLR, s.SUB_DIV, s.MAJ_CAT, s.SSN, s.SALE_TD, s.SALE_MTD, s.MTH_TGT, s.BGT_CURRENT_MONTH, s.BGT_UPCOMING_MONTH, s.BGT_AVG_L7D, s.BGT_AVG_L4WK, s.SALE_L7D, s.SALE_L4WK, s.AVG_SALE_L7D, s.AVG_SALE_L4WK, s.AVG_SALE_SSN, s.AVG_SALE_L7D_LYSP, s.AVG_SALE_L4WK_LYSP, s.AVG_SALE_LYSP_SSN, s.BUILT_AT from V2RETAIL.GOLD.ST_MJ_SALE s left join V2RETAIL.PULSE.V_STORE_HIERARCHY h on h.STORE_CODE = s.ST_CD;

grant select on all views in schema V2RETAIL.PULSE to role PULSE_SVC_ROLE;
