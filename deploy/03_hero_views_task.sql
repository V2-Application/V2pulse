-- Hero Articles: the PULSE views behind the two V2Pulse datasets, the daily task, and the grants.
-- Run as ACCOUNTADMIN on COMPUTE_WH after 01 and 02. Safe to re-run.
use role ACCOUNTADMIN;
use warehouse COMPUTE_WH;

-- Dataset "hero_position": one row per store x generic article for each of the last 14 days (AS_OF_DT is the
-- snapshot: V2Pulse shows the latest by default). Stock columns are as of STOCK_DT; flags are 0/1 store counts.
create or replace view V2RETAIL.PULSE.V_BI_HERO_POSITION
  comment = 'V2Pulse dataset: Hero articles - position, stock and gaps'
as
select p.AS_OF_DT, p.STOCK_DT,
       p.ST_CD, s.STORE_NAME as ST_NM, coalesce(h.STATE, s.STATE) as STATE, h.ZONE_CODE, h.REGION_CODE, h.CLUSTER_CODE,
       s.OLD_NEW_FLAG as ST_STATUS, coalesce(s.RDC_CD, 'NO RDC') as RDC_CD,
       p.SEG, p.DIV, p.SUB_DIV, p.MAJ_CAT, g.MC_DESC,
       p.GEN_ART_NUMBER, g.GEN_ART_DESC, g.MRP, g.VENDOR_NAME, g.RNG_SEG, g.SSN, g.FIT, g.PATTERN, g.FABRIC,
       p.CO_RANK, p.MC_RANK, p.ST_RANK, p.HERO_TIER, p.MC_HERO, p.ST_TOP100,
       p.GAP_TYPE, p.ACTION,
       case when p.SALE_Q_P7D <= 0 and p.SALE_Q_L7D > 0 then '4 · New this week'
            when p.SALE_Q_P7D <= 0 then '5 · No sale'
            when p.SALE_Q_L7D >= 1.2 * p.SALE_Q_P7D then '1 · Rising (>+20%)'
            when p.SALE_Q_L7D <= 0.8 * p.SALE_Q_P7D then '3 · Falling (<-20%)'
            else '2 · Steady' end                                                                  as MOMENTUM,
       case when p.DISC_V_L30D <= 0 or p.SALE_V_L30D + p.DISC_V_L30D <= 0 then '0 · No discount'
            when p.DISC_V_L30D / (p.SALE_V_L30D + p.DISC_V_L30D) < 0.10 then '1 · Under 10%'
            when p.DISC_V_L30D / (p.SALE_V_L30D + p.DISC_V_L30D) < 0.20 then '2 · 10-20%'
            else '3 · 20% and more' end                                                          as DISCOUNT_BAND,
       case when p.AGE_DAYS is null then '9 · Unknown' when p.AGE_DAYS <= 30 then '1 · 0-30 days'
            when p.AGE_DAYS <= 90 then '2 · 31-90 days' when p.AGE_DAYS <= 180 then '3 · 91-180 days'
            when p.AGE_DAYS <= 365 then '4 · 181-365 days' else '5 · Over a year' end           as AGE_BAND,
       case when p.ROS_Q <= 0 then iff(p.FLOOR_Q + p.BACKROOM_Q > 0, '6 · No sale (dead)', '0 · No stock, no sale')
            when (p.FLOOR_Q + p.BACKROOM_Q) / p.ROS_Q < 7 then '1 · Under 7 days'
            when (p.FLOOR_Q + p.BACKROOM_Q) / p.ROS_Q < 15 then '2 · 7-15 days'
            when (p.FLOOR_Q + p.BACKROOM_Q) / p.ROS_Q < 30 then '3 · 15-30 days'
            when (p.FLOOR_Q + p.BACKROOM_Q) / p.ROS_Q < 90 then '4 · 30-90 days'
            else '5 · Over 90 days' end                                                           as COVER_BAND,
       case when p.SEASON_FACTOR >= 1.2 then '1 · Season building (>+20%)'
            when p.SEASON_FACTOR <= 0.8 then '3 · Season fading (<-20%)' else '2 · Flat' end       as SEASON_OUTLOOK,
       p.SALE_V_L7D, p.SALE_Q_L7D, p.SALE_V_P7D, p.SALE_Q_P7D, p.SALE_V_L30D, p.SALE_Q_L30D, p.GM_V_L30D,
       p.DISC_V_L30D, p.SALE_V_L30D + p.DISC_V_L30D as GROSS_V_L30D, p.MRP_V_L30D, p.SALE_V_MTD,
       p.LY_SALE_V_L30D, p.LY_SALE_Q_L30D,
       p.FLOOR_Q, p.BACKROOM_Q, p.FLOOR_Q + p.BACKROOM_Q as ST_STK_Q, p.PIPELINE_Q, p.INTRANSIT_Q,
       p.PIPELINE_Q + p.INTRANSIT_Q as INBOUND_Q, p.RDC_Q, p.ALL_DC_Q,
       (p.FLOOR_Q + p.BACKROOM_Q) * p.ASP as ST_STK_V,
       p.ROS_Q, p.SEASON_FACTOR, p.FCST_30_Q, p.FCST_30_Q * p.ASP as FCST_30_V, p.AVAIL_Q, p.GAP_Q, p.GAP_V,
       p.LOST_7D_V, p.EXCESS_Q, p.EXCESS_Q * p.ASP as EXCESS_V, p.ASP,
       p.CORE_SIZES, p.CORE_SIZES_STK,
       p.SELLING_F, p.STOCKED_F, p.OOS_F, p.LOW_COVER_F, p.BROKEN_SIZE_F, p.NOT_RANGED_F, p.EXCESS_F,
       iff(p.SELLING_F = 1 or p.NOT_RANGED_F = 1, 1, 0) as SHOULD_STOCK_F,
       p.AGE_DAYS, p.FIRST_SALE_DT, p.BUILT_AT
