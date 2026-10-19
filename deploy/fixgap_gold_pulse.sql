-- ST INSTALLED VS PLANNED FIXTURE GAP: typed GOLD tables from RETAIL_HUB.BRZ_FIXGAP_* and the plain PULSE views on them.
-- Source: ST INSTALLED VS PLANNED FIXTURE GAP.xlsx (file dated 2026-09-15), loaded by load_fixgap.py. Run AFTER the load.
-- Run as DATA_PLATFORM_ADMIN on WH_SAP_LOAD. Re-runnable (rebuilds from bronze). A check at the end must show matching totals.

create or replace table V2RETAIL.GOLD.RPT_FIXGAP_ST_ONELINER comment = 'Fixture GAP (installed vs planned): ST_ONELINER, typed from RETAIL_HUB.BRZ_FIXGAP_ST_ONELINER' as
select upper(trim(ST_CD)) AS ST_CD,
  nullif(trim(STORE), '') AS STORE_NAME,
  nullif(trim(ZONE), '') AS ZONE,
  nullif(trim(REGION), '') AS REGION,
  nullif(trim(CLUSTER), '') AS CLUSTER,
  nullif(trim(ST_STAT), '') AS ST_STAT,
  try_to_date(nullif(trim(OP_DATE), '')) AS OP_DATE,
  try_to_double(nullif(trim(ST_FIX), '')) AS ST_FIX,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__OCT_BGT), '')) AS OCT_BGT,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__FRESH_FIX), '')) AS FRESH_FIX,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__BS_FIX), '')) AS BS_FIX,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__BS_FIX_CONT), '')) AS BS_FIX_CONT,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__C_ART_FIX), '')) AS C_ART_FIX,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__TTL_C_ART_CONTB), '')) AS C_ART_FIX_CONT,
  try_to_double(nullif(trim(SEPT_BGT__PURCHASE), '')) AS SEPT_BGT_PURCHASE,
  try_to_double(nullif(trim(SEPT_BGT__ALLOCATION), '')) AS SEPT_BGT_ALLOCATION,
  try_to_double(nullif(trim(DIFF_ST_FLR_FIX__PURCHASE_INSTD), '')) AS DIFF_PURCHASE,
  try_to_double(nullif(trim(DIFF_ST_FLR_FIX__ALLOCATION_BGT), '')) AS DIFF_ALLOCATION,
  to_date('2026-09-15') SOURCE_FILE_DATE, current_timestamp() LOADED_AT
from V2RETAIL.RETAIL_HUB.BRZ_FIXGAP_ST_ONELINER
where nullif(trim(ST_CD), '') is not null;

create or replace table V2RETAIL.GOLD.RPT_FIXGAP_ST_DIV comment = 'Fixture GAP (installed vs planned): ST_DIV, typed from RETAIL_HUB.BRZ_FIXGAP_ST_DIV' as
select upper(trim(ST_CD)) AS ST_CD,
  nullif(trim(STORE), '') AS STORE_NAME,
  nullif(trim(ZONE), '') AS ZONE,
  nullif(trim(REGION), '') AS REGION,
  nullif(trim(CLUSTER), '') AS CLUSTER,
  nullif(trim(ST_STAT), '') AS ST_STAT,
  try_to_date(nullif(trim(OP_DATE), '')) AS OP_DATE,
  nullif(trim(COMBI), '') AS COMBI_KEY,
  nullif(trim(DIV), '') AS DIV,
  try_to_double(nullif(trim(ST_FIX), '')) AS ST_FIX,
  try_to_double(nullif(trim(OCT_BGT), '')) AS OCT_BGT,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__FRESH_FIX), '')) AS FRESH_FIX,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__BS_FIX), '')) AS BS_FIX,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__BS_FIX_CONT), '')) AS BS_FIX_CONT,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__C_ART_FIX), '')) AS C_ART_FIX,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__C_ART_FIX_CONT), '')) AS C_ART_FIX_CONT,
  try_to_double(nullif(trim(SEPT_BGT__PURCHASE), '')) AS SEPT_BGT_PURCHASE,
  try_to_double(nullif(trim(SEPT_BGT__ALLOCATION), '')) AS SEPT_BGT_ALLOCATION,
  try_to_double(nullif(trim(DIFF_BGT_VS_ST_FI_FIX__PURCHASE), '')) AS DIFF_PURCHASE,
  try_to_double(nullif(trim(DIFF_BGT_VS_ST_FI_FIX__ALLOCATION), '')) AS DIFF_ALLOCATION,
  to_date('2026-09-15') SOURCE_FILE_DATE, current_timestamp() LOADED_AT
