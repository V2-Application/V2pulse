-- AI suggestions (V2Pulse Analytics): every morning, one row per alert with where it is, what is wrong, what to do,
-- how much and what it is worth; then Claude (Snowflake Cortex, claude-sonnet-4-5) writes a short brief per scope from
-- the exact figures. Run as ACCOUNTADMIN on COMPUTE_WH; safe to re-run.
--   SILVER.AI_SUGGESTION   latest day's suggestions (7 days kept)
--   SILVER.AI_BRIEF        Claude's written brief: company, each zone, each owner team (30 days kept)
--   SILVER.SP_AI_SUGGESTIONS  builds both; task SILVER.TASK_AI_SUGGESTIONS runs after SILVER.TASK_HERO_DAILY

create table if not exists V2RETAIL.SILVER.AI_SUGGESTION (
  AS_OF_DT date not null, SUGG_ID varchar, CATEGORY varchar, RULE_CODE varchar, PRIORITY varchar, OWNER varchar,
  LOCATION_TYPE varchar, ST_CD varchar, ST_NM varchar, ZONE_CODE varchar, REGION_CODE varchar, CLUSTER_CODE varchar,
  RDC_CD varchar, SEG varchar, DIV varchar, SUB_DIV varchar, MAJ_CAT varchar, GEN_ART_NUMBER number(18,0),
  GEN_ART_DESC varchar, VENDOR_NAME varchar, HERO_TIER varchar,
  ALERT varchar, ACTION varchar, EVIDENCE varchar, QTY number(18,0), VALUE_V number(18,2), SCORE number(18,2),
  BUILT_AT timestamp_ntz
) comment = 'AI suggestions: one row per alert and suggested action. Built by SILVER.SP_AI_SUGGESTIONS.';

create table if not exists V2RETAIL.SILVER.STORE_FILL_RATE (
  AS_OF_DT date not null, STOCK_DT date, ARS_SESSION varchar, ST_CD varchar, ST_NM varchar, ZONE_CODE varchar,
  REGION_CODE varchar, CLUSTER_CODE varchar, RDC_CD varchar, ST_STATUS varchar, SEG varchar, DIV varchar, SUB_DIV varchar,
  MAJ_CAT varchar, MBQ float, DISP_Q float, STOCK_Q float, STOCK_V float, FILL_Q float, SHORT_Q float, EXCESS_Q float,
  SALE_Q_L7D float, SALE_Q_L30D float, SALE_V_L30D float, ASP float, SHORT_V float, RDC_STK_Q float, FR float, FR_BAND varchar,
  MBQ_F number(1,0), BELOW85_F number(1,0), SELLING_F number(1,0), BUILT_AT timestamp_ntz
) comment = 'Store fill rate: sales-area stock (SLoc 0001) per store x major category against the ARS MBQ of the latest clean run. 30 days kept. Built by SILVER.SP_AI_SUGGESTIONS.';

create table if not exists V2RETAIL.SILVER.AI_BRIEF (
  AS_OF_DT date not null, SCOPE_TYPE varchar, SCOPE_CODE varchar, TITLE varchar, BRIEF varchar, SUGGESTIONS number,
  P1 number, VALUE_V number(18,2), MODEL varchar, BUILT_AT timestamp_ntz
) comment = 'AI suggestions: Claude-written daily brief per scope (company, zone, owner team). Built by SILVER.SP_AI_SUGGESTIONS.';

create or replace procedure V2RETAIL.SILVER.SP_AI_SUGGESTIONS(WITH_BRIEF boolean)
returns varchar
language sql
comment = 'AI suggestions: rule-based alerts and actions from the hero position, sales and DC stock, plus a Claude brief.'
execute as owner
as
$$
declare
  d date;
  sd date;
  n integer;
  nb integer default 0;
  ars varchar;
  fd date;