from V2RETAIL.SILVER.HERO_POSITION p
left join V2RETAIL.SILVER.HERO_DIM_GENART g  on g.GEN_ART_NUMBER = p.GEN_ART_NUMBER
left join V2RETAIL.PULSE.V_STORE_HIERARCHY h on h.STORE_CODE = p.ST_CD
left join V2RETAIL.GOLD.DIM_STORE_V2 s       on s.STORE_CODE = p.ST_CD;

-- Dataset "hero_sales": daily store x generic article sale since 2025-04-01 with the 364-day-earlier twin,
-- tagged with today's hero tier so a user can follow today's heroes back through time.
create or replace view V2RETAIL.PULSE.V_BI_HERO_SALES
  comment = 'V2Pulse dataset: Hero articles - sales over time'
as
select f.DT, f.ST_CD, s.STORE_NAME as ST_NM, coalesce(h.STATE, s.STATE) as STATE, h.ZONE_CODE, h.REGION_CODE,
       h.CLUSTER_CODE, s.OLD_NEW_FLAG as ST_STATUS, coalesce(s.RDC_CD, 'NO RDC') as RDC_CD,
       f.SEG, f.DIV, f.SUB_DIV, f.MAJ_CAT, g.MC_DESC,
       f.GEN_ART_NUMBER, g.GEN_ART_DESC, g.MRP, g.VENDOR_NAME, g.RNG_SEG, g.SSN, g.FIT, g.PATTERN, g.FABRIC,
       coalesce(t.HERO_TIER, iff(f.SEG in ('MKT', 'NT'), 'X · Gift / non-trading', 'D · Rest')) as HERO_TIER,
       coalesce(t.MC_HERO, 'Other') as MC_HERO, t.CO_RANK,
       f.SALE_V, f.SALE_Q, f.GM_V, f.DISC_V, coalesce(f.SALE_V, 0) + coalesce(f.DISC_V, 0) as GROSS_V,
       f.SALE_Q * g.MRP as MRP_V, f.LY_SALE_V, f.LY_SALE_Q, f.LY_GM_V,
       iff(f.SALE_Q > 0, f.ST_CD, null) as SELLING_ST, iff(f.SALE_Q > 0, f.DT, null) as SELLING_DT, f.SRC
from V2RETAIL.SILVER.HERO_SALE_DAILY f
left join V2RETAIL.SILVER.HERO_DIM_GENART g   on g.GEN_ART_NUMBER = f.GEN_ART_NUMBER
left join V2RETAIL.SILVER.HERO_TIER_CURRENT t on t.GEN_ART_NUMBER = f.GEN_ART_NUMBER
left join V2RETAIL.PULSE.V_STORE_HIERARCHY h  on h.STORE_CODE = f.ST_CD
left join V2RETAIL.GOLD.DIM_STORE_V2 s        on s.STORE_CODE = f.ST_CD;

grant select on view V2RETAIL.PULSE.V_BI_HERO_POSITION to role PULSE_SVC_ROLE;
grant select on view V2RETAIL.PULSE.V_BI_HERO_SALES to role PULSE_SVC_ROLE;

-- Daily at 09:30 IST (sale feed lands ~05:00, ET_STOCK_DATA ~05:20, PRD/PTL ~06:25, hub in-transit ~07:05)
-- and again at 13:30 IST to pick up a late stock load. Each run re-states the last 4 sale days and replaces
-- today's snapshot, so a second run is harmless.
create or replace task V2RETAIL.SILVER.TASK_HERO_DAILY
  warehouse = COMPUTE_WH
  schedule = 'USING CRON 30 9,13 * * * Asia/Kolkata'
  comment = 'Hero Articles (V2Pulse): SILVER.SP_HERO_BUILD(false)'
as call V2RETAIL.SILVER.SP_HERO_BUILD(false);
alter task V2RETAIL.SILVER.TASK_HERO_DAILY resume;