from V2RETAIL.RETAIL_HUB.BRZ_FIXGAP_ST_DIV
where nullif(trim(ST_CD), '') is not null;

create or replace table V2RETAIL.GOLD.RPT_FIXGAP_ST_FLR_SUBDIV comment = 'Fixture GAP (installed vs planned): ST_FLR_SUBDIV, typed from RETAIL_HUB.BRZ_FIXGAP_ST_FLR_SUBDIV' as
select upper(trim(ST_CD)) AS ST_CD,
  nullif(trim(STORE), '') AS STORE_NAME,
  nullif(trim(ZONE), '') AS ZONE,
  nullif(trim(REGION), '') AS REGION,
  nullif(trim(CLUSTER), '') AS CLUSTER,
  nullif(trim(ST_STAT), '') AS ST_STAT,
  try_to_date(nullif(trim(OP_DATE), '')) AS OP_DATE,
  nullif(trim(COMBI), '') AS COMBI_KEY,
  nullif(trim(FLR), '') AS FLR,
  nullif(trim(DIV), '') AS DIV,
  nullif(trim(SUB_DIV), '') AS SUB_DIV,
  try_to_double(nullif(trim(ST_FIX), '')) AS ST_FIX,
  try_to_double(nullif(trim(ST_FL_FIX), '')) AS ST_FL_FIX,
  try_to_double(nullif(trim(OCT_BGT), '')) AS OCT_BGT,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__FRESH_FIX), '')) AS FRESH_FIX,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__BS_FIX), '')) AS BS_FIX,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__BS_FIX_CONT), '')) AS BS_FIX_CONT,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__C_ART_FIX), '')) AS C_ART_FIX,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__C_ART_FIX_CONT), '')) AS C_ART_FIX_CONT,
  try_to_double(nullif(trim(SEPT_BGT__PURCHASE), '')) AS SEPT_BGT_PURCHASE,
  try_to_double(nullif(trim(SEPT_BGT__ALLOCATION), '')) AS SEPT_BGT_ALLOCATION,
  try_to_double(nullif(trim(DIFF_BGT_VS_ST_FI_FIX__PURCHASE), '')) AS DIFF_PURCHASE,
  try_to_double(nullif(trim(DIFF_BGT_VS_ST_FI_FIX__ALLOCATION), '')) AS DIFF_ALLOCATION,
  to_date('2026-09-15') SOURCE_FILE_DATE, current_timestamp() LOADED_AT
from V2RETAIL.RETAIL_HUB.BRZ_FIXGAP_ST_FLR_SUBDIV
where nullif(trim(ST_CD), '') is not null;

create or replace table V2RETAIL.GOLD.RPT_FIXGAP_ST_FLR comment = 'Fixture GAP (installed vs planned): ST_FLR, typed from RETAIL_HUB.BRZ_FIXGAP_ST_FLR' as
select upper(trim(ST_CD)) AS ST_CD,
  nullif(trim(STORE), '') AS STORE_NAME,
  nullif(trim(ZONE), '') AS ZONE,
  nullif(trim(REGION), '') AS REGION,
  nullif(trim(CLUSTER), '') AS CLUSTER,
  nullif(trim(ST_STAT), '') AS ST_STAT,
  try_to_date(nullif(trim(OP_DATE), '')) AS OP_DATE,
  nullif(trim(LAYOUT_BLANK), '') AS LAYOUT_BLANK,
  nullif(trim(LAYOUT_WORKING), '') AS LAYOUT_WORKING,
  nullif(trim(COMBI), '') AS COMBI_KEY,
  nullif(trim(FL), '') AS FLR,
  try_to_double(nullif(trim(ST_FIX), '')) AS ST_FIX,
  try_to_double(nullif(trim(ST_FL_FIX), '')) AS ST_FL_FIX,
  try_to_double(nullif(trim(OCT_BGT), '')) AS OCT_BGT,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__FRESH_FIX), '')) AS FRESH_FIX,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__BS_FIX), '')) AS BS_FIX,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__BS_FIX_CONT), '')) AS BS_FIX_CONT,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__C_ART_FIX), '')) AS C_ART_FIX,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__C_ART_FIX_CONT), '')) AS C_ART_FIX_CONT,
  try_to_double(nullif(trim(SEPT_BGT__PURCHASE), '')) AS SEPT_BGT_PURCHASE,
  try_to_double(nullif(trim(SEPT_BGT__ALLOCATION), '')) AS SEPT_BGT_ALLOCATION,
  try_to_double(nullif(trim(DIFF_ST_FLR_FIX__PURCHASE_INSTD), '')) AS DIFF_PURCHASE,
  try_to_double(nullif(trim(DIFF_ST_FLR_FIX__ALLOCATION_BGT), '')) AS DIFF_ALLOCATION,
  to_date('2026-09-15') SOURCE_FILE_DATE, current_timestamp() LOADED_AT
