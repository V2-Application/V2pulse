create or replace procedure V2RETAIL.SILVER.SP_HERO_BUILD(FULL_REBUILD boolean)
returns varchar
language sql
comment = 'Hero Articles: rebuilds HERO_DIM_GENART, re-states the last 4 days of HERO_SALE_DAILY (all history when FULL_REBUILD) and writes today''s HERO_POSITION snapshot.'
execute as owner
as
$$
declare
  d        date;      -- sale anchor: last sale day loaded
  sd       date;      -- stock day used
  pd       date;      -- pipeline (PRD / PTL / HUB PRD) day used
  from_dt  date;
  n_sale   integer;
  n_pos    integer;
begin
  select max(DT) into :d from V2RETAIL.BRONZE.DAY_WISE_ST_VAR_SALE;
  select max(STOCK_DATE)::date into :sd from V2RETAIL.BRONZE.ET_STOCK_DATA
   where STOCK_DATE >= dateadd(day, -10, :d) and STOCK_DATE <= :d;
  select max(DT) into :pd from V2RETAIL.BRONZE.DAY_WISE_ST_VAR_STOCK
   where DT >= dateadd(day, -10, :d) and DT <= :d and KPI in ('DH24_PRD', 'DW01_PRD', 'F_HUBPRD', 'G_HUBPRD', 'PTL');
  from_dt := iff(FULL_REBUILD, '2025-04-01'::date, dateadd(day, -3, d));

  -- 1. Generic article dimension (one row per generic article). Articles missing from the article master
  --    (~0.3% of position rows) fall back to the sale feed's description / category, with the division and
  --    sub-division of that category from ZMC_MASTER.
  create or replace transient table V2RETAIL.SILVER.HERO_VARMAP as
  select VAR_ARTICLE, GEN_ART_NUMBER, SIZE_1 from V2RETAIL.GOLD.V_VAR_ARTICLE_MASTER where GEN_ART_NUMBER is not null;

  create or replace table V2RETAIL.SILVER.HERO_DIM_GENART
    comment = 'Hero Articles: generic article attributes. Built by SILVER.SP_HERO_BUILD.' as
  with am as (
    select GEN_ART_NUMBER,
           nullif(any_value(trim(GEN_ART_DESC)), '') GEN_ART_DESC, mode(SEG) SEG, mode(DIV) as DIV, mode(SUB_DIV) SUB_DIV,
           mode(MAJ_CAT) MAJ_CAT, mode(MC_DESC) MC_DESC, max(MRP) MRP, mode(MERGE_VENDOR_NAME) VENDOR_NAME,
           mode(RNG_SEG) RNG_SEG, mode(SSN) SSN, mode(M_FIT) FIT, mode(M_PATTERN) PATTERN,
           mode(M_FAB_MAIN_MVGR_1) FABRIC, min(CREATE_DATE) CREATE_DATE, count(distinct SIZE_1) SIZES,
           count(distinct COLOR) COLOURS
    from V2RETAIL.GOLD.V_VAR_ARTICLE_MASTER where GEN_ART_NUMBER is not null group by 1),
  zmc as (
    select MAJ_CAT, mode(SEG) SEG, mode(DIVISION) as DIV, mode(SUB_DIVISION) SUB_DIV
    from V2RETAIL.BRONZE.ZMC_MASTER where MAJ_CAT is not null group by 1),
  sd as (
    select GEN_ART_NUMBER, any_value(nullif(trim(split_part(GEN_ART_DESC, ',', 1)), '')) GEN_ART_DESC, mode(MAJ_CAT) MAJ_CAT,
           mode(SEG) SEG, max(SALE_V / nullif(SALE_Q, 0)) MRP
    from V2RETAIL.BRONZE.DAY_WISE_ST_VAR_SALE
    where DT > dateadd(day, -400, :d) and GEN_ART_NUMBER is not null
    group by 1)
  select am.GEN_ART_NUMBER, coalesce(am.GEN_ART_DESC, sd.GEN_ART_DESC, am.MAJ_CAT || ' (no description in SAP)') GEN_ART_DESC,
         am.SEG, am.DIV, am.SUB_DIV, am.MAJ_CAT, am.MC_DESC, am.MRP, am.VENDOR_NAME, am.RNG_SEG, am.SSN, am.FIT,
         am.PATTERN, am.FABRIC, am.CREATE_DATE, am.SIZES, am.COLOURS
  from am left join sd on sd.GEN_ART_NUMBER = am.GEN_ART_NUMBER
  union all
  select sd.GEN_ART_NUMBER, coalesce(sd.GEN_ART_DESC, sd.MAJ_CAT || ' (no description in SAP)'), coalesce(sd.SEG, z.SEG), z.DIV,
         z.SUB_DIV, sd.MAJ_CAT, null, sd.MRP, null, null, null, null, null, null, null, null, null
  from sd left join zmc z on z.MAJ_CAT = sd.MAJ_CAT
  where sd.GEN_ART_NUMBER not in (select GEN_ART_NUMBER from am);

  -- 2. Daily sale fact, with the same store x article 364 days earlier alongside.
  --    Source per day: BRONZE.DAY_WISE_ST_VAR_SALE, except days it is nearly empty (Apr-Jun 2025 and Oct 2025 -
  --    Jan 2026), which come from GOLD.FACT_STORE_STOCK_SALE_DAILY (sale value / qty only, no GM or discount,
  --    and ~4% higher than DAY_WISE on months both cover). The choice is kept in HERO_SALE_SOURCE_DAY.
  create or replace table V2RETAIL.SILVER.HERO_SALE_SOURCE_DAY
    comment = 'Hero Articles: which sale feed HERO_SALE_DAILY uses for each day, and both feeds'' totals.' as
  with a as (select DT, sum(SALE_V) V from V2RETAIL.BRONZE.DAY_WISE_ST_VAR_SALE group by 1),
       b as (select DT, sum(SALE_V) V from V2RETAIL.GOLD.FACT_STORE_STOCK_SALE_DAILY group by 1)
  select coalesce(a.DT, b.DT) DT, a.V DAY_WISE_V, b.V FACT_DAILY_V,
         iff(coalesce(a.V, 0) < 0.5 * coalesce(b.V, 0), 'FACT_DAILY', 'DAY_WISE') SRC
  from a full outer join b on b.DT = a.DT;

  -- Each delete + reload is one transaction: a second run that overlaps (the task and a manual call) waits for
  -- the first to commit and then replaces its rows, instead of both inserting after both deleted.
  begin transaction;
  delete from V2RETAIL.SILVER.HERO_SALE_DAILY where DT >= :from_dt;
  insert into V2RETAIL.SILVER.HERO_SALE_DAILY
    (DT, ST_CD, GEN_ART_NUMBER, SEG, DIV, SUB_DIV, MAJ_CAT, SALE_V, SALE_Q, GM_V, DISC_V, LY_SALE_V, LY_SALE_Q, LY_GM_V,
     SRC, BUILT_AT)
  with src as (
    select s.DT, s.ST_CD, s.GEN_ART_NUMBER, s.SALE_V, s.SALE_Q, s.GM_V, s.DISCOUNT_V DISC_V, s.MAJ_CAT, s.SEG
    from V2RETAIL.BRONZE.DAY_WISE_ST_VAR_SALE s
    join V2RETAIL.SILVER.HERO_SALE_SOURCE_DAY x on x.DT = s.DT and x.SRC = 'DAY_WISE'
    where s.GEN_ART_NUMBER is not null
      and (s.DT between :from_dt and :d or s.DT between dateadd(day, -364, :from_dt) and dateadd(day, -364, :d))
    union all
    select f.DT, f.STORE_CODE, m.GEN_ART_NUMBER, f.SALE_V, f.SALE_Q, null, null, null, null
    from V2RETAIL.GOLD.FACT_STORE_STOCK_SALE_DAILY f
    join V2RETAIL.SILVER.HERO_SALE_SOURCE_DAY x on x.DT = f.DT and x.SRC = 'FACT_DAILY'
    join V2RETAIL.SILVER.HERO_VARMAP m on m.VAR_ARTICLE = try_to_number(f.ARTICLE_CODE)
    where f.DT between :from_dt and :d or f.DT between dateadd(day, -364, :from_dt) and dateadd(day, -364, :d)),
  cy as (
    select DT, ST_CD, GEN_ART_NUMBER, sum(SALE_V) SALE_V, sum(SALE_Q) SALE_Q, sum(GM_V) GM_V, sum(DISC_V) DISC_V,
           any_value(MAJ_CAT) MAJ_CAT, any_value(SEG) SEG
    from src where DT between :from_dt and :d group by 1, 2, 3),
  ly as (
    select dateadd(day, 364, DT) DT, ST_CD, GEN_ART_NUMBER, sum(SALE_V) SALE_V, sum(SALE_Q) SALE_Q, sum(GM_V) GM_V,
           any_value(MAJ_CAT) MAJ_CAT, any_value(SEG) SEG
    from src where DT between dateadd(day, -364, :from_dt) and dateadd(day, -364, :d) group by 1, 2, 3)
  select coalesce(cy.DT, ly.DT), coalesce(cy.ST_CD, ly.ST_CD), coalesce(cy.GEN_ART_NUMBER, ly.GEN_ART_NUMBER),
         coalesce(g.SEG, cy.SEG, ly.SEG), g.DIV, g.SUB_DIV, coalesce(g.MAJ_CAT, cy.MAJ_CAT, ly.MAJ_CAT),
         cy.SALE_V, cy.SALE_Q, cy.GM_V, cy.DISC_V, ly.SALE_V, ly.SALE_Q, ly.GM_V,
         x.SRC, current_timestamp()::timestamp_ntz
  from cy
  full outer join ly on ly.DT = cy.DT and ly.ST_CD = cy.ST_CD and ly.GEN_ART_NUMBER = cy.GEN_ART_NUMBER
  left join V2RETAIL.SILVER.HERO_DIM_GENART g on g.GEN_ART_NUMBER = coalesce(cy.GEN_ART_NUMBER, ly.GEN_ART_NUMBER)
  left join V2RETAIL.SILVER.HERO_SALE_SOURCE_DAY x on x.DT = coalesce(cy.DT, ly.DT);
  n_sale := sqlrowcount;
  commit;

  -- 3. Today's position snapshot.
  begin transaction;
  delete from V2RETAIL.SILVER.HERO_POSITION where AS_OF_DT = :d or AS_OF_DT < dateadd(day, -13, :d);
  insert into V2RETAIL.SILVER.HERO_POSITION
  with
  win as (
    select ST_CD, GEN_ART_NUMBER,
           sum(iff(DT > dateadd(day, -7, :d), SALE_V, 0))                                         V7,
           sum(iff(DT > dateadd(day, -7, :d), SALE_Q, 0))                                         Q7,
           sum(iff(DT between dateadd(day, -13, :d) and dateadd(day, -7, :d), SALE_V, 0))        VP7,
           sum(iff(DT between dateadd(day, -13, :d) and dateadd(day, -7, :d), SALE_Q, 0))        QP7,
           sum(iff(DT > dateadd(day, -30, :d), SALE_V, 0))                                        V30,
           sum(iff(DT > dateadd(day, -30, :d), SALE_Q, 0))                                        Q30,
           sum(iff(DT > dateadd(day, -30, :d), GM_V, 0))                                          GM30,
           sum(iff(DT > dateadd(day, -30, :d), DISC_V, 0))                                        D30,
           sum(iff(DT >= date_trunc(month, :d), SALE_V, 0))                                       VMTD,
           sum(iff(DT > dateadd(day, -30, :d), LY_SALE_V, 0))                                     LYV30,
           sum(iff(DT > dateadd(day, -30, :d), LY_SALE_Q, 0))                                     LYQ30
    from V2RETAIL.SILVER.HERO_SALE_DAILY
    where DT between least(dateadd(day, -29, :d), date_trunc(month, :d)) and :d
    group by 1, 2),
  co as (
    select w.GEN_ART_NUMBER, any_value(g.MAJ_CAT) MAJ_CAT, sum(w.V30) V30, sum(w.Q30) Q30,
           row_number() over (order by sum(w.V30) desc, w.GEN_ART_NUMBER) CO_RANK,
           row_number() over (partition by any_value(g.MAJ_CAT) order by sum(w.V30) desc, w.GEN_ART_NUMBER) MC_RANK
    from win w left join V2RETAIL.SILVER.HERO_DIM_GENART g on g.GEN_ART_NUMBER = w.GEN_ART_NUMBER
    -- gift / marketing, non-trading, trims & accessories, fabric and scrap are never heroes
    where coalesce(g.SEG, '') not in ('MKT', 'NT', 'ACC', 'FAB') and coalesce(g.DIV, '') <> 'SCRAP'
    group by 1 having sum(w.V30) > 0),
  str as (
    select ST_CD, GEN_ART_NUMBER, row_number() over (partition by ST_CD order by V30 desc, GEN_ART_NUMBER) ST_RANK
    from win where V30 > 0),
  sf_st as (
    select ST_CD, MAJ_CAT,
           sum(iff(DT <= dateadd(day, -364, :d), SALE_Q, 0)) LYL,
           sum(iff(DT >  dateadd(day, -364, :d), SALE_Q, 0)) LYN
    from V2RETAIL.SILVER.HERO_SALE_DAILY
    where DT between dateadd(day, -393, :d) and dateadd(day, -334, :d)
    group by 1, 2),
  sf_co as (select MAJ_CAT, sum(LYL) LYL, sum(LYN) LYN from sf_st group by 1),
  stk_v as (
    select e.WERKS ST_CD, m.GEN_ART_NUMBER, m.SIZE_1,
           sum(iff(e.LGORT = '0001', e.LABST, 0)) FLOOR_Q, sum(iff(e.LGORT = '0002', e.LABST, 0)) BACK_Q
    from V2RETAIL.BRONZE.ET_STOCK_DATA e
    join V2RETAIL.SILVER.HERO_VARMAP m on m.VAR_ARTICLE = try_to_number(e.MATNR)
    where e.STOCK_DATE = :sd and e.LGORT in ('0001', '0002')
    group by 1, 2, 3),
  stk as (select ST_CD, GEN_ART_NUMBER, sum(FLOOR_Q) FLOOR_Q, sum(BACK_Q) BACK_Q from stk_v group by 1, 2),
  dc as (
    select GEN_ART_NUMBER,
           sum(iff(ST_CD = 'DH24', FLOOR_Q, 0)) DH24_Q, sum(iff(ST_CD = 'DW01', FLOOR_Q, 0)) DW01_Q,
           sum(iff(ST_CD = 'DK04', FLOOR_Q, 0)) DK04_Q, sum(FLOOR_Q) ALL_Q
    from stk where ST_CD in ('DH24', 'DW01', 'DK04') group by 1),
  vs as (
    select GEN_ART_NUMBER, SIZE_1, sum(SALE_Q) Q from V2RETAIL.BRONZE.DAY_WISE_ST_VAR_SALE
    where DT > dateadd(day, -30, :d) and DT <= :d group by 1, 2 having sum(SALE_Q) > 0),
  core as (
    select GEN_ART_NUMBER, SIZE_1 from (
      select GEN_ART_NUMBER, SIZE_1, Q / nullif(sum(Q) over (partition by GEN_ART_NUMBER), 0) SH from vs)
    where SH >= 0.05),
  core_n as (select GEN_ART_NUMBER, count(*) N from core group by 1),
  st_sz as (
    select s.ST_CD, s.GEN_ART_NUMBER, count(distinct iff(s.FLOOR_Q + s.BACK_Q > 0, s.SIZE_1, null)) N
    from stk_v s join core c on c.GEN_ART_NUMBER = s.GEN_ART_NUMBER and c.SIZE_1 = s.SIZE_1
    group by 1, 2),
  pipe as (
    select ST_CD, GEN_ART_NUMBER, sum(STOCK_Q) Q from V2RETAIL.BRONZE.DAY_WISE_ST_VAR_STOCK
    where DT = :pd and KPI in ('DH24_PRD', 'DW01_PRD', 'F_HUBPRD', 'G_HUBPRD', 'PTL') group by 1, 2),
  intr as (
    select FINAL_DEST ST_CD, try_to_number(GEN_ART) GEN_ART_NUMBER, sum(QTY) Q from V2RETAIL.GOLD.HUB_INTRANSIT_HU_ART
    where G_FATHER = 'INTRANSIT-UP' and FINAL_DEST_TYPE = 'STORE' group by 1, 2),
  -- Operating stores = sold anything in the last 7 days; their RDC from the store dimension.
  ops as (
    select w.ST_CD, any_value(s.RDC_CD) RDC_CD from win w left join V2RETAIL.GOLD.DIM_STORE_V2 s on s.STORE_CODE = w.ST_CD
    where w.Q7 > 0 group by 1),
  age as (select try_to_number(GEN_ART) GEN_ART_NUMBER, min(AGING_DAYS) AGE_DAYS from V2RETAIL.GOLD.ARS_ART_AGING group by 1),
  fs as (select GEN_ART_NUMBER, min(DT) FIRST_SALE_DT from V2RETAIL.SILVER.HERO_SALE_DAILY where SALE_Q > 0 group by 1),
  keys as (
    select ST_CD, GEN_ART_NUMBER from win where V30 <> 0 or Q30 <> 0
    union select ST_CD, GEN_ART_NUMBER from stk
      where FLOOR_Q + BACK_Q > 0 and ST_CD not in ('DH24', 'DW01', 'DK04')
        and GEN_ART_NUMBER in (select GEN_ART_NUMBER from co)
    union select ST_CD, GEN_ART_NUMBER from pipe where GEN_ART_NUMBER in (select GEN_ART_NUMBER from co)
    union select ST_CD, GEN_ART_NUMBER from intr where GEN_ART_NUMBER in (select GEN_ART_NUMBER from co)
    union select o.ST_CD, c.GEN_ART_NUMBER from ops o cross join co c where c.CO_RANK <= 500),
  base as (
    select k.ST_CD, k.GEN_ART_NUMBER, g.SEG, g.DIV, g.SUB_DIV, g.MAJ_CAT, g.MRP, g.CREATE_DATE,
           c.CO_RANK, c.MC_RANK, r.ST_RANK, c.V30 CO_V30, c.Q30 CO_Q30,
           coalesce(w.V7, 0) V7, coalesce(w.Q7, 0) Q7, coalesce(w.VP7, 0) VP7, coalesce(w.QP7, 0) QP7,
           coalesce(w.V30, 0) V30, coalesce(w.Q30, 0) Q30, coalesce(w.GM30, 0) GM30, coalesce(w.D30, 0) D30,
           coalesce(w.VMTD, 0) VMTD, coalesce(w.LYV30, 0) LYV30, coalesce(w.LYQ30, 0) LYQ30,
           greatest(coalesce(s.FLOOR_Q, 0), 0) FLOOR_Q, greatest(coalesce(s.BACK_Q, 0), 0) BACK_Q,
           greatest(coalesce(p.Q, 0), 0) PIPE_Q, greatest(coalesce(i.Q, 0), 0) INTR_Q,
           greatest(coalesce(case o.RDC_CD when 'DH24' then dc.DH24_Q when 'DW01' then dc.DW01_Q when 'DK04' then dc.DK04_Q
                                           else dc.ALL_Q end, 0), 0) RDC_Q,
           greatest(coalesce(dc.ALL_Q, 0), 0) ALL_DC_Q,
           least(greatest(coalesce(iff(sf.LYL >= 20 and sf.LYN > 0, sf.LYN / sf.LYL, null),
                                   iff(sc.LYL >= 100 and sc.LYN > 0, sc.LYN / sc.LYL, null), 1), 0.5), 3) SF,
           coalesce(cn.N, 0) CORE_SIZES, coalesce(z.N, 0) CORE_SIZES_STK,
           coalesce(a.AGE_DAYS, datediff(day, g.CREATE_DATE, :d)) AGE_DAYS, f.FIRST_SALE_DT
    from keys k
    left join win w                         on w.ST_CD = k.ST_CD and w.GEN_ART_NUMBER = k.GEN_ART_NUMBER
    left join V2RETAIL.SILVER.HERO_DIM_GENART g on g.GEN_ART_NUMBER = k.GEN_ART_NUMBER
    left join co c                          on c.GEN_ART_NUMBER = k.GEN_ART_NUMBER
    left join str r                         on r.ST_CD = k.ST_CD and r.GEN_ART_NUMBER = k.GEN_ART_NUMBER
    left join stk s                         on s.ST_CD = k.ST_CD and s.GEN_ART_NUMBER = k.GEN_ART_NUMBER
    left join pipe p                        on p.ST_CD = k.ST_CD and p.GEN_ART_NUMBER = k.GEN_ART_NUMBER
    left join intr i                        on i.ST_CD = k.ST_CD and i.GEN_ART_NUMBER = k.GEN_ART_NUMBER
    left join ops o                         on o.ST_CD = k.ST_CD
    left join dc                            on dc.GEN_ART_NUMBER = k.GEN_ART_NUMBER
    left join sf_st sf                      on sf.ST_CD = k.ST_CD and sf.MAJ_CAT = g.MAJ_CAT
    left join sf_co sc                      on sc.MAJ_CAT = g.MAJ_CAT
    left join core_n cn                     on cn.GEN_ART_NUMBER = k.GEN_ART_NUMBER
    left join st_sz z                       on z.ST_CD = k.ST_CD and z.GEN_ART_NUMBER = k.GEN_ART_NUMBER
    left join age a                         on a.GEN_ART_NUMBER = k.GEN_ART_NUMBER
    left join fs f                          on f.GEN_ART_NUMBER = k.GEN_ART_NUMBER),
  calc as (
    select b.*,
           0.6 * Q7 / 7 + 0.4 * Q30 / 30                                                   ROS,
           FLOOR_Q + BACK_Q                                                                ST_STK,
           FLOOR_Q + BACK_Q + PIPE_Q + INTR_Q                                              AVAIL,
           coalesce(V30 / nullif(Q30, 0), CO_V30 / nullif(CO_Q30, 0), MRP)                 ASP_
    from base b),
  flags as (
    select c.*,
           ROS * 30 * SF                                                                   FCST,
           iff(Q30 > 0, 1, 0)                                                              SELLING_F,
           iff(ST_STK > 0, 1, 0)                                                           STOCKED_F,
           iff(Q30 > 0 and ST_STK <= 0, 1, 0)                                              OOS_F,
           iff(Q30 > 0 and ST_STK > 0 and ROS > 0 and ST_STK / ROS < 7, 1, 0)              LOW_COVER_F,
           iff(Q30 > 0 and ST_STK > 0 and CORE_SIZES >= 3 and CORE_SIZES_STK / CORE_SIZES < 0.6, 1, 0) BROKEN_SIZE_F,
           iff(coalesce(CO_RANK, 99999) <= 500 and Q30 <= 0 and ST_STK <= 0 and PIPE_Q + INTR_Q <= 0, 1, 0) NOT_RANGED_F,
           iff(ST_STK > 0 and (ROS = 0 or ST_STK / ROS > 90), 1, 0)                        EXCESS_F
    from calc c)
  select :d, :sd, ST_CD, GEN_ART_NUMBER, SEG, DIV, SUB_DIV, MAJ_CAT,
         CO_RANK, MC_RANK, ST_RANK,
         case when SEG in ('MKT', 'NT', 'ACC', 'FAB') or DIV = 'SCRAP' then 'X · Non-merchandise' when CO_RANK <= 100 then 'A · Top 100'
              when CO_RANK <= 500 then 'B · 101-500' when CO_RANK <= 2000 then 'C · 501-2000' else 'D · Rest' end,
         case when MC_RANK <= 10 then 'Top 10 in category' when MC_RANK <= 50 then 'Top 11-50 in category'
              else 'Other' end,
         iff(ST_RANK <= 100, 'Y', 'N'),
         V7, Q7, VP7, QP7, V30, Q30, GM30, D30, Q30 * MRP, VMTD, LYV30, LYQ30,
         FLOOR_Q, BACK_Q, PIPE_Q, INTR_Q, RDC_Q, ALL_DC_Q,
         ROS, SF, FCST, AVAIL,
         round(greatest(FCST - AVAIL, 0), 0),
         ASP_,
         round(greatest(FCST - AVAIL, 0), 0) * ASP_,
         iff(OOS_F = 1, ROS * 7 * ASP_, 0),
         iff(ST_STK > 0, greatest(ST_STK - ROS * 90, 0), 0),
         CORE_SIZES, CORE_SIZES_STK,
         SELLING_F, STOCKED_F, OOS_F, LOW_COVER_F, BROKEN_SIZE_F, NOT_RANGED_F, EXCESS_F,
         case when OOS_F = 1 then '1 · Out of stock' when NOT_RANGED_F = 1 then '2 · Hero not ranged'
              when LOW_COVER_F = 1 then '3 · Low cover (<7 days)' when BROKEN_SIZE_F = 1 then '4 · Broken sizes'
              when EXCESS_F = 1 then '5 · Excess (>90 days)' when SELLING_F = 1 then '6 · Healthy'
              else '7 · Inbound only' end,
         case when OOS_F = 1 and PIPE_Q + INTR_Q > 0 then 'Stock on the way - expedite'
              when OOS_F = 1 and RDC_Q > 0 then 'Replenish from own RDC'
              when OOS_F = 1 and ALL_DC_Q > 0 then 'Replenish from another RDC'
              when OOS_F = 1 then 'No DC stock - reorder or inter-store transfer'
              when NOT_RANGED_F = 1 and RDC_Q > 0 then 'Allocate - hero not in this store'
              when NOT_RANGED_F = 1 then 'Hero not ranged - no DC stock'
              when LOW_COVER_F = 1 and PIPE_Q + INTR_Q > 0 then 'Low cover - stock on the way'
              when LOW_COVER_F = 1 and RDC_Q > 0 then 'Replenish from own RDC'
              when LOW_COVER_F = 1 then 'Low cover - no DC stock'
              when BROKEN_SIZE_F = 1 then 'Fill missing core sizes'
              when EXCESS_F = 1 then 'Excess - transfer out or mark down'
              else 'No action' end,
         AGE_DAYS, FIRST_SALE_DT, current_timestamp()::timestamp_ntz
  from flags;
  n_pos := sqlrowcount;
  commit;

  -- 4. Current hero tier per article, for the time-series dataset (small lookup, avoids scanning the snapshot).
  create or replace table V2RETAIL.SILVER.HERO_TIER_CURRENT
    comment = 'Hero Articles: latest company hero tier and ranks per generic article. Built by SILVER.SP_HERO_BUILD.' as
  select GEN_ART_NUMBER, any_value(HERO_TIER) HERO_TIER, any_value(MC_HERO) MC_HERO, min(CO_RANK) CO_RANK,
         min(MC_RANK) MC_RANK, any_value(AS_OF_DT) AS_OF_DT
  from V2RETAIL.SILVER.HERO_POSITION where AS_OF_DT = :d group by 1;

  -- 5. The Analytics dashboard's stacked table (position snapshot + daily sale), pre-joined to the masters.
  --    Kept up to date incrementally: today's position rows and the re-stated sale days are swapped in.
  --    No cluster key or sort: re-clustering a table this size every day would cost more than it saves.
  if (FULL_REBUILD) then
    create or replace table V2RETAIL.SILVER.HERO_ANALYTICS
      comment = 'Analytics (V2Pulse): latest position rows + daily sale rows, from SILVER.V_HERO_ANALYTICS_SRC. Built by SILVER.SP_HERO_BUILD.' as
    select * from V2RETAIL.SILVER.V_HERO_ANALYTICS_SRC;
  else
    begin transaction;
    delete from V2RETAIL.SILVER.HERO_ANALYTICS where ROW_KIND = 'POS' or (ROW_KIND = 'SALE' and DT >= :from_dt);
    insert into V2RETAIL.SILVER.HERO_ANALYTICS select * from V2RETAIL.SILVER.V_HERO_ANALYTICS_SRC where ROW_KIND = 'POS';
    insert into V2RETAIL.SILVER.HERO_ANALYTICS select * from V2RETAIL.SILVER.V_HERO_ANALYTICS_SRC
      where ROW_KIND = 'SALE' and DT >= :from_dt;
    commit;
  end if;

  return 'sale anchor ' || d || ', stock ' || coalesce(sd::varchar, 'none') || ', pipeline ' || coalesce(pd::varchar, 'none')
         || ' | sale rows ' || n_sale || ' | position rows ' || n_pos;
end;
$$