begin
  select max(AS_OF_DT) into :d from V2RETAIL.SILVER.HERO_POSITION;
  select max(STOCK_DT) into :sd from V2RETAIL.SILVER.HERO_POSITION where AS_OF_DT = :d;

  create or replace temporary table AI_POS as
  select * from V2RETAIL.PULSE.V_BI_HERO_POSITION where AS_OF_DT = :d
    -- stores only: the RDCs and hubs appear in the sale feed too
    and ST_CD not in ('DH24', 'DW01', 'DK04', 'DB03') and coalesce(ST_NM, '') not ilike '%HUB%';

  -- 0. Store fill rate: sales-area stock (SLoc 0001) of each store x major category against its MBQ, the norm of the
  --    latest clean (non-adhoc) ARS allocation run (MBQ = display qty + daily sale budget x cover days). 30 days kept.
  select SESSION_ID into :ars from V2RETAIL.BRONZE.ARS_GRID_MJ where SESSION_ID <> '' group by 1
    having count_if(coalesce(ADHOC, '') <> '') = 0 order by max(_LOADED_AT) desc limit 1;
  select max(DT) into :fd from V2RETAIL.BRONZE.DAY_WISE_ST_VAR_STOCK
    where KPI = '0001' and DT between dateadd(day, -10, :d) and :d;
  begin transaction;
  delete from V2RETAIL.SILVER.STORE_FILL_RATE where AS_OF_DT = :d or AS_OF_DT < dateadd(day, -29, :d);
  insert into V2RETAIL.SILVER.STORE_FILL_RATE
  with a as (
    select WERKS ST_CD, MAJ_CAT, max(try_to_double(MBQ)) MBQ, max(try_to_double(DISP_Q)) DISP_Q
    from V2RETAIL.BRONZE.ARS_GRID_MJ where SESSION_ID = :ars and MAJ_CAT is not null group by 1, 2),
  s as (
    select ST_CD, MAJ_CAT, sum(STOCK_Q) STOCK_Q, sum(STOCK_V) STOCK_V
    from V2RETAIL.BRONZE.DAY_WISE_ST_VAR_STOCK where KPI = '0001' and DT = :fd and MAJ_CAT is not null group by 1, 2),
  sl as (
    select ST_CD, MAJ_CAT, sum(iff(DT > dateadd(day, -7, :d), SALE_Q, 0)) Q7, sum(SALE_Q) Q30, sum(SALE_V) V30
    from V2RETAIL.SILVER.HERO_SALE_DAILY where DT > dateadd(day, -30, :d) and DT <= :d group by 1, 2),
  zmc as (select MAJ_CAT, mode(SEG) SEG, mode(DIVISION) as DIV, mode(SUB_DIVISION) SUB_DIV
          from V2RETAIL.BRONZE.ZMC_MASTER where MAJ_CAT is not null group by 1),
  dcq as (
    select e.WERKS RDC_CD, g.MAJ_CAT, sum(e.LABST) Q
    from V2RETAIL.BRONZE.ET_STOCK_DATA e
    join V2RETAIL.SILVER.HERO_VARMAP m on m.VAR_ARTICLE = try_to_number(e.MATNR)
    join V2RETAIL.SILVER.HERO_DIM_GENART g on g.GEN_ART_NUMBER = m.GEN_ART_NUMBER
    where e.STOCK_DATE = :sd and e.WERKS in ('DH24', 'DW01', 'DK04') and e.LGORT = '0001' and e.LABST > 0 group by 1, 2),
  k as (select ST_CD, MAJ_CAT from a union select ST_CD, MAJ_CAT from s),
  -- operating stores only: sold something in the last 7 days (closed and not-yet-open stores read as 0% fill)
  ops as (select ST_CD from sl group by 1 having sum(Q7) > 0),
  st as (select STORE_CODE ST_CD, STORE_NAME, ZONE_CODE, REGION_CODE, CLUSTER_CODE from V2RETAIL.PULSE.V_STORE_HIERARCHY)
  select :d, :fd, :ars, k.ST_CD, coalesce(ds.STORE_NAME, st.STORE_NAME), coalesce(st.ZONE_CODE, 'Unmapped'),
         coalesce(st.REGION_CODE, 'Unmapped'), coalesce(st.CLUSTER_CODE, 'Unmapped'), coalesce(ds.RDC_CD, 'NO RDC'),
         ds.OLD_NEW_FLAG, z.SEG, z.DIV, z.SUB_DIV, k.MAJ_CAT,
         coalesce(a.MBQ, 0), coalesce(a.DISP_Q, 0), greatest(coalesce(s.STOCK_Q, 0), 0), greatest(coalesce(s.STOCK_V, 0), 0),
         least(greatest(coalesce(s.STOCK_Q, 0), 0), coalesce(a.MBQ, 0)),
         greatest(coalesce(a.MBQ, 0) - greatest(coalesce(s.STOCK_Q, 0), 0), 0),
         greatest(greatest(coalesce(s.STOCK_Q, 0), 0) - 1.1 * coalesce(a.MBQ, 0), 0),
         coalesce(sl.Q7, 0), coalesce(sl.Q30, 0), coalesce(sl.V30, 0), sl.V30 / nullif(sl.Q30, 0),
         greatest(coalesce(a.MBQ, 0) - greatest(coalesce(s.STOCK_Q, 0), 0), 0) * coalesce(sl.V30 / nullif(sl.Q30, 0), 0),
         coalesce(case coalesce(ds.RDC_CD, 'NO RDC') when 'NO RDC' then null else d1.Q end, 0),
         greatest(coalesce(s.STOCK_Q, 0), 0) / nullif(a.MBQ, 0),
         case when coalesce(a.MBQ, 0) = 0 then '0 · No MBQ'
              when coalesce(s.STOCK_Q, 0) < 0.5 * a.MBQ then '1 · Below 50%' when s.STOCK_Q < 0.7 * a.MBQ then '2 · 50-70%'
              when s.STOCK_Q < 0.85 * a.MBQ then '3 · 70-85%' when s.STOCK_Q <= 1.1 * a.MBQ then '4 · 85-110% (on norm)'
              when s.STOCK_Q <= 1.5 * a.MBQ then '5 · 110-150%' else '6 · Over 150%' end,
         iff(coalesce(a.MBQ, 0) > 0, 1, 0), iff(coalesce(a.MBQ, 0) > 0 and coalesce(s.STOCK_Q, 0) < 0.85 * a.MBQ, 1, 0),
         iff(coalesce(sl.Q30, 0) > 0, 1, 0), current_timestamp()::timestamp_ntz
  from k
  join st on st.ST_CD = k.ST_CD                                -- real stores only (hubs / DCs are not in the hierarchy)
  join ops on ops.ST_CD = k.ST_CD
  left join V2RETAIL.GOLD.DIM_STORE_V2 ds on ds.STORE_CODE = k.ST_CD
  left join a on a.ST_CD = k.ST_CD and a.MAJ_CAT = k.MAJ_CAT
  left join s on s.ST_CD = k.ST_CD and s.MAJ_CAT = k.MAJ_CAT
  left join sl on sl.ST_CD = k.ST_CD and sl.MAJ_CAT = k.MAJ_CAT
  left join zmc z on z.MAJ_CAT = k.MAJ_CAT
  left join dcq d1 on d1.RDC_CD = ds.RDC_CD and d1.MAJ_CAT = k.MAJ_CAT;
  commit;

  begin transaction;
  delete from V2RETAIL.SILVER.AI_SUGGESTION where AS_OF_DT = :d or AS_OF_DT < dateadd(day, -6, :d);

  -- 7. FILL RATE: categories below 85% of MBQ that sell, stores whose overall fill is low, stores ARS cannot fill.
  insert into V2RETAIL.SILVER.AI_SUGGESTION
  with f as (select * from V2RETAIL.SILVER.STORE_FILL_RATE where AS_OF_DT = :d),
  stf as (select ST_CD, any_value(ST_NM) ST_NM, any_value(ZONE_CODE) ZONE_CODE, any_value(REGION_CODE) REGION_CODE,
                 any_value(CLUSTER_CODE) CLUSTER_CODE, any_value(RDC_CD) RDC_CD, sum(MBQ) MBQ, sum(FILL_Q) FILL_Q,
                 sum(SHORT_Q) SHORT_Q, sum(SHORT_V) SHORT_V, sum(BELOW85_F) N_LOW, sum(STOCK_Q) STOCK_Q, sum(SALE_V_L30D) SALE_V
          from f group by 1),
  co as (select sum(FILL_Q) / nullif(sum(MBQ), 0) CO_FR from f),
  rows_ as (
    select 'FR_GAP' RULE_CODE, ST_CD || '-' || MAJ_CAT ID,
           case when FR < 0.5 and SALE_Q_L7D >= 5 then 'P1 · Act today' when FR < 0.7 then 'P2 · This week' else 'P3 · Plan' end PRIORITY,
           'Allocation / RDC' OWNER, ST_CD, ST_NM, ZONE_CODE, REGION_CODE, CLUSTER_CODE, RDC_CD, SEG, DIV, SUB_DIV, MAJ_CAT,
           MAJ_CAT || ' fill rate ' || round(FR * 100) || '% (' || round(STOCK_Q) || ' on the floor against an MBQ of ' ||
             round(MBQ) || ')' ALERT,
           'Fill about ' || ceil(SHORT_Q) || ' units' ||
             iff(RDC_STK_Q > 0, ' from ' || RDC_CD || ' (it holds ' || round(RDC_STK_Q) || ')', '; the RDC holds none, check the other DCs') ACTION,
           'Sells ' || round(SALE_Q_L7D) || ' a week · ARS run ' || ARS_SESSION || ' · stock as of ' || STOCK_DT EVIDENCE,
           ceil(SHORT_Q) QTY, SHORT_V VALUE_V, coalesce(SHORT_V, 0) * iff(FR < 0.5, 3, 1.5) SCORE
    from f where MBQ > 0 and FR < 0.85 and SALE_Q_L30D > 0 and SHORT_Q >= 2
    union all
    select 'FR_STORE', ST_CD, iff(FILL_Q / MBQ < 0.6, 'P1 · Act today', 'P2 · This week'), 'Allocation / RDC', ST_CD, ST_NM,
           ZONE_CODE, REGION_CODE, CLUSTER_CODE, RDC_CD, null, null, null, null,
           'Store fill rate ' || round(FILL_Q / MBQ * 100) || '% of MBQ against ' || round(CO_FR * 100) || '% for the company; ' ||
             N_LOW || ' categories are below 85%',
           'Put this store first in the next ARS run and clear its category gaps (see Fill rate)',
           'Short ' || round(SHORT_Q) || ' units worth Rs ' || round(SHORT_V / 1e5, 1) || ' L at selling price',
           round(SHORT_Q), SHORT_V, coalesce(SHORT_V, 0) * 2
    from stf, co where MBQ > 0 and FILL_Q / MBQ < 0.75
    union all
    select 'FR_NO_MBQ', ST_CD, 'P2 · This week', 'Merchandising', ST_CD, ST_NM, ZONE_CODE, REGION_CODE, CLUSTER_CODE, RDC_CD,
           null, null, null, null,
           'No MBQ in the ARS run for this store, so it cannot be replenished automatically',
           'Set the store''s listing and MBQ in ARS before the next run',
           'Sales-area stock ' || round(STOCK_Q) || ' units · sale L30D Rs ' || round(SALE_V / 1e5, 1) || ' L',
           null, SALE_V, coalesce(SALE_V, 0)
    from stf where coalesce(MBQ, 0) = 0 and SALE_V > 0)
  select :d, RULE_CODE || '-' || ID, '7 · Store fill rate', RULE_CODE, PRIORITY, OWNER, 'Store', ST_CD, ST_NM, ZONE_CODE,
         REGION_CODE, CLUSTER_CODE, RDC_CD, SEG, DIV, SUB_DIV, MAJ_CAT, null, null, null, null, ALERT, ACTION, EVIDENCE,
         QTY, VALUE_V, SCORE, current_timestamp()::timestamp_ntz
  from rows_;

  -- 1. BUY: company need for the next 30 days (+7 days safety) beyond stores, inbound and DC stock.
  insert into V2RETAIL.SILVER.AI_SUGGESTION
  with a as (
    select GEN_ART_NUMBER, any_value(GEN_ART_DESC) GEN_ART_DESC, any_value(VENDOR_NAME) VENDOR_NAME, any_value(SEG) SEG,
           any_value(DIV) as DIV, any_value(SUB_DIV) SUB_DIV, any_value(MAJ_CAT) MAJ_CAT, any_value(HERO_TIER) HERO_TIER,
           min(CO_RANK) RK, sum(FCST_30_Q) NEED, sum(ST_STK_Q) ST, sum(INBOUND_Q) INB, max(ALL_DC_Q) DC, sum(ROS_Q) ROS,
           sum(SALE_V_L30D) / nullif(sum(SALE_Q_L30D), 0) ASP, sum(OOS_F) OOS
    from AI_POS where CO_RANK <= 2000 group by 1),
  b as (select a.*, ceil(NEED + 7 * ROS - (ST + INB + DC)) Q, DC / nullif(ROS, 0) DC_COVER,
               (ST + INB + DC) / nullif(ROS, 0) TTL_COVER from a)
  select :d, 'BUY-' || GEN_ART_NUMBER, '1 · What to buy', 'BUY_SHORT',
         case when HERO_TIER in ('A · Top 100', 'B · 101-500') and coalesce(DC_COVER, 0) < 7 then 'P1 · Act today'
              when coalesce(DC_COVER, 0) < 15 then 'P2 · This week' else 'P3 · Plan' end,
         'Buying', 'Company', null, null, null, null, null, null, SEG, DIV, SUB_DIV, MAJ_CAT, GEN_ART_NUMBER, GEN_ART_DESC,
         VENDOR_NAME, HERO_TIER,
         'All stock covers ' || round(TTL_COVER) || ' days of sale; the DCs hold ' || round(coalesce(DC_COVER, 0)) ||
           ' days' || iff(OOS > 0, '; ' || OOS || ' stores are out of stock', ''),
         'Buy about ' || Q || ' units' || iff(VENDOR_NAME is not null, ' from ' || VENDOR_NAME, ''),
         '30-day need ' || round(NEED) || ' · in stores ' || round(ST) || ' · on the way ' || round(INB) || ' · in DCs ' ||
           round(DC) || ' · company rank ' || RK,
         Q, Q * ASP, Q * ASP * iff(HERO_TIER in ('A · Top 100', 'B · 101-500'), 3, 1.5), current_timestamp()::timestamp_ntz
  from b where Q >= 12 and ASP > 0;

  -- 2. SEND: store gaps on the top 2000 filled from the store's RDC, most urgent store first, never more than the RDC has.
  insert into V2RETAIL.SILVER.AI_SUGGESTION
  with g as (
    select p.*, coalesce(sum(GAP_Q) over (partition by GEN_ART_NUMBER, RDC_CD order by LOST_7D_V desc, GAP_V desc, ST_CD
                                          rows between unbounded preceding and 1 preceding), 0) PRIOR_GAP
    from AI_POS p
    where CO_RANK <= 2000 and GAP_Q >= 1 and RDC_Q > 0
      and GAP_TYPE in ('1 · Out of stock', '2 · Hero not ranged', '3 · Low cover (<7 days)', '4 · Broken sizes')),
  s as (select g.*, least(GAP_Q, greatest(RDC_Q - PRIOR_GAP, 0)) ALLOC from g)
  select :d, 'SEND-' || ST_CD || '-' || GEN_ART_NUMBER, '2 · What to send to stores', 'SEND_GAP',
         case when OOS_F = 1 and HERO_TIER in ('A · Top 100', 'B · 101-500') then 'P1 · Act today'
              when OOS_F = 1 or NOT_RANGED_F = 1 then 'P2 · This week' else 'P3 · Plan' end,
         'Allocation / RDC', 'Store', ST_CD, ST_NM, ZONE_CODE, REGION_CODE, CLUSTER_CODE, RDC_CD, SEG, DIV, SUB_DIV, MAJ_CAT,
         GEN_ART_NUMBER, GEN_ART_DESC, VENDOR_NAME, HERO_TIER,
         substr(GAP_TYPE, 5) || ' · sells ' || round(ROS_Q * 7, 1) || ' a week · ' || round(ST_STK_Q) || ' in store',
         'Send ' || round(ALLOC) || ' units from ' || iff(RDC_CD = 'NO RDC', 'the nearest RDC', RDC_CD),
         '30-day forecast ' || round(FCST_30_Q) || ' · available ' || round(AVAIL_Q) || ' · RDC stock ' || round(RDC_Q),
         ALLOC, ALLOC * ASP, ALLOC * ASP * iff(OOS_F = 1, 3, 1.5), current_timestamp()::timestamp_ntz
  from s where ALLOC >= 1;

  -- 3. MOVE: excess in one store of an RDC area while others there are still short after the RDC has sent all it has.
  insert into V2RETAIL.SILVER.AI_SUGGESTION
  with short as (
    select p.GEN_ART_NUMBER, p.RDC_CD, p.ST_CD, p.ST_NM,
           greatest(p.GAP_Q - coalesce(s.QTY, 0), 0) LEFT_Q
    from AI_POS p
    left join V2RETAIL.SILVER.AI_SUGGESTION s on s.AS_OF_DT = :d and s.RULE_CODE = 'SEND_GAP' and s.ST_CD = p.ST_CD
                                             and s.GEN_ART_NUMBER = p.GEN_ART_NUMBER
    where p.CO_RANK <= 2000 and p.GAP_Q >= 1 and p.GAP_TYPE in ('1 · Out of stock', '3 · Low cover (<7 days)')),
  need as (
    select GEN_ART_NUMBER, RDC_CD, sum(LEFT_Q) LEFT_Q, count_if(LEFT_Q > 0) N_SHORT,
           listagg(iff(LEFT_Q > 0, ST_NM, null), ', ') within group (order by LEFT_Q desc) STORES
    from short group by 1, 2 having sum(LEFT_Q) >= 3),
  donor as (
    select p.*, n.LEFT_Q NEED_Q, n.N_SHORT, n.STORES,
           coalesce(sum(p.EXCESS_Q) over (partition by p.GEN_ART_NUMBER, p.RDC_CD order by p.EXCESS_Q desc, p.ST_CD
                                          rows between unbounded preceding and 1 preceding), 0) PRIOR_X
    from AI_POS p join need n on n.GEN_ART_NUMBER = p.GEN_ART_NUMBER and n.RDC_CD = p.RDC_CD
    where p.EXCESS_Q >= 3),
  m as (select donor.*, floor(least(EXCESS_Q, greatest(NEED_Q - PRIOR_X, 0))) MOVE_Q from donor)
  select :d, 'MOVE-' || ST_CD || '-' || GEN_ART_NUMBER, '3 · Move between stores', 'MOVE_EXCESS',
         iff(HERO_TIER in ('A · Top 100', 'B · 101-500'), 'P2 · This week', 'P3 · Plan'),
         'Store ops', 'Store', ST_CD, ST_NM, ZONE_CODE, REGION_CODE, CLUSTER_CODE, RDC_CD, SEG, DIV, SUB_DIV, MAJ_CAT,
         GEN_ART_NUMBER, GEN_ART_DESC, VENDOR_NAME, HERO_TIER,
         'Excess of ' || round(EXCESS_Q) || ' units here while ' || N_SHORT || ' stores of the same RDC are short',
         'Transfer ' || MOVE_Q || ' units to ' || substr(STORES, 1, 120),
         'Store stock ' || round(ST_STK_Q) || ' · sells ' || round(ROS_Q * 7, 1) || ' a week · RDC has none left to send',
         MOVE_Q, MOVE_Q * ASP, MOVE_Q * ASP * 1.2, current_timestamp()::timestamp_ntz
  from m where MOVE_Q >= 2;

  -- 4. STORE ISSUES: each store against the company median.
  insert into V2RETAIL.SILVER.AI_SUGGESTION
  with st as (
    select ST_CD, any_value(ST_NM) ST_NM, any_value(ZONE_CODE) ZONE_CODE, any_value(REGION_CODE) REGION_CODE,
           any_value(CLUSTER_CODE) CLUSTER_CODE, any_value(RDC_CD) RDC_CD, any_value(ST_STATUS) ST_STATUS,
           sum(iff(CO_RANK <= 500, IN_STOCK_F, 0)) / nullif(sum(iff(CO_RANK <= 500, SHOULD_STOCK_F, 0)), 0) AVAIL,
           sum(iff(CO_RANK <= 500, OOS_F, 0)) OOS, sum(iff(CO_RANK <= 500, NOT_RANGED_F, 0)) NR,
           sum(BROKEN_SIZE_F) / nullif(sum(iff(SELLING_F = 1 and STOCKED_F = 1, 1, 0)), 0) BROKEN,
           sum(iff(ROS_Q = 0, ST_STK_V, 0)) DEAD_V, sum(ST_STK_V) STOCK_V, sum(SALE_V_L30D) SALE_V,
           sum(iff(CO_RANK <= 500, LOST_7D_V, 0)) LOST_V
    from AI_POS group by 1 having sum(SELLING_F) > 50),
  gr as (
    select ST_CD, sum(SALE_V) CY, sum(LY_SALE_V) LY from V2RETAIL.SILVER.HERO_SALE_DAILY
    where DT > dateadd(day, -30, :d) and DT <= :d group by 1),
  x as (select st.*, st.DEAD_V / nullif(st.STOCK_V, 0) DEAD_SHARE, iff(gr.LY > 0, gr.CY / gr.LY - 1, null) GROWTH from st
        left join gr on gr.ST_CD = st.ST_CD),
  -- Festivals inside last year's 30 days that fall outside this year's: the comparison is not like for like.
  fest as (
    select 'last year''s 30 days include ' || listagg(f.FESTIVAL_NAME, ', ') within group (order by f.FEST_DATE) ||
           ', which fall later this year' FEST_NOTE
    from (select distinct FESTIVAL_NAME, FEST_DATE from V2RETAIL.GOLD.DIM_FESTIVAL_CALENDAR) f
    where f.FEST_DATE > dateadd(day, -394, :d) and f.FEST_DATE <= dateadd(day, -364, :d)
      and not exists (select 1 from V2RETAIL.GOLD.DIM_FESTIVAL_CALENDAR c where c.FESTIVAL_NAME = f.FESTIVAL_NAME
                      and c.FEST_DATE > dateadd(day, -30, :d) and c.FEST_DATE <= :d)
    having count(*) > 0),
  med as (select median(AVAIL) M_AVAIL, median(BROKEN) M_BROKEN, percentile_cont(0.9) within group (order by DEAD_SHARE) P90_DEAD,
                 percentile_cont(0.8) within group (order by NR) P80_NR
          from x),
  issues as (
    select x.*, 'ST_AVAIL' RULE_CODE,
           iff(AVAIL < M_AVAIL - 0.2, 'P1 · Act today', 'P2 · This week') PRIORITY,
           'Top-500 hero availability ' || round(AVAIL * 100) || '% against ' || round(M_AVAIL * 100) || '% for the company' ALERT,
           'Clear the ' || OOS || ' out-of-stock heroes (see What to send) and check that stock on the way is received' ACTION,
           'Sale at risk next 7 days ₹' || round(LOST_V) || ' · out of stock ' || OOS || ' · not ranged ' || NR EVIDENCE,
           OOS QTY, LOST_V VALUE_V, LOST_V * 4 SCORE
    from x, med where AVAIL < M_AVAIL - 0.10
    union all
    select x.*, 'ST_NOT_RANGED', 'P2 · This week',
           NR || ' of the company top-500 heroes are not in this store',
           'Range the top ones: ask allocation to send them (see What to send)',
           'Availability ' || round(AVAIL * 100) || '% · sale L30D ₹' || round(SALE_V), NR, null, NR * 1000
    from x, med where NR >= 50 and NR > P80_NR
    union all
    select x.*, 'ST_BROKEN', 'P2 · This week',
           'Broken sizes on ' || round(BROKEN * 100) || '% of selling articles (company ' || round(M_BROKEN * 100) || '%)',
           'Fill core sizes from the RDC and club broken sets into one display',
           'Selling articles with fewer than 60% of core sizes', null, null, BROKEN * 100000
    from x, med where BROKEN > 2 * M_BROKEN and BROKEN > 0.2
    union all
    select x.*, 'ST_DEAD', 'P2 · This week',
           '₹' || round(DEAD_V / 1e5, 1) || ' L of stock (' || round(DEAD_SHARE * 100) || '%) has not sold in 30 days',
           'Transfer it to stores that sell it, or mark it down',
           'Stock ₹' || round(STOCK_V / 1e5, 1) || ' L · sale L30D ₹' || round(SALE_V / 1e5, 1) || ' L', null, DEAD_V, DEAD_V
    from x, med where DEAD_SHARE > P90_DEAD
    union all
    select x.*, 'ST_DECLINE', iff(GROWTH < -0.3, 'P2 · This week', 'P3 · Plan'),
           'Sale down ' || round(-GROWTH * 100) || '% against the same 30 days last year',
           'Review range, display, staffing and footfall; start with the category gaps in Range fix',
           'Sale L30D ₹' || round(SALE_V / 1e5, 1) || ' L' || coalesce(' · ' || (select FEST_NOTE from fest), ''),
           null, SALE_V * -GROWTH / (1 + GROWTH), SALE_V * -GROWTH * 0.5
    from x where GROWTH < -0.15 and ST_STATUS = 'OLD')
  select :d, RULE_CODE || '-' || ST_CD, '4 · What is wrong in stores', RULE_CODE, PRIORITY, 'Store ops', 'Store', ST_CD, ST_NM,
         ZONE_CODE, REGION_CODE, CLUSTER_CODE, RDC_CD, null, null, null, null, null, null, null, null,
         ALERT, ACTION, EVIDENCE, QTY, VALUE_V, SCORE, current_timestamp()::timestamp_ntz
  from issues;

  -- 5. RANGE FIX: a category's share of the store's stock against its share of the store's sale.
  insert into V2RETAIL.SILVER.AI_SUGGESTION
  with sm as (
    select ST_CD, MAJ_CAT, any_value(ST_NM) ST_NM, any_value(ZONE_CODE) ZONE_CODE, any_value(REGION_CODE) REGION_CODE,
           any_value(CLUSTER_CODE) CLUSTER_CODE, any_value(RDC_CD) RDC_CD, any_value(SEG) SEG, any_value(DIV) as DIV,
           any_value(SUB_DIV) SUB_DIV, sum(SALE_V_L30D) SALE_V, sum(ST_STK_V) STOCK_V, sum(ST_STK_Q) STOCK_Q, sum(ROS_Q) ROS
    from AI_POS where MAJ_CAT is not null group by 1, 2),
  tot as (select ST_CD, sum(SALE_V) T_SALE, sum(STOCK_V) T_STOCK from sm group by 1 having sum(SALE_V) > 0 and sum(STOCK_V) > 0),
  co as (select MAJ_CAT, sum(STOCK_Q) / nullif(sum(ROS), 0) CO_COVER from sm group by 1),
  r as (select sm.*, SALE_V / T_SALE SALE_SH, STOCK_V / T_STOCK STOCK_SH, STOCK_Q / nullif(ROS, 0) COVER, co.CO_COVER
        from sm join tot using (ST_CD) left join co using (MAJ_CAT)),
  f as (
    select r.*, 'RANGE_OVER' RULE_CODE, iff(STOCK_V >= 200000, 'P2 · This week', 'P3 · Plan') PRIORITY,
           MAJ_CAT || ' holds ' || round(STOCK_SH * 100, 1) || '% of the store''s stock but makes ' || round(SALE_SH * 100, 1) ||
             '% of its sale (' || coalesce(round(COVER)::varchar, 'no sale, so no') || ' days cover)' ALERT,
           'Move out about ' || round(greatest(STOCK_Q - 60 * ROS, 0)) || ' units and cut allocation' ACTION,
           round(greatest(STOCK_Q - 60 * ROS, 0)) QTY, STOCK_V * (1 - 60 * ROS / nullif(STOCK_Q, 0)) VALUE_V
    from r where STOCK_SH >= 2.5 * SALE_SH and STOCK_V >= 100000 and coalesce(COVER, 999) > 120 and SALE_V > 0
    union all
    select r.*, 'RANGE_UNDER', 'P2 · This week',
           MAJ_CAT || ' makes ' || round(SALE_SH * 100, 1) || '% of the store''s sale with ' || round(STOCK_SH * 100, 1) ||
             '% of its stock (' || round(COVER) || ' days cover)',
           'Send about ' || round(greatest(30 * ROS - STOCK_Q, 0)) || ' units and give it more display space',
           round(greatest(30 * ROS - STOCK_Q, 0)), SALE_V / 30 * greatest(30 - COVER, 0)
    from r where SALE_SH >= 1.8 * STOCK_SH and COVER < 10 and SALE_V >= 20000
    union all
    select r.*, 'RANGE_NOT_WORKING', 'P3 · Plan',
           MAJ_CAT || ': no sale in 30 days on ₹' || round(STOCK_V / 1e3) || 'K of stock, while it sells in other stores',
           'Transfer it to stores that sell it; stop allocating it here',
           round(STOCK_Q), STOCK_V
    from r where SALE_V <= 0 and STOCK_V >= 20000 and CO_COVER < 120)
  select :d, RULE_CODE || '-' || ST_CD || '-' || MAJ_CAT, '5 · Range fix in stores (category)', RULE_CODE, PRIORITY,
         'Merchandising', 'Store', ST_CD, ST_NM, ZONE_CODE, REGION_CODE, CLUSTER_CODE, RDC_CD, SEG, DIV, SUB_DIV, MAJ_CAT,
         null, null, null, null, ALERT, ACTION,
         'Sale L30D ₹' || round(SALE_V / 1e3) || 'K · stock ₹' || round(STOCK_V / 1e3) || 'K · company cover ' ||
           coalesce(round(CO_COVER)::varchar, '-') || ' days',
         QTY, VALUE_V, coalesce(VALUE_V, 0) * iff(RULE_CODE = 'RANGE_UNDER', 2, 1), current_timestamp()::timestamp_ntz
  from f where coalesce(QTY, 0) >= 1;

  -- 6. DC PLACEMENT: each RDC's stock of a category against the demand of the stores it serves.
  insert into V2RETAIL.SILVER.AI_SUGGESTION
  with dc as (
    select e.WERKS RDC_CD, g.MAJ_CAT, any_value(g.SEG) SEG, any_value(g.DIV) as DIV, any_value(g.SUB_DIV) SUB_DIV,
           sum(e.LABST) DC_Q, sum(e.LABST_DMBTR) DC_V,
           sum(iff(s.GEN_ART_NUMBER is null, e.LABST_DMBTR, 0)) DEAD_V
    from V2RETAIL.BRONZE.ET_STOCK_DATA e
    join V2RETAIL.SILVER.HERO_VARMAP m on m.VAR_ARTICLE = try_to_number(e.MATNR)
    join V2RETAIL.SILVER.HERO_DIM_GENART g on g.GEN_ART_NUMBER = m.GEN_ART_NUMBER
    left join (select distinct GEN_ART_NUMBER from V2RETAIL.SILVER.HERO_SALE_DAILY
               where DT > dateadd(day, -30, :d) and DT <= :d and SALE_Q > 0) s on s.GEN_ART_NUMBER = m.GEN_ART_NUMBER
    where e.STOCK_DATE = :sd and e.WERKS in ('DH24', 'DW01', 'DK04') and e.LGORT = '0001' and e.LABST > 0
      and g.MAJ_CAT is not null and coalesce(g.SEG, '') not in ('MKT', 'NT', 'ACC', 'FAB') and coalesce(g.DIV, '') <> 'SCRAP'
    group by 1, 2),
  dem as (select RDC_CD, MAJ_CAT, sum(ROS_Q) ROS, sum(GAP_Q) GAP from AI_POS where RDC_CD in ('DH24', 'DW01', 'DK04')
          group by 1, 2),
  j as (select dc.*, coalesce(dem.ROS, 0) ROS, coalesce(dem.GAP, 0) GAP, dc.DC_Q / nullif(dem.ROS, 0) COVER
        from dc left join dem using (RDC_CD, MAJ_CAT)),
  f as (
    select j.*, 'DC_OVER' RULE_CODE, iff(DEAD_V > 0.5 * DC_V, 'P2 · This week', 'P3 · Plan') PRIORITY,
           RDC_CD || ' holds ' || coalesce(round(COVER)::varchar, 'unlimited (no store demand)') || ' days of ' || MAJ_CAT ||
             ' demand: ₹' || round(DC_V / 1e5, 1) || ' L, of which ₹' || round(DEAD_V / 1e5, 1) || ' L has not sold anywhere in 30 days' ALERT,
           'Stop buying ' || MAJ_CAT || '; push selling lots to stores with gaps and liquidate the dead lots' ACTION,
           round(greatest(DC_Q - 90 * ROS, 0)) QTY, DC_V * least(1, greatest(0, 1 - 90 * ROS / nullif(DC_Q, 0))) VALUE_V
    from j where coalesce(COVER, 9999) > 180 and DC_Q >= 2000 and ROS > 0
    union all
    select j.*, 'DC_SHORT', iff(GAP >= 2000, 'P1 · Act today', 'P2 · This week'),
           RDC_CD || ' covers ' || round(COVER, 1) || ' days of ' || MAJ_CAT || ' while its stores are short ' || round(GAP) || ' units',
           'Buy or expedite open POs for ' || MAJ_CAT || '; check the other RDCs (see Move between DCs)',
           round(GAP), null
    from j where COVER < 7 and GAP >= 500),
  two as (
    select a.RDC_CD FROM_DC, b.RDC_CD TO_DC, a.MAJ_CAT, a.SEG, a.DIV, a.SUB_DIV, a.COVER A_COVER, b.COVER B_COVER,
           least(a.DC_Q - 60 * a.ROS, 30 * b.ROS - b.DC_Q) Q, a.DC_V / nullif(a.DC_Q, 0) UNIT_V
    from j a join j b on a.MAJ_CAT = b.MAJ_CAT and a.RDC_CD <> b.RDC_CD
    where a.COVER > 120 and b.COVER < 20 and b.GAP > 0)
  select :d, RULE_CODE || '-' || RDC_CD || '-' || MAJ_CAT, '6 · DC placement', RULE_CODE, PRIORITY,
         iff(RULE_CODE = 'DC_SHORT', 'Buying', 'Allocation / RDC'), 'DC',
         RDC_CD, RDC_CD, null, null, null, RDC_CD, SEG, DIV, SUB_DIV, MAJ_CAT, null, null, null, null, ALERT, ACTION,
         'DC stock ' || round(DC_Q) || ' units · store sale ' || round(ROS * 7) || ' a week · store gap ' || round(GAP),
         QTY, VALUE_V, coalesce(VALUE_V, GAP * 300) * iff(RULE_CODE = 'DC_SHORT', 3, 1), current_timestamp()::timestamp_ntz
  from f
  union all
  select :d, 'DC_MOVE-' || FROM_DC || '-' || TO_DC || '-' || MAJ_CAT, '6 · DC placement', 'DC_MOVE', 'P2 · This week',
         'Allocation / RDC', 'DC', FROM_DC, FROM_DC, null, null, null, FROM_DC, SEG, DIV, SUB_DIV, MAJ_CAT, null, null, null, null,
         MAJ_CAT || ' is in the wrong DC: ' || FROM_DC || ' holds ' || round(A_COVER) || ' days while ' || TO_DC ||
           '''s stores have ' || round(B_COVER, 1) || ' days',
         'Inter-DC transfer about ' || round(Q) || ' units from ' || FROM_DC || ' to ' || TO_DC,
         'Cover ' || FROM_DC || ' ' || round(A_COVER) || ' days · ' || TO_DC || ' ' || round(B_COVER, 1) || ' days',
         round(Q), Q * UNIT_V, Q * UNIT_V * 2, current_timestamp()::timestamp_ntz
  from two where Q >= 100;

  select count(*) into :n from V2RETAIL.SILVER.AI_SUGGESTION where AS_OF_DT = :d;
  commit;

  -- 7. Claude's brief per scope, written only from the figures given (company, each zone, each owner team).
  if (WITH_BRIEF) then
    begin transaction;
    delete from V2RETAIL.SILVER.AI_BRIEF where AS_OF_DT = :d or AS_OF_DT < dateadd(day, -29, :d);
    insert into V2RETAIL.SILVER.AI_BRIEF
    with s as (select * from V2RETAIL.SILVER.AI_SUGGESTION where AS_OF_DT = :d),
    scopes as (
      select 'Company' SCOPE_TYPE, 'ALL' SCOPE_CODE, 'V2 Retail - company' TITLE from dual
      union all select distinct 'Zone', ZONE_CODE, 'Zone ' || ZONE_CODE from s where ZONE_CODE is not null and ZONE_CODE <> 'Unmapped'
      union all select distinct 'Owner', OWNER, OWNER || ' team' from s),
    pick as (
      select sc.SCOPE_TYPE, sc.SCOPE_CODE, sc.TITLE, s.*,
             row_number() over (partition by sc.SCOPE_TYPE, sc.SCOPE_CODE order by left(s.PRIORITY, 2), s.SCORE desc) RN
      from scopes sc join s on sc.SCOPE_TYPE = 'Company' or (sc.SCOPE_TYPE = 'Zone' and s.ZONE_CODE = sc.SCOPE_CODE)
                            or (sc.SCOPE_TYPE = 'Owner' and s.OWNER = sc.SCOPE_CODE)),
    agg as (
      select SCOPE_TYPE, SCOPE_CODE, any_value(TITLE) TITLE, count(*) N, count_if(PRIORITY like 'P1%') P1, sum(VALUE_V) V,
             listagg(case when RN <= 30 then '- [' || left(PRIORITY, 2) || '] ' || substr(CATEGORY, 5) || ' | ' ||
                          coalesce(ST_NM || ' (' || ST_CD || ')', LOCATION_TYPE) || ' | ' ||
                          coalesce(MAJ_CAT, '') || coalesce(' / ' || GEN_ART_DESC, '') || ' | ' || ALERT || ' | ' || ACTION ||
                          coalesce(' | Rs ' || round(VALUE_V / 1e5, 2) || ' lakh', '') end, '\n') within group (order by RN) LINES
      from pick group by 1, 2),
    bytype as (
      select sc.SCOPE_TYPE, sc.SCOPE_CODE, s.CATEGORY, any_value(s.OWNER) OWNER, count(*) N,
             count_if(s.PRIORITY like 'P1%') P1, round(sum(s.QTY)) Q, sum(s.VALUE_V) V,
             round(sum(iff(s.PRIORITY like 'P1%', s.QTY, 0))) P1Q, sum(iff(s.PRIORITY like 'P1%', s.VALUE_V, 0)) P1V
      from scopes sc join s on sc.SCOPE_TYPE = 'Company' or (sc.SCOPE_TYPE = 'Zone' and s.ZONE_CODE = sc.SCOPE_CODE)
                            or (sc.SCOPE_TYPE = 'Owner' and s.OWNER = sc.SCOPE_CODE)
      group by 1, 2, 3),
    -- One line per type, with its owner team and what its value means, amounts already in lakh / crore.
    counts as (
      select SCOPE_TYPE, SCOPE_CODE,
             listagg('- ' || substr(CATEGORY, 5) || ' (owner: ' || OWNER || '): ' || N || ' alerts, of which ' || P1 ||
                     ' are P1' || iff(left(CATEGORY, 1) in ('1', '2', '3') and P1 > 0, ' (the P1 ones alone: ' || P1Q ||
                     ' units, Rs ' || round(P1V / 1e7, 2) || ' crore)', '') ||
                     decode(left(CATEGORY, 1),
                            '1', '; all of them together: ' || Q || ' units to buy, worth Rs ' || round(V / 1e7, 2) || ' crore at selling price',
                            '2', '; all of them together: ' || Q || ' units to send from RDCs, worth Rs ' || round(V / 1e7, 2) || ' crore',
                            '3', '; all of them together: ' || Q || ' units to transfer between stores, worth Rs ' || round(V / 1e7, 2) || ' crore',
                            '4', ' (store-level issues; their values mix sale at risk, dead stock and sale lost vs last year, so do not quote a total)',
                            '5', '; all of them together: ' || Q || ' units to move out of or into store categories',
                            '6', '; DC excess over 90 days of demand worth Rs ' || round(V / 1e7, 2) || ' crore at cost, plus DC shortages',
                            '7', '; store categories below 85% of their ARS MBQ (norm), short ' || Q || ' units worth Rs ' || round(V / 1e7, 2) || ' crore at selling price', ''),
                     '\n') within group (order by CATEGORY) SUMMARY
      from bytype group by 1, 2)
    select :d, a.SCOPE_TYPE, a.SCOPE_CODE, a.TITLE,
           snowflake.cortex.complete('claude-sonnet-4-5',
             'You are the retail analytics assistant of V2 Retail, an Indian value-fashion chain. Below are today''s '
             || 'alerts for ' || a.TITLE || ' (as of ' || :d || '), produced by fixed rules from SAP sales and stock; the '
             || 'figures are exact. Write a short brief for the managers of this scope.\n'
             || 'Format: a first line of one sentence on the overall situation, then "Today''s priorities:" with 3 to 5 '
             || 'bullets naming the store / DC / category / article and the number behind each, then "Actions:" with '
             || 'one bullet per owner team that has work (Buying, Allocation / RDC, Store ops, Merchandising), giving that '
             || 'team''s own alert types only. '
             || 'Use only numbers that appear below, exactly as written; do not invent, estimate or add up figures. The '
             || '"all of them together" totals cover every alert of a type, not just the P1 ones, so never attribute them to '
             || 'P1. Values of different types measure different things and must never be summed. Use Rs, lakh and crore '
             || 'as given; at most 220 words; plain text, no markdown headings or bold.\n\n'
             || 'Alert types:\n' || coalesce(c.SUMMARY, '') || '\nTotal alerts: ' || a.N || ', of which P1 (act today): ' || a.P1
             || '\n\nTop alerts (priority | type | where | what | alert | action | value):\n'
             || a.LINES),
           a.N, a.P1, a.V, 'claude-sonnet-4-5', current_timestamp()::timestamp_ntz
    from agg a left join counts c using (SCOPE_TYPE, SCOPE_CODE);
    nb := sqlrowcount;
    commit;
  end if;

  return 'as of ' || d || ' | suggestions ' || n || ' | briefs ' || nb;
end;
$$
;

create or replace view V2RETAIL.PULSE.V_BI_AI_SUGGESTIONS comment = 'V2Pulse dataset: AI suggestions - alerts and actions'
as select s.*, 1 as N_F, iff(PRIORITY like 'P1%', 1, 0) as P1_F, iff(PRIORITY like 'P2%', 1, 0) as P2_F
from V2RETAIL.SILVER.AI_SUGGESTION s;

create or replace view V2RETAIL.PULSE.V_BI_AI_BRIEF comment = 'V2Pulse dataset: AI suggestions - Claude brief'
as select * from V2RETAIL.SILVER.AI_BRIEF;

grant select on view V2RETAIL.PULSE.V_BI_AI_SUGGESTIONS to role PULSE_SVC_ROLE;
grant select on view V2RETAIL.PULSE.V_BI_AI_BRIEF to role PULSE_SVC_ROLE;

-- Runs right after the hero build; a task with a predecessor cannot have its own schedule.
alter task V2RETAIL.SILVER.TASK_HERO_DAILY suspend;
create or replace task V2RETAIL.SILVER.TASK_AI_SUGGESTIONS
  warehouse = COMPUTE_WH
  comment = 'AI suggestions (V2Pulse): SILVER.SP_AI_SUGGESTIONS(true), after the hero build'
  after V2RETAIL.SILVER.TASK_HERO_DAILY
as call V2RETAIL.SILVER.SP_AI_SUGGESTIONS(true);
alter task V2RETAIL.SILVER.TASK_AI_SUGGESTIONS resume;
alter task V2RETAIL.SILVER.TASK_HERO_DAILY resume;