from V2RETAIL.RETAIL_HUB.BRZ_FIXGAP_ST_FLR
where nullif(trim(ST_CD), '') is not null;

create or replace table V2RETAIL.GOLD.RPT_FIXGAP_ST_MAJCAT comment = 'Fixture GAP (installed vs planned): ST_MAJCAT, typed from RETAIL_HUB.BRZ_FIXGAP_ST_MAJCAT' as
select upper(trim(ST_CD)) AS ST_CD,
  nullif(trim(STORE), '') AS STORE_NAME,
  nullif(trim(ZONE), '') AS ZONE,
  nullif(trim(REGION), '') AS REGION,
  nullif(trim(CLUSTER), '') AS CLUSTER,
  nullif(trim(ST_STAT), '') AS ST_STAT,
  try_to_date(nullif(trim(OP_DATE), '')) AS OP_DATE,
  nullif(trim(COMBI__ST_CD_DIV), '') AS COMBI_ST_DIV,
  nullif(trim(COMBI__ST_CD_SUB_DIV), '') AS COMBI_ST_SUBDIV,
  nullif(trim(COMBI__ST_CD_MAJCAT), '') AS COMBI_ST_MAJCAT,
  nullif(trim(COMBI__ST_CD_FLR), '') AS COMBI_ST_FLR,
  nullif(trim(FL), '') AS FLR,
  nullif(trim(SSN), '') AS SSN,
  nullif(trim(DIV), '') AS DIV,
  nullif(trim(SUB_DIV), '') AS SUB_DIV,
  nullif(trim(MAJCAT), '') AS MAJCAT,
  try_to_double(nullif(trim(ST_FIX), '')) AS ST_FIX,
  try_to_double(nullif(trim(ST_FL_FIX), '')) AS ST_FL_FIX,
  try_to_double(nullif(trim(OCT_BGT), '')) AS OCT_BGT,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__FRESH_FIX), '')) AS FRESH_FIX,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__BS_FIX), '')) AS BS_FIX,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__BS_FIX_1), '')) AS BS_FIX_CONT,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__C_ART_FIX), '')) AS C_ART_FIX,
  try_to_double(nullif(trim(FIX_BREAKUP_AS_PER_STOCK__TTL_C_ART_CONTB), '')) AS C_ART_FIX_CONT,
  try_to_double(nullif(trim(SEPT_BGT__PURCHASE), '')) AS SEPT_BGT_PURCHASE,
  try_to_double(nullif(trim(SEPT_BGT__ALLOCATION), '')) AS SEPT_BGT_ALLOCATION,
  try_to_double(nullif(trim(DIFF_BGT_VS_ST_FI_FIX__PURCHASE), '')) AS DIFF_PURCHASE,
  try_to_double(nullif(trim(DIFF_BGT_VS_ST_FI_FIX__ALLOCATION), '')) AS DIFF_ALLOCATION,
  to_date('2026-09-15') SOURCE_FILE_DATE, current_timestamp() LOADED_AT
from V2RETAIL.RETAIL_HUB.BRZ_FIXGAP_ST_MAJCAT
where nullif(trim(ST_CD), '') is not null;

