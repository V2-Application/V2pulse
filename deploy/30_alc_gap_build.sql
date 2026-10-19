-- Allocation gap reports: the 22 gap formats in Desktop\New folder\Allocation, built per ARS allocation session from
-- the run files the gap-allocation workbook pastes (loaded by load_ars.py) and the workbook's own masters
-- (load_masters.py). One stacked table, one row per report x checked entity, all rows kept (OK and GAP).
use role ACCOUNTADMIN;
use warehouse COMPUTE_WH;

create table if not exists V2RETAIL.SILVER.ALC_GAP_CONFIG (KEY varchar, NUM_VALUE float, NOTE varchar);
merge into V2RETAIL.SILVER.ALC_GAP_CONFIG t using (
  select $1 KEY, $2 NUM_VALUE, $3 NOTE from values
    ('OL_FR_HIGH', 1.20, 'ST one-liner: fill rate after allocation above this is HIGH (format 120%; the workbook flags use 110%)'),
    ('OL_FR_LOW', 0.80, 'ST one-liner: fill rate after allocation below this is LOW (format 80%; the workbook flags use 90%)'),
    ('GND_DIFF', 10, 'Installed vs allocation gandola: absolute difference above this is a GAP'),
    ('MJ_FR_HIGH', 1.20, 'ST-MJ fill rate above this is HIGH'),
    ('MJ_FR_LOW', 0.80, 'ST-MJ fill rate below this is LOW'),
    ('EXC_BEFORE_FR', 1.10, 'Excess allocation: store-MJ was already above this fill rate before the run, yet got allocation'),
    ('EXCESS_BAND', 1.10, 'Excess after allocation = stock above MBQ x this (ARS tolerance band)'),
    ('BGT_ALC_DIFF_HIGH', 3000, 'BGT vs ALC display: ALC - BGT above this is a GAP'),
    ('BGT_ALC_DIFF_LOW', -1000, 'BGT vs ALC display: ALC - BGT below this is a GAP'),
    ('BGT_ALC_PCT', 0.05, 'BGT vs ALC display: |ALC/BGT - 1| above this is a GAP'),
    ('LOW_MSA_SHORT', 3000, 'Zero or low MSA: CO-MJ short above this ...'),
    ('LOW_MSA_REM', 3000, '... while remaining MSA is below this'),
    ('NOALC_SHORT', 100, 'MSA available vs no ALC: CO-MJ short above this ...'),
    ('NOALC_MSA', 1000, '... remaining MSA above this, and zero allocation'),
    ('GRT_SHORT', 100, 'GRT stock available but store short: CO-MJ short above this, no MSA, processed GRT stock > 0'),
    ('PRIO_SHORT', 1000, 'PROC / UN-PROC GRT vs CO short: CO-MJ short above this ...'),
    ('PRIO_GRT', 1000, '... and GRT stock above this: prioritise these major-category bins in GRT'),
    ('HOLD_CONTB', 0.30, 'Hold gap: hold share of (remaining MSA + hold) above this while the CO-MJ is short'),
    ('MSA_NODISP', 1000, 'MSA available but display not given: remaining MSA above this with zero budget display'),
    ('HIGH_MSA_GM', 5000, 'High MSA, GM: remaining MSA of a generic-colour above this'),
    ('HIGH_MSA_OTH', 3000, 'High MSA, other segments: remaining MSA of a generic-colour above this'),
    ('LOWMSA_REM', 50, 'Low MSA, ALC not done: generic-colour remaining MSA below this (and above 0) while its MJ-RDC is short'),
    ('PACK_MSA', 100, 'Pack size: only GM, non-SSNL, active MJs with remaining MSA above this'),
    ('KEEP_DAYS', 30, 'Run days kept in SILVER.ALC_GAP_ROWS')
) s on t.KEY = s.KEY
when not matched then insert (KEY, NUM_VALUE, NOTE) values (s.KEY, s.NUM_VALUE, s.NOTE)
when matched then update set NOTE = s.NOTE;

create or replace table V2RETAIL.SILVER.ALC_GAP_REPORT_DEF (
  REPORT_CODE varchar, REPORT_NAME varchar, LEVEL varchar, SUBJECT varchar, RULE varchar, SORT_NO number);
