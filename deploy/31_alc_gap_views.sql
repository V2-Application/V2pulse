-- V2Pulse datasets for the allocation gap reports (pulse/modules/alc_gap_catalog.py): the two PULSE views and grants.
use role ACCOUNTADMIN;
use warehouse COMPUTE_WH;

create or replace view V2RETAIL.PULSE.V_BI_ALC_GAP
  comment = 'V2Pulse dataset: allocation gap reports - one row per report x checked store / store-MJ / MJ / generic-colour'
as select r.*,
       iff(r.IS_GAP = 1, 'GAP', 'OK') as GAP_STATUS,
       iff(r.IS_GAP = 1, r.ST_CD, null) as GAP_ST_CD,
       iff(r.IS_GAP = 1, r.MAJ_CAT, null) as GAP_MAJ_CAT,
       h.ZONE_CODE, h.REGION_CODE
from V2RETAIL.SILVER.ALC_GAP_ROWS r
left join V2RETAIL.PULSE.V_STORE_HIERARCHY h on h.STORE_CODE = r.ST_CD;

create or replace view V2RETAIL.PULSE.V_BI_ALC_GAP_TREND
  comment = 'V2Pulse dataset: allocation gap reports - gaps per run day, report, segment and division'
as select * from V2RETAIL.SILVER.ALC_GAP_DAILY;

grant select on view V2RETAIL.PULSE.V_BI_ALC_GAP to role PULSE_SVC_ROLE;
grant select on view V2RETAIL.PULSE.V_BI_ALC_GAP_TREND to role PULSE_SVC_ROLE;