create or replace view V2RETAIL.PULSE.V_BI_FIXGAP_ST_ONELINER copy grants comment = 'V2Pulse: RPT_FIXGAP_ST_ONELINER' as
select b.*, to_varchar(convert_timezone('Asia/Kolkata', b.LOADED_AT)::timestamp_ntz, 'DD-MM-YYYY HH24:MI') LAST_REFRESHED
from V2RETAIL.GOLD.RPT_FIXGAP_ST_ONELINER b;
grant select on view V2RETAIL.PULSE.V_BI_FIXGAP_ST_ONELINER to role PULSE_SVC_ROLE;

create or replace view V2RETAIL.PULSE.V_BI_FIXGAP_ST_DIV copy grants comment = 'V2Pulse: RPT_FIXGAP_ST_DIV' as
select b.*, to_varchar(convert_timezone('Asia/Kolkata', b.LOADED_AT)::timestamp_ntz, 'DD-MM-YYYY HH24:MI') LAST_REFRESHED
from V2RETAIL.GOLD.RPT_FIXGAP_ST_DIV b;
grant select on view V2RETAIL.PULSE.V_BI_FIXGAP_ST_DIV to role PULSE_SVC_ROLE;

create or replace view V2RETAIL.PULSE.V_BI_FIXGAP_ST_FLR_SUBDIV copy grants comment = 'V2Pulse: RPT_FIXGAP_ST_FLR_SUBDIV' as
select b.*, to_varchar(convert_timezone('Asia/Kolkata', b.LOADED_AT)::timestamp_ntz, 'DD-MM-YYYY HH24:MI') LAST_REFRESHED
from V2RETAIL.GOLD.RPT_FIXGAP_ST_FLR_SUBDIV b;
grant select on view V2RETAIL.PULSE.V_BI_FIXGAP_ST_FLR_SUBDIV to role PULSE_SVC_ROLE;

create or replace view V2RETAIL.PULSE.V_BI_FIXGAP_ST_FLR copy grants comment = 'V2Pulse: RPT_FIXGAP_ST_FLR' as
select b.*, to_varchar(convert_timezone('Asia/Kolkata', b.LOADED_AT)::timestamp_ntz, 'DD-MM-YYYY HH24:MI') LAST_REFRESHED
from V2RETAIL.GOLD.RPT_FIXGAP_ST_FLR b;
grant select on view V2RETAIL.PULSE.V_BI_FIXGAP_ST_FLR to role PULSE_SVC_ROLE;

create or replace view V2RETAIL.PULSE.V_BI_FIXGAP_ST_MAJCAT copy grants comment = 'V2Pulse: RPT_FIXGAP_ST_MAJCAT' as
select b.*, to_varchar(convert_timezone('Asia/Kolkata', b.LOADED_AT)::timestamp_ntz, 'DD-MM-YYYY HH24:MI') LAST_REFRESHED
from V2RETAIL.GOLD.RPT_FIXGAP_ST_MAJCAT b;
grant select on view V2RETAIL.PULSE.V_BI_FIXGAP_ST_MAJCAT to role PULSE_SVC_ROLE;

-- CHECK (compare with the TTL row of each Excel sheet): stores, installed fixtures, October budget
select 'ONELINER' g, count(*) rows_, count(distinct ST_CD) stores, sum(ST_FIX) st_fix, sum(OCT_BGT) oct_bgt, sum(FRESH_FIX) fresh, sum(DIFF_ALLOCATION) diff_alloc from V2RETAIL.GOLD.RPT_FIXGAP_ST_ONELINER
union all select 'DIV', count(*), count(distinct ST_CD), null, sum(OCT_BGT), sum(FRESH_FIX), sum(DIFF_ALLOCATION) from V2RETAIL.GOLD.RPT_FIXGAP_ST_DIV
union all select 'FLR_SUBDIV', count(*), count(distinct ST_CD), null, sum(OCT_BGT), sum(FRESH_FIX), sum(DIFF_ALLOCATION) from V2RETAIL.GOLD.RPT_FIXGAP_ST_FLR_SUBDIV
union all select 'FLR', count(*), count(distinct ST_CD), null, sum(OCT_BGT), sum(FRESH_FIX), sum(DIFF_ALLOCATION) from V2RETAIL.GOLD.RPT_FIXGAP_ST_FLR
union all select 'MAJCAT', count(*), count(distinct ST_CD), null, sum(OCT_BGT), sum(FRESH_FIX), sum(DIFF_ALLOCATION) from V2RETAIL.GOLD.RPT_FIXGAP_ST_MAJCAT;