insert into V2RETAIL.SILVER.ALC_GAP_REPORT_DEF values
  ('ST01', 'ST-ONE-LINER FILLRATE GAP', 'ST', 'FR% GAP', 'Store fill rate after allocation (stock / MBQ) above OL_FR_HIGH or below OL_FR_LOW', 1),
  ('ST02', 'INSTALLED VS ALC GND', 'ST', 'GND GAP', 'Installed gandola - allocation gandola outside +/- GND_DIFF, or either not given', 2),
  ('ST03', 'ZERO ALC IN SCHEDULE ST', 'ST', 'ALC CHECK', 'Store scheduled for the run day (SCH-ST) but got zero allocation', 3),
  ('ST04', 'DISP GIVEN BUT ALC NOT DONE', 'ST', 'DISP GAP', 'Store has budget display but got zero allocation', 4),
  ('SM01', 'ST-MJ DISPLAY NOT GIVEN STILL ALC DONE', 'ST-MJ', 'DISP GAP', 'Budget display is zero but the store-MJ got allocation', 5),
  ('SM02', 'DISP VS ACC-DENSITY', 'ST-MJ', 'DISP GAP', 'Budget display below the accessory density (one fixture''s capacity)', 6),
  ('SM03', 'EXCESS ALC @ ST-MJ LEVEL', 'ST-MJ', 'ALC CHECK', 'Fill rate before the run above EXC_BEFORE_FR, yet allocation was done', 7),
  ('SM04', 'MSA AVAILABLE VS STORE SHORT', 'ST-MJ', 'ALC CHECK', 'Store-MJ short after allocation while MSA of the MJ remains at the RDCs', 8),
  ('SM05', 'ST-MJ FILLRATE GAP', 'ST-MJ', 'FR% GAP', 'Store-MJ fill rate after allocation above MJ_FR_HIGH or below MJ_FR_LOW (zero-MBQ rows shown as NO MBQ)', 9),
  ('CM01', 'ZERO ALLOCATION GAP (MAJ-CAT)', 'CO-MJ', 'ZERO ALC', 'MJ short across stores, MSA remaining, zero allocation', 10),
  ('CM02', 'ZERO OR LOW MSA GAP', 'CO-MJ', 'MSA GAP', 'MJ short above LOW_MSA_SHORT with remaining MSA below LOW_MSA_REM', 11),
  ('CM03', 'MSA STK AVAILABLE VS NO ALC (CO-MJ)', 'CO-MJ', 'ZERO ALC', 'MJ short above NOALC_SHORT, MSA above NOALC_MSA, zero allocation', 12),
  ('CM04', 'GRT STOCK AVAILABLE BUT ST-SHORT (CO-MJ)', 'CO-MJ', 'GRT GAP', 'MJ short above GRT_SHORT, no MSA left, processed GRT stock available', 13),
  ('CM05', 'PROC-GRT VS CO-SHORT', 'CO-MJ', 'GRT GAP', 'MJ short and processed GRT stock both above the PRIO thresholds', 14),
  ('CM06', 'UN-PROC-GRT VS CO-SHORT', 'CO-MJ', 'GRT GAP', 'MJ short and un-processed GRT (0032) stock both above the PRIO thresholds', 15),
  ('CM07', 'HOLD GAP', 'CO-MJ', 'HOLD GAP', 'MJ short while hold is more than HOLD_CONTB of (remaining MSA + hold)', 16),
  ('CM08', 'HOLD MSA GAP', 'CO-MJ', 'HOLD GAP', 'GM with any hold; others: hold with no free MSA while short', 17),
  ('CM09', 'MSA AVAILABLE BUT DISP NOT GIVEN', 'CO-MJ', 'DISP GAP', 'No budget display for the MJ while remaining MSA is above MSA_NODISP', 18),
  ('CM10', 'BGT-DISP VS ALC-DISP', 'CO-MJ', 'DISP GAP', 'Allocation display differs from budget display beyond the BGT_ALC limits', 19),
  ('CM11', 'PACK-SIZE GAP', 'CO-MJ', 'MSA GAP', 'GM active MJ with MSA: half the accessory density is below the pack size', 20),
  ('GC01', 'HIGH MSA GAP', 'GEN-CLR', 'MSA GAP', 'Remaining MSA of a generic-colour at an RDC above HIGH_MSA_GM / HIGH_MSA_OTH', 21),
  ('GC02', 'LOW MSA-Q, ALC NOT DONE (MJ-RDC)', 'GEN-CLR', 'MSA GAP', 'Generic-colour with a little MSA (below LOWMSA_REM) not allocated while its MJ-RDC is short: manual push', 22);

create table if not exists V2RETAIL.SILVER.ALC_GAP_ROWS (
  RUN_DATE date, SESSION_ID varchar, APPROVED_AT timestamp_ntz,
  REPORT_CODE varchar, REPORT_NAME varchar, LEVEL varchar, SUBJECT varchar,
  GAP_FLAG varchar, IS_GAP number(1), GAP_QTY float,
  ST_CD varchar, ST_NM varchar, ST_STAT varchar, ST_OP_DT date, RDC varchar,
  SEG varchar, DIV varchar, SUB_DIV varchar, MAJ_CAT varchar, SSN varchar, MJ_STS varchar, SZ_APP varchar,
  GEN_ART varchar, CLR varchar,
  SCHLD number, BGT_DISP float, ALC_DISP float, DIFF_Q float, INSTALLED_GND float, ALC_GND float, BGT_GND float,
  ACC_DENS float, PAK_SZ float, MBQ float, BEFORE_STK float, ALC_Q float, AFTER_STK float, SHORT_AFT float,
  EXCESS_AFT float, EXCESS_ALC float, OPT_SHORT float, REM_MSA float, REM_MSA_OPT float, HOLD_MSA float, TTL_MSA float,
  PROC_GRT float, PROC_GRT_OPT float, UNPROC_GRT float, UNPROC_GRT_OPT float,
  BUILT_AT timestamp_ntz)
  cluster by (RUN_DATE, REPORT_CODE);

create table if not exists V2RETAIL.SILVER.ALC_GAP_DAILY (
  RUN_DATE date, SESSION_ID varchar, REPORT_CODE varchar, REPORT_NAME varchar, LEVEL varchar, SUBJECT varchar,
  SEG varchar, DIV varchar, CHECKED number, GAPS number, GAP_QTY float, GAP_STORES number, BUILT_AT timestamp_ntz);

create table if not exists V2RETAIL.SILVER.ALC_GAP_BUILD_LOG (
  SESSION_ID varchar, RUN_DATE date, STATUS varchar, ROWS_BUILT number, GAPS number, MESSAGE varchar,
  BUILT_AT timestamp_ntz default convert_timezone('Asia/Kolkata', current_timestamp())::timestamp_ntz);

create or replace procedure V2RETAIL.SILVER.SP_ALC_GAP_BUILD(P_SESSION varchar)
returns varchar
language sql
execute as caller
as
$$
declare
  v_run date;
  v_appr timestamp_ntz;
  v_newer number;
  v_rows number;
  v_gaps number;
  v_keep number;
begin
  select max(to_date(APPROVED_AT)), max(try_to_timestamp_ntz(APPROVED_AT)) into :v_run, :v_appr
    from V2RETAIL.BRONZE.ALC_ARS_GRID_MJ where SESSION_ID = :P_SESSION;
  if (v_run is null) then
    insert into V2RETAIL.SILVER.ALC_GAP_BUILD_LOG (SESSION_ID, STATUS, MESSAGE) values (:P_SESSION, 'FAILED', 'session not in BRONZE.ALC_ARS_GRID_MJ');
    return 'FAILED: session ' || P_SESSION || ' not loaded';
  end if;
  -- one session per run day: the latest approved one wins
  select count(*) into :v_newer from (select distinct SESSION_ID from V2RETAIL.SILVER.ALC_GAP_ROWS
    where RUN_DATE = :v_run and SESSION_ID > :P_SESSION);
  if (v_newer > 0) then
    insert into V2RETAIL.SILVER.ALC_GAP_BUILD_LOG (SESSION_ID, RUN_DATE, STATUS, MESSAGE)
      values (:P_SESSION, :v_run, 'SKIPPED', 'a later session of the same day is already built');
    return 'SKIPPED: a later session of ' || v_run || ' is built';
  end if;
  select NUM_VALUE into :v_keep from V2RETAIL.SILVER.ALC_GAP_CONFIG where KEY = 'KEEP_DAYS';

  -- config as one row
  create or replace temporary table V2RETAIL.SILVER.TMP_ALC_CFG as
    select * from (select KEY, NUM_VALUE from V2RETAIL.SILVER.ALC_GAP_CONFIG)
    pivot (max(NUM_VALUE) for KEY in ('OL_FR_HIGH','OL_FR_LOW','GND_DIFF','MJ_FR_HIGH','MJ_FR_LOW','EXC_BEFORE_FR',
      'EXCESS_BAND','BGT_ALC_DIFF_HIGH','BGT_ALC_DIFF_LOW','BGT_ALC_PCT','LOW_MSA_SHORT','LOW_MSA_REM','NOALC_SHORT',
      'NOALC_MSA','GRT_SHORT','PRIO_SHORT','PRIO_GRT','HOLD_CONTB','MSA_NODISP','HIGH_MSA_GM','HIGH_MSA_OTH',
      'LOWMSA_REM','PACK_MSA'))
    as p (OL_FR_HIGH, OL_FR_LOW, GND_DIFF, MJ_FR_HIGH, MJ_FR_LOW, EXC_BEFORE_FR, EXCESS_BAND, BGT_ALC_DIFF_HIGH,
          BGT_ALC_DIFF_LOW, BGT_ALC_PCT, LOW_MSA_SHORT, LOW_MSA_REM, NOALC_SHORT, NOALC_MSA, GRT_SHORT, PRIO_SHORT,
          PRIO_GRT, HOLD_CONTB, MSA_NODISP, HIGH_MSA_GM, HIGH_MSA_OTH, LOWMSA_REM, PACK_MSA);

  -- store x MJ base: the workbook's 02-ST-MJ (APP and GM only, as in the workbook)
  create or replace temporary table V2RETAIL.SILVER.TMP_ALC_SM as
  select g.WERKS ST_CD, coalesce(ms.ST_NM, g.STORE_NAME) ST_NM, g.ST_STATUS ST_STAT, ms.OP_DT ST_OP_DT, g.RDC,
         g.SEG, g.DIV, g.SUB_DIV, g.MAJ_CAT, coalesce(mj.SSN, g.SSN) SSN, mj.MJ_STS, sz.SZ_APP,
         coalesce(g.MBQ, 0) MBQ, coalesce(g.ALC_Q, 0) ALC_Q, coalesce(g.STK_TTL, 0) AFTER_STK,
         greatest(coalesce(g.STK_TTL, 0) - coalesce(g.ALC_Q, 0), 0) BEFORE_STK,
         greatest(coalesce(g.MBQ, 0) - coalesce(g.STK_TTL, 0), 0) SHORT_AFT,
         greatest(coalesce(g.STK_TTL, 0) - coalesce(g.MBQ, 0) * c.EXCESS_BAND, 0) EXCESS_AFT,
         least(coalesce(g.ALC_Q, 0), greatest(coalesce(g.STK_TTL, 0) - coalesce(g.MBQ, 0) * c.EXCESS_BAND, 0)) EXCESS_ALC,
         iff(g.MJ_PER_OPT_MBQ > 0, round(greatest(coalesce(g.MBQ, 0) - coalesce(g.STK_TTL, 0), 0) / g.MJ_PER_OPT_MBQ), 0) OPT_SHORT,
         coalesce(g.DISP_Q, 0) ALC_DISP, b.BGT_DISP, b.BGT_GND, a.ALC_GND, g.ACS_D ACC_DENS,
         coalesce(g.DH24_MSA_CL_Q, 0) + coalesce(g.DW01_MSA_CL_Q, 0) RDC_REM_MSA,  -- both RDCs: stores are allocated from either
         g.DH24_MSA_CL_Q, g.DW01_MSA_CL_Q, g."DH24_CL_OPT_CNT_50_PCS" DH24_OPT, g."DW01_CL_OPT_CNT_50_PCS" DW01_OPT,
         coalesce(g.HOLD_CLOSE_REM, 0) HOLD_REM
  from V2RETAIL.BRONZE.ALC_ARS_GRID_MJ g
  cross join V2RETAIL.SILVER.TMP_ALC_CFG c
  left join V2RETAIL.BRONZE.ALC_GAP_MST_STORE ms on ms.ST_CD = g.WERKS
  left join V2RETAIL.BRONZE.ALC_GAP_MST_MAJCAT mj on mj.MAJ_CAT = g.MAJ_CAT
  left join V2RETAIL.BRONZE.ALC_GAP_MST_SZ_APP sz on sz.MAJ_CAT = g.MAJ_CAT
  left join V2RETAIL.BRONZE.ALC_GAP_MST_BGT_DISP b on b.ST_CD = g.WERKS and b.MAJ_CAT = g.MAJ_CAT
  left join V2RETAIL.BRONZE.ALC_GAP_MST_ALC_GND a on a.ST_CD = g.WERKS and a.MAJ_CAT = g.MAJ_CAT
  where g.SESSION_ID = :P_SESSION and g.SEG in ('APP', 'GM');

  -- DC GRT stock per RDC x MJ as the run saw it: the latest snapshot before the run day (ties to the workbook)
  create or replace temporary table V2RETAIL.SILVER.TMP_ALC_GRT as
  with p as (
    select WERKS RDC, MAJ_CAT, sum(QTY_TOTAL) PROC_GRT,
           count(distinct iff(QTY_TOTAL > 0, left(ARTICLE, 10) || '|' || COLOR, null)) PROC_GRT_OPT
    from V2RETAIL.SILVER.V_DC_BIN_STOCK_CLASS_SNAP
    where STK_STATUS = 'GRT STK' and WERKS in ('DH24', 'DW01')
      and SNAP_DATE = (select max(SNAP_DATE) from V2RETAIL.SILVER.V_DC_BIN_STOCK_CLASS_SNAP where SNAP_DATE < :v_run)
    group by 1, 2),
  u as (
    select e.WERKS RDC, d.MAJ_CAT, sum(e.LABST) UNPROC_GRT,
           count(distinct iff(e.LABST > 0, d.GEN_ART_NUMBER_TXT || '|' || d.COLOR, null)) UNPROC_GRT_OPT
    from V2RETAIL.BRONZE.ET_STOCK_DATA e
    join V2RETAIL.GOLD.DIM_PRODUCT_V2 d on d.MATNR = ltrim(e.MATNR, '0')
    where e.WERKS in ('DH24', 'DW01') and e.LGORT = '0032'
      and e.STOCK_DATE = (select max(STOCK_DATE) from V2RETAIL.BRONZE.ET_STOCK_DATA
                          where WERKS = 'DH24' and STOCK_DATE < :v_run)
    group by 1, 2)
  select coalesce(p.RDC, u.RDC) RDC, coalesce(p.MAJ_CAT, u.MAJ_CAT) MAJ_CAT, coalesce(p.PROC_GRT, 0) PROC_GRT,
         coalesce(p.PROC_GRT_OPT, 0) PROC_GRT_OPT, coalesce(u.UNPROC_GRT, 0) UNPROC_GRT,
         coalesce(u.UNPROC_GRT_OPT, 0) UNPROC_GRT_OPT
  from p full join u on u.RDC = p.RDC and u.MAJ_CAT = p.MAJ_CAT;

  -- company x MJ: the workbook's 03-CO-MJ
  create or replace temporary table V2RETAIL.SILVER.TMP_ALC_CM as
  with m as (
    select SEG, DIV, SUB_DIV, MAJ_CAT, max(SSN) SSN, max(MJ_STS) MJ_STS, max(SZ_APP) SZ_APP,
           sum(MBQ) MBQ, sum(ALC_Q) ALC_Q, sum(BEFORE_STK) BEFORE_STK, sum(AFTER_STK) AFTER_STK, sum(SHORT_AFT) SHORT_AFT,
           sum(EXCESS_AFT) EXCESS_AFT, sum(EXCESS_ALC) EXCESS_ALC, max(OPT_SHORT) OPT_SHORT,
           sum(ALC_DISP) ALC_DISP, sum(BGT_DISP) BGT_DISP,
           coalesce(max(DH24_MSA_CL_Q), 0) + coalesce(max(DW01_MSA_CL_Q), 0) REM_MSA,
           coalesce(max(DH24_OPT), 0) + coalesce(max(DW01_OPT), 0) REM_MSA_OPT,
           sum(HOLD_REM) HOLD_MSA,
           mode(iff(MBQ > 0, ACC_DENS, null)) ACC_DENS
    from V2RETAIL.SILVER.TMP_ALC_SM group by 1, 2, 3, 4),
  pk as (
    select MAJ_CAT, PAK_SZ from (
      select MAJ_CAT, PAK_SZ, sum(REM_MSA_Q) q
      from V2RETAIL.BRONZE.ALC_ARS_MSA_OP_CL
      where SESSION_ID = :P_SESSION and LEVEL = 'DETAIL' and PAK_SZ is not null
      group by 1, 2)
    qualify row_number() over (partition by MAJ_CAT order by q desc, PAK_SZ desc) = 1),
  gr as (select MAJ_CAT, sum(PROC_GRT) PROC_GRT, sum(PROC_GRT_OPT) PROC_GRT_OPT, sum(UNPROC_GRT) UNPROC_GRT,
                sum(UNPROC_GRT_OPT) UNPROC_GRT_OPT from V2RETAIL.SILVER.TMP_ALC_GRT group by 1)
  select m.*, pk.PAK_SZ, coalesce(gr.PROC_GRT, 0) PROC_GRT, coalesce(gr.PROC_GRT_OPT, 0) PROC_GRT_OPT,
         coalesce(gr.UNPROC_GRT, 0) UNPROC_GRT, coalesce(gr.UNPROC_GRT_OPT, 0) UNPROC_GRT_OPT
  from m left join pk on pk.MAJ_CAT = m.MAJ_CAT left join gr on gr.MAJ_CAT = m.MAJ_CAT;

  -- store one-liner: the workbook's 01-ST-ONE-LINER
  create or replace temporary table V2RETAIL.SILVER.TMP_ALC_ST as
  select s.ST_CD, max(s.ST_NM) ST_NM, max(s.ST_STAT) ST_STAT, max(s.ST_OP_DT) ST_OP_DT, max(s.RDC) RDC,
         sum(s.MBQ) MBQ, sum(s.ALC_Q) ALC_Q, sum(s.BEFORE_STK) BEFORE_STK, sum(s.AFTER_STK) AFTER_STK,
         sum(s.SHORT_AFT) SHORT_AFT, sum(s.EXCESS_AFT) EXCESS_AFT, sum(s.EXCESS_ALC) EXCESS_ALC,
         sum(s.BGT_DISP) BGT_DISP, sum(s.ALC_DISP) ALC_DISP, sum(s.ALC_GND) ALC_GND, sum(s.BGT_GND) BGT_GND,
         max(f.INSTALLED_GND) INSTALLED_GND,
         max(case dayname(:v_run) when 'Mon' then sc.MON when 'Tue' then sc.TUE when 'Wed' then sc.WED
                                  when 'Thu' then sc.THU when 'Fri' then sc.FRI when 'Sat' then sc.SAT else 0 end) SCHLD
  from V2RETAIL.SILVER.TMP_ALC_SM s
  left join V2RETAIL.BRONZE.ALC_GAP_MST_ST_FIXTURE f on f.ST_CD = s.ST_CD
  left join V2RETAIL.BRONZE.ALC_GAP_MST_SCHEDULE sc on sc.ST_CD = s.ST_CD and sc.RUNNING_STATUS = 'RUNNING'
  group by s.ST_CD;

  -- generic-colour at each RDC (MSA_OP_CL GEN_CLR level), with its MJ-RDC short from the grid
  create or replace temporary table V2RETAIL.SILVER.TMP_ALC_GC as
  with mr as (
    select RDC, MAJ_CAT, sum(MBQ) MBQ, sum(ALC_Q) ALC_Q, sum(SHORT_AFT) SHORT_AFT, max(OPT_SHORT) OPT_SHORT
    from V2RETAIL.SILVER.TMP_ALC_SM group by 1, 2)
  select o.RDC, o.SEG, o.DIV, o.SUB_DIV, o.MAJ_CAT, mj.SSN, o.GEN_ART_NUMBER GEN_ART, o.CLR,
         coalesce(o.REM_MSA_Q, 0) REM_MSA, coalesce(o.ALC_Q, 0) GC_ALC_Q, coalesce(o.HOLD_AFT_ALC, 0) HOLD_MSA,
         mr.MBQ MR_MBQ, mr.ALC_Q MR_ALC_Q, mr.SHORT_AFT MR_SHORT, mr.OPT_SHORT MR_OPT_SHORT
  from V2RETAIL.BRONZE.ALC_ARS_MSA_OP_CL o
  left join V2RETAIL.BRONZE.ALC_GAP_MST_MAJCAT mj on mj.MAJ_CAT = o.MAJ_CAT
  left join mr on mr.RDC = o.RDC and mr.MAJ_CAT = o.MAJ_CAT
  where o.SESSION_ID = :P_SESSION and o.LEVEL = 'GEN_CLR' and o.SEG in ('APP', 'GM');

  -- one row per report x entity
  create or replace temporary table V2RETAIL.SILVER.TMP_ALC_OUT like V2RETAIL.SILVER.ALC_GAP_ROWS;

  -- ST01 one-liner fill rate
  insert into V2RETAIL.SILVER.TMP_ALC_OUT (REPORT_CODE, GAP_FLAG, IS_GAP, GAP_QTY, ST_CD, ST_NM, ST_STAT, ST_OP_DT, RDC, MBQ, BEFORE_STK,
                       ALC_Q, AFTER_STK, SHORT_AFT, EXCESS_AFT, EXCESS_ALC)
  select 'ST01', f, iff(f in ('HIGH-FR%', 'LOW-FR%'), 1, 0), case f when 'HIGH-FR%' then EXCESS_AFT when 'LOW-FR%' then SHORT_AFT else 0 end,
         ST_CD, ST_NM, ST_STAT, ST_OP_DT, RDC, MBQ, BEFORE_STK, ALC_Q, AFTER_STK, SHORT_AFT, EXCESS_AFT, EXCESS_ALC
  from (select s.*, case when s.MBQ <= 0 then 'NO MBQ' when s.AFTER_STK / s.MBQ > c.OL_FR_HIGH then 'HIGH-FR%'
                         when s.AFTER_STK / s.MBQ < c.OL_FR_LOW then 'LOW-FR%' else 'OK' end f
        from V2RETAIL.SILVER.TMP_ALC_ST s cross join V2RETAIL.SILVER.TMP_ALC_CFG c);

  -- ST02 installed vs allocation gandola
  insert into V2RETAIL.SILVER.TMP_ALC_OUT (REPORT_CODE, GAP_FLAG, IS_GAP, GAP_QTY, ST_CD, ST_NM, ST_STAT, ST_OP_DT, RDC, INSTALLED_GND, ALC_GND,
                       BGT_GND, DIFF_Q)
  select 'ST02', f, iff(f = 'OK', 0, 1), iff(f = 'GAP', abs(d), 0), ST_CD, ST_NM, ST_STAT, ST_OP_DT, RDC, INSTALLED_GND,
         ALC_GND, BGT_GND, d
  from (select s.*, coalesce(s.INSTALLED_GND, 0) - coalesce(s.ALC_GND, 0) d,
               case when coalesce(s.INSTALLED_GND, 0) = 0 or coalesce(s.ALC_GND, 0) = 0 then 'INSTALLED/ALC GND NOT GIVEN'
                    when abs(coalesce(s.INSTALLED_GND, 0) - coalesce(s.ALC_GND, 0)) > c.GND_DIFF then 'GAP' else 'OK' end f
        from V2RETAIL.SILVER.TMP_ALC_ST s cross join V2RETAIL.SILVER.TMP_ALC_CFG c);

  -- ST03 zero allocation in a scheduled store
  insert into V2RETAIL.SILVER.TMP_ALC_OUT (REPORT_CODE, GAP_FLAG, IS_GAP, GAP_QTY, ST_CD, ST_NM, ST_STAT, ST_OP_DT, RDC, SCHLD, MBQ, BEFORE_STK,
                       ALC_Q, AFTER_STK, SHORT_AFT, EXCESS_AFT)
  select 'ST03', f, iff(f = 'GAP', 1, 0), iff(f = 'GAP', SHORT_AFT, 0), ST_CD, ST_NM, ST_STAT, ST_OP_DT, RDC,
         coalesce(SCHLD, 0), MBQ, BEFORE_STK, ALC_Q, AFTER_STK, SHORT_AFT, EXCESS_AFT
  from (select s.*, case when coalesce(s.SCHLD, 0) = 0 then 'NOT SCHEDULED' when s.ALC_Q > 0 then 'OK' else 'GAP' end f
        from V2RETAIL.SILVER.TMP_ALC_ST s);

  -- ST04 display given but no allocation (store)
  insert into V2RETAIL.SILVER.TMP_ALC_OUT (REPORT_CODE, GAP_FLAG, IS_GAP, GAP_QTY, ST_CD, ST_NM, ST_STAT, ST_OP_DT, RDC, SCHLD, BGT_DISP, ALC_DISP,
                       ALC_Q, MBQ, SHORT_AFT)
  select 'ST04', f, iff(f = 'GAP', 1, 0), iff(f = 'GAP', BGT_DISP, 0), ST_CD, ST_NM, ST_STAT, ST_OP_DT, RDC,
         coalesce(SCHLD, 0), BGT_DISP, ALC_DISP, ALC_Q, MBQ, SHORT_AFT
  from (select s.*, iff(coalesce(s.BGT_DISP, 0) > 0 and s.ALC_Q = 0, 'GAP', 'OK') f from V2RETAIL.SILVER.TMP_ALC_ST s);

  -- ST-MJ reports share the identity columns
  insert into V2RETAIL.SILVER.TMP_ALC_OUT (REPORT_CODE, GAP_FLAG, IS_GAP, GAP_QTY, ST_CD, ST_NM, ST_STAT, ST_OP_DT, RDC, SEG, DIV, SUB_DIV,
                       MAJ_CAT, SSN, MJ_STS, SZ_APP, BGT_DISP, ALC_DISP, ALC_Q, ACC_DENS, DIFF_Q, MBQ, BEFORE_STK, AFTER_STK,
                       SHORT_AFT, EXCESS_AFT, EXCESS_ALC, OPT_SHORT, REM_MSA, BGT_GND, ALC_GND)
  select r0.REPORT_CODE, decode(r0.REPORT_CODE, 'SM01', s.F1, 'SM02', s.F2, 'SM03', s.F3, 'SM04', s.F4, s.F5) f,
         iff(f in ('OK', 'NO MBQ', 'NA'), 0, 1),
         decode(r0.REPORT_CODE, 'SM01', s.Q1, 'SM02', s.Q2, 'SM03', s.Q3, 'SM04', s.Q4, s.Q5),
         s.ST_CD, s.ST_NM, s.ST_STAT, s.ST_OP_DT, s.RDC, s.SEG, s.DIV, s.SUB_DIV, s.MAJ_CAT, s.SSN, s.MJ_STS, s.SZ_APP,
         s.BGT_DISP, s.ALC_DISP, s.ALC_Q, s.ACC_DENS, iff(r0.REPORT_CODE = 'SM02', s.D2, null), s.MBQ, s.BEFORE_STK, s.AFTER_STK, s.SHORT_AFT, s.EXCESS_AFT,
         s.EXCESS_ALC, s.OPT_SHORT, s.RDC_REM_MSA, s.BGT_GND, s.ALC_GND
  from (
    select s.*,
           iff(coalesce(s.BGT_DISP, 0) = 0 and s.ALC_Q > 0, 'GAP', 'OK') F1,
           iff(coalesce(s.BGT_DISP, 0) = 0 and s.ALC_Q > 0, s.ALC_Q, 0) Q1,
           iff(coalesce(s.BGT_DISP, 0) > 0 and s.BGT_DISP - coalesce(s.ACC_DENS, 0) < 0, 'GAP', 'OK') F2,
           iff(coalesce(s.BGT_DISP, 0) > 0, greatest(coalesce(s.ACC_DENS, 0) - s.BGT_DISP, 0), 0) Q2,
           iff(coalesce(s.BGT_DISP, 0) > 0, s.BGT_DISP - coalesce(s.ACC_DENS, 0), 0) D2,
           iff(s.MBQ > 0 and s.BEFORE_STK / nullif(s.MBQ, 0) > c.EXC_BEFORE_FR and s.ALC_Q > 0, 'EXCESS ALC', 'OK') F3,
           iff(s.MBQ > 0 and s.BEFORE_STK / nullif(s.MBQ, 0) > c.EXC_BEFORE_FR and s.ALC_Q > 0, s.ALC_Q, 0) Q3,
           iff(s.SHORT_AFT > 0 and coalesce(s.RDC_REM_MSA, 0) > 0, 'GAP', 'OK') F4,
           iff(s.SHORT_AFT > 0 and coalesce(s.RDC_REM_MSA, 0) > 0, s.SHORT_AFT, 0) Q4,
           case when s.MBQ <= 0 then iff(s.AFTER_STK > 0, 'NO MBQ', 'NA')
                when s.AFTER_STK / s.MBQ > c.MJ_FR_HIGH then 'HIGH-FR%'
                when s.AFTER_STK / s.MBQ < c.MJ_FR_LOW then 'LOW-FR%' else 'OK' end F5,
           case when s.MBQ <= 0 then 0 when s.AFTER_STK / s.MBQ > c.MJ_FR_HIGH then s.EXCESS_AFT
                when s.AFTER_STK / s.MBQ < c.MJ_FR_LOW then s.SHORT_AFT else 0 end Q5
    from V2RETAIL.SILVER.TMP_ALC_SM s cross join V2RETAIL.SILVER.TMP_ALC_CFG c) s
  cross join (select column1 REPORT_CODE from values ('SM01'), ('SM02'), ('SM03'), ('SM04'), ('SM05')) r0;

  -- CO-MJ reports
  insert into V2RETAIL.SILVER.TMP_ALC_OUT (REPORT_CODE, GAP_FLAG, IS_GAP, GAP_QTY, SEG, DIV, SUB_DIV, MAJ_CAT, SSN, MJ_STS, SZ_APP, MBQ, ALC_Q,
                       BEFORE_STK, AFTER_STK, SHORT_AFT, EXCESS_AFT, EXCESS_ALC, OPT_SHORT, REM_MSA, REM_MSA_OPT, HOLD_MSA,
                       TTL_MSA, PROC_GRT, PROC_GRT_OPT, UNPROC_GRT, UNPROC_GRT_OPT, BGT_DISP, ALC_DISP, DIFF_Q, ACC_DENS, PAK_SZ)
  select r0.REPORT_CODE, decode(r0.REPORT_CODE, 'CM01', m.F01, 'CM02', m.F02, 'CM03', m.F03, 'CM04', m.F04, 'CM05', m.F05, 'CM06', m.F06, 'CM07', m.F07, 'CM08', m.F08, 'CM09', m.F09, 'CM10', m.F10, 'CM11', m.F11) f, iff(f in ('OK', 'NA'), 0, 1),
         decode(r0.REPORT_CODE, 'CM01', m.Q01, 'CM02', m.Q02, 'CM03', m.Q03, 'CM04', m.Q04, 'CM05', m.Q05, 'CM06', m.Q06, 'CM07', m.Q07, 'CM08', m.Q08, 'CM09', m.Q09, 'CM10', m.Q10, 'CM11', m.Q11), m.SEG, m.DIV, m.SUB_DIV, m.MAJ_CAT, m.SSN, m.MJ_STS, m.SZ_APP,
         m.MBQ, m.ALC_Q, m.BEFORE_STK, m.AFTER_STK, m.SHORT_AFT, m.EXCESS_AFT, m.EXCESS_ALC, m.OPT_SHORT, m.REM_MSA,
         m.REM_MSA_OPT, m.HOLD_MSA, m.REM_MSA + m.HOLD_MSA, m.PROC_GRT, m.PROC_GRT_OPT, m.UNPROC_GRT, m.UNPROC_GRT_OPT,
         coalesce(m.BGT_DISP, 0), m.ALC_DISP, iff(r0.REPORT_CODE = 'CM10', m.ALC_DISP - coalesce(m.BGT_DISP, 0), null),
         m.ACC_DENS, m.PAK_SZ
  from (
    select m.*,
           iff(m.SHORT_AFT > 0 and m.REM_MSA > 0 and m.ALC_Q = 0, 'GAP', 'OK') F01,
           iff(m.SHORT_AFT > 0 and m.REM_MSA > 0 and m.ALC_Q = 0, m.SHORT_AFT, 0) Q01,
           iff(m.SHORT_AFT > c.LOW_MSA_SHORT and m.REM_MSA < c.LOW_MSA_REM, 'ZERO OR LOW MSA', 'OK') F02,
           iff(m.SHORT_AFT > c.LOW_MSA_SHORT and m.REM_MSA < c.LOW_MSA_REM, m.SHORT_AFT, 0) Q02,
           iff(m.SHORT_AFT > c.NOALC_SHORT and m.REM_MSA > c.NOALC_MSA and m.ALC_Q = 0, 'GAP', 'OK') F03,
           iff(m.SHORT_AFT > c.NOALC_SHORT and m.REM_MSA > c.NOALC_MSA and m.ALC_Q = 0, least(m.SHORT_AFT, m.REM_MSA), 0) Q03,
           iff(m.SHORT_AFT > c.GRT_SHORT and m.REM_MSA = 0 and m.PROC_GRT > 0, 'GAP', 'OK') F04,
           iff(m.SHORT_AFT > c.GRT_SHORT and m.REM_MSA = 0 and m.PROC_GRT > 0, least(m.SHORT_AFT, m.PROC_GRT), 0) Q04,
           iff(m.SHORT_AFT > c.PRIO_SHORT and m.PROC_GRT > c.PRIO_GRT, 'PRIORITISE IN GRT', 'OK') F05,
           iff(m.SHORT_AFT > c.PRIO_SHORT and m.PROC_GRT > c.PRIO_GRT, least(m.SHORT_AFT, m.PROC_GRT), 0) Q05,
           iff(m.SHORT_AFT > c.PRIO_SHORT and m.UNPROC_GRT > c.PRIO_GRT, 'PRIORITISE IN GRT', 'OK') F06,
           iff(m.SHORT_AFT > c.PRIO_SHORT and m.UNPROC_GRT > c.PRIO_GRT, least(m.SHORT_AFT, m.UNPROC_GRT), 0) Q06,
           iff(m.SHORT_AFT > 0 and m.REM_MSA + m.HOLD_MSA > 0 and m.HOLD_MSA / (m.REM_MSA + m.HOLD_MSA) > c.HOLD_CONTB, 'GAP', 'OK') F07,
           iff(m.SHORT_AFT > 0 and m.REM_MSA + m.HOLD_MSA > 0 and m.HOLD_MSA / (m.REM_MSA + m.HOLD_MSA) > c.HOLD_CONTB, least(m.SHORT_AFT, m.HOLD_MSA), 0) Q07,
           case when m.SEG = 'GM' and m.HOLD_MSA > 0 then 'GM-HOLD-GAP'
                        when m.REM_MSA = 0 and m.HOLD_MSA > 0 and m.SHORT_AFT > 0 then 'GAP' else 'OK' end F08,
           case when m.SEG = 'GM' and m.HOLD_MSA > 0 then m.HOLD_MSA
                when m.REM_MSA = 0 and m.HOLD_MSA > 0 and m.SHORT_AFT > 0 then least(m.SHORT_AFT, m.HOLD_MSA) else 0 end Q08,
           iff(coalesce(m.BGT_DISP, 0) = 0 and m.REM_MSA > c.MSA_NODISP, 'MSA AVAILABLE BUT DISP NOT GIVEN', 'OK') F09,
           iff(coalesce(m.BGT_DISP, 0) = 0 and m.REM_MSA > c.MSA_NODISP, m.REM_MSA, 0) Q09,
           iff(m.ALC_DISP - coalesce(m.BGT_DISP, 0) > c.BGT_ALC_DIFF_HIGH or m.ALC_DISP - coalesce(m.BGT_DISP, 0) < c.BGT_ALC_DIFF_LOW
                       or (coalesce(m.BGT_DISP, 0) > 0 and abs(m.ALC_DISP / m.BGT_DISP - 1) > c.BGT_ALC_PCT), 'GAP', 'OK') F10,
           iff(m.ALC_DISP - coalesce(m.BGT_DISP, 0) > c.BGT_ALC_DIFF_HIGH or m.ALC_DISP - coalesce(m.BGT_DISP, 0) < c.BGT_ALC_DIFF_LOW
               or (coalesce(m.BGT_DISP, 0) > 0 and abs(m.ALC_DISP / m.BGT_DISP - 1) > c.BGT_ALC_PCT), abs(m.ALC_DISP - coalesce(m.BGT_DISP, 0)), 0) Q10,
           iff(m.SEG = 'GM' and coalesce(m.SSN, '') <> 'SSNL' and m.MJ_STS = 'ACT' and m.REM_MSA > c.PACK_MSA
                       and m.ACC_DENS / 2 < m.PAK_SZ, 'GAP', iff(m.SEG = 'GM', 'OK', 'NA')) F11,
           iff(m.SEG = 'GM' and coalesce(m.SSN, '') <> 'SSNL' and m.MJ_STS = 'ACT' and m.REM_MSA > c.PACK_MSA
               and m.ACC_DENS / 2 < m.PAK_SZ, m.REM_MSA, 0) Q11
    from V2RETAIL.SILVER.TMP_ALC_CM m cross join V2RETAIL.SILVER.TMP_ALC_CFG c) m
  cross join (select column1 REPORT_CODE from values ('CM01'), ('CM02'), ('CM03'), ('CM04'), ('CM05'), ('CM06'), ('CM07'), ('CM08'), ('CM09'), ('CM10'), ('CM11')) r0;

  -- GEN-CLR reports
  insert into V2RETAIL.SILVER.TMP_ALC_OUT (REPORT_CODE, GAP_FLAG, IS_GAP, GAP_QTY, RDC, SEG, DIV, SUB_DIV, MAJ_CAT, SSN, GEN_ART, CLR, REM_MSA,
                       REM_MSA_OPT, HOLD_MSA, ALC_Q, MBQ, SHORT_AFT, OPT_SHORT)
  select 'GC01', f, iff(f = 'HIGH-MSA', 1, 0), iff(f = 'HIGH-MSA', REM_MSA, 0), RDC, SEG, DIV, SUB_DIV, MAJ_CAT, SSN,
         GEN_ART, CLR, REM_MSA, iff(REM_MSA > 0, 1, 0), HOLD_MSA, GC_ALC_Q, null, null, null
  from (select g.*, iff(g.REM_MSA > iff(g.SEG = 'GM', c.HIGH_MSA_GM, c.HIGH_MSA_OTH), 'HIGH-MSA', 'OK') f
        from V2RETAIL.SILVER.TMP_ALC_GC g cross join V2RETAIL.SILVER.TMP_ALC_CFG c);
  insert into V2RETAIL.SILVER.TMP_ALC_OUT (REPORT_CODE, GAP_FLAG, IS_GAP, GAP_QTY, RDC, SEG, DIV, SUB_DIV, MAJ_CAT, SSN, GEN_ART, CLR, REM_MSA,
                       REM_MSA_OPT, HOLD_MSA, ALC_Q, MBQ, SHORT_AFT, OPT_SHORT)
  select 'GC02', f, iff(f = 'MANUAL PUSH', 1, 0), iff(f = 'MANUAL PUSH', REM_MSA, 0), RDC, SEG, DIV, SUB_DIV, MAJ_CAT, SSN,
         GEN_ART, CLR, REM_MSA, iff(REM_MSA > 0, 1, 0), HOLD_MSA, GC_ALC_Q, MR_MBQ, MR_SHORT, MR_OPT_SHORT
  from (select g.*, iff(coalesce(g.MR_SHORT, 0) > 0 and g.GC_ALC_Q = 0 and g.REM_MSA > 0 and g.REM_MSA < c.LOWMSA_REM,
                        'MANUAL PUSH', 'OK') f
        from V2RETAIL.SILVER.TMP_ALC_GC g cross join V2RETAIL.SILVER.TMP_ALC_CFG c);

  -- publish: replace this run day atomically, drop old days
  begin transaction;
  delete from V2RETAIL.SILVER.ALC_GAP_ROWS where RUN_DATE = :v_run
     or RUN_DATE < dateadd(day, -:v_keep, :v_run);
  insert into V2RETAIL.SILVER.ALC_GAP_ROWS
  select :v_run, :P_SESSION, :v_appr, o.REPORT_CODE, d.REPORT_NAME, d.LEVEL, d.SUBJECT, o.GAP_FLAG, o.IS_GAP, o.GAP_QTY,
         o.ST_CD, o.ST_NM, o.ST_STAT, o.ST_OP_DT, o.RDC, o.SEG, o.DIV, o.SUB_DIV, o.MAJ_CAT, o.SSN, o.MJ_STS, o.SZ_APP,
         o.GEN_ART, o.CLR, o.SCHLD, o.BGT_DISP, o.ALC_DISP, o.DIFF_Q, o.INSTALLED_GND, o.ALC_GND, o.BGT_GND, o.ACC_DENS,
         o.PAK_SZ, o.MBQ, o.BEFORE_STK, o.ALC_Q, o.AFTER_STK, o.SHORT_AFT, o.EXCESS_AFT, o.EXCESS_ALC, o.OPT_SHORT,
         o.REM_MSA, o.REM_MSA_OPT, o.HOLD_MSA, o.TTL_MSA, o.PROC_GRT, o.PROC_GRT_OPT, o.UNPROC_GRT, o.UNPROC_GRT_OPT,
         convert_timezone('Asia/Kolkata', current_timestamp())::timestamp_ntz
  from V2RETAIL.SILVER.TMP_ALC_OUT o join V2RETAIL.SILVER.ALC_GAP_REPORT_DEF d on d.REPORT_CODE = o.REPORT_CODE;
  v_rows := SQLROWCOUNT;
  commit;
  select count_if(IS_GAP = 1) into :v_gaps from V2RETAIL.SILVER.ALC_GAP_ROWS where RUN_DATE = :v_run;
  -- day x report summary for the trend dashboard (kept as long as the history allows)
  delete from V2RETAIL.SILVER.ALC_GAP_DAILY where RUN_DATE = :v_run;
  insert into V2RETAIL.SILVER.ALC_GAP_DAILY
  select RUN_DATE, SESSION_ID, REPORT_CODE, REPORT_NAME, LEVEL, SUBJECT, coalesce(SEG, 'ALL'), coalesce(DIV, 'ALL'),
         count(*), count_if(IS_GAP = 1), sum(GAP_QTY), count(distinct iff(IS_GAP = 1, ST_CD, null)),
         convert_timezone('Asia/Kolkata', current_timestamp())::timestamp_ntz
  from V2RETAIL.SILVER.ALC_GAP_ROWS where RUN_DATE = :v_run group by 1, 2, 3, 4, 5, 6, 7, 8;
  insert into V2RETAIL.SILVER.ALC_GAP_BUILD_LOG (SESSION_ID, RUN_DATE, STATUS, ROWS_BUILT, GAPS)
    values (:P_SESSION, :v_run, iff(:v_rows > 0, 'OK', 'FAILED'), :v_rows, :v_gaps);
  return 'OK ' || v_run || ': ' || v_rows || ' rows, ' || v_gaps || ' gaps';
end;
$$;
