-- Karma productivity (V2Pulse Analytics): Snowflake layer. Run as ACCOUNTADMIN on COMPUTE_WH; safe to re-run.
-- Sources (Karma app, refreshed ~04:00 IST): KARMA.EMP_REPORT (one row per employee, today's snapshot only),
-- KARMA.YES_NO_REPORT (checklist lines since 2025-12-04), KARMA.TASKS_REPORT (every task since 2025-11-11).
--   SILVER.KARMA_EMP_SNAP  EMP_REPORT kept day by day (history starts 2026-10-15), with status flags
--   SILVER.KARMA_EMP_DAY   employee x day: checklist hours / lines / maker-checker qty, tasks raised / completed
--   SILVER.SP_KARMA_BUILD  rebuilds both; task SILVER.TASK_KARMA_DAILY at 09:45 IST

create table if not exists V2RETAIL.SILVER.KARMA_EMP_SNAP (
  REPORT_DATE date not null, E_CODE varchar not null, EMP_NAME varchar, DEPT varchar, SUB_DEPT_1 varchar,
  SUB_DEPT_2 varchar, SUB_DEPT_3 varchar, LOC varchar, LOC_GROUP varchar, DESIGNATION varchar,
  MANAGER varchar, MANAGER_ECODE varchar, PROJECT_COUNT number(6,0), PROJECT_NAMES varchar,
  ATTENDANCE_TD varchar, ATTENDANCE_YD varchar, STATUS_YD varchar, STATUS_TD varchar, HOURS_BAND varchar,
  TASK_BAND varchar, PENDING_BAND varchar,
  HOURS_TD float, HOURS_YD float, AVG_HOURS_7D float, AVG_HOURS_30D float,
  EFF_DAYS_7D float, FILL_DAYS_7D float, EFF_DAYS_30D float,
  MC_BGT_1D float, MC_ACT_1D float, MC_BGT_7D float, MC_ACT_7D float, MC_BGT_30D float, MC_ACT_30D float,
  PENDING_OVERAGED number(10,0), PENDING_OPEN number(10,0),
  RAISED_1D number(10,0), RAISED_7D number(10,0), RAISED_30D number(10,0),
  COMPLETED_1D number(10,0), COMPLETED_7D number(10,0), COMPLETED_30D number(10,0),
  DELEGATED_1D number(10,0), DELEGATED_7D number(10,0), DELEGATED_30D number(10,0),
  EMP_F number(1,0), FILLED_F number(1,0), NOT_FILLED_F number(1,0), LEAVE_F number(1,0), ACTIVE_F number(1,0),
  UPTO5_F number(1,0), OVER5_F number(1,0), NO_TASK_F number(1,0), RAISED_YD number(10,0), HOURS_YD_FILLED float,
  BUILT_AT timestamp_ntz,
  primary key (REPORT_DATE, E_CODE)
) comment = 'Karma: EMP_REPORT kept per report date, with fill / leave / hours / task flags. Built by SILVER.SP_KARMA_BUILD.';

create or replace procedure V2RETAIL.SILVER.SP_KARMA_BUILD()
returns varchar
language sql
comment = 'Karma: appends today''s EMP_REPORT to KARMA_EMP_SNAP and rebuilds KARMA_EMP_DAY.'
execute as owner
as
$$
declare
  rd date;
  n_snap integer;
  n_day integer;
begin
  select max(REPORT_DATE) into :rd from V2RETAIL.KARMA.EMP_REPORT;

  begin transaction;
  delete from V2RETAIL.SILVER.KARMA_EMP_SNAP where REPORT_DATE = :rd;
  insert into V2RETAIL.SILVER.KARMA_EMP_SNAP
  with e as (
    select r.*,
           -- The day being reported is yesterday (T-1D): 'L' = on leave, hours above zero = filled.
           case when r.YES_NO_L1D = 'L' then 'On leave' when coalesce(r.HOURS_YESTERDAY_NUM, 0) > 0 then 'Filled'
                else 'Not filled' end ST_YD,
           case when r.YES_NO_TD = 'L' then 'On leave' when coalesce(r.HOURS_TODAY_NUM, 0) > 0 then 'Filled'
                else 'Not filled' end ST_TD
    from V2RETAIL.KARMA.EMP_REPORT r where r.REPORT_DATE = :rd and nullif(trim(r.E_CODE), '') is not null)
  select REPORT_DATE, upper(trim(E_CODE)), EMP_NAME, nullif(nullif(trim(DPT), ''), '-'), nullif(SUB_1, '-'), nullif(SUB_2, '-'),
         nullif(SUB_3, '-'), nullif(LOC, '-'),
         case when LOC = 'RH01' then 'HO' when LOC = 'RH02' then 'HO-2 (RH02)'
              when LOC ilike '%RDC%' or LOC ilike '%HUB%' or LOC ilike 'D%0%' then 'RDC / hub'
              when LOC is null or LOC = '-' then 'Unknown' else 'Store' end,
         DESIGNATION, nullif(REPORT_M, '-'), nullif(REPORT_MNGR_E_CODE, '-'), PROJECT_COUNT, PROJECT_NAMES,
         ATTENDANCE_STATUS_TODAY, ATTENDANCE_STATUS_YESTERDAY, ST_YD, ST_TD,
         case when ST_YD = 'On leave' then '0 · On leave' when ST_YD = 'Not filled' then '1 · Not filled'
              when HOURS_YESTERDAY_NUM <= 5 then '2 · Up to 5 h' when HOURS_YESTERDAY_NUM < 7 then '3 · 5-7 h'
              else '4 · 7 h and more' end,
         case when ST_YD = 'On leave' then '0 · On leave' when coalesce(TASK_RAISED_L1D, 0) = 0 then '1 · No task raised'
              when TASK_RAISED_L1D <= 5 then '2 · 1-5 tasks' else '3 · More than 5 tasks' end,
         case when coalesce(PENDING_OA, 0) = 0 then '0 · None overaged' when PENDING_OA <= 5 then '1 · 1-5 overaged'
              when PENDING_OA <= 20 then '2 · 6-20 overaged' else '3 · More than 20 overaged' end,
         HOURS_TODAY_NUM, HOURS_YESTERDAY_NUM, TRUE_AVG_7D, TRUE_AVG_30D, EFF_DAYS_L7D, L7D_FILL_COUNT, EFFECTIVE_DAYS_30D,
         B_1D, A_1D, B_7D, A_7D, B_30D, A_30D, PENDING_OA, PENDING_P,
         TASK_RAISED_L1D, TASK_RAISED_L7D, TASK_RAISED_L30D, TASK_COMPLETED_L1D, TASK_COMPLETED_L7D, TASK_COMPLETED_L30D,
         TASK_DELEGATED_L1D, TASK_DELEGATED_L7D, TASK_DELEGATED_L30D,
         1, iff(ST_YD = 'Filled', 1, 0), iff(ST_YD = 'Not filled', 1, 0), iff(ST_YD = 'On leave', 1, 0),
         iff(ST_YD <> 'On leave', 1, 0),
         iff(ST_YD = 'Filled' and HOURS_YESTERDAY_NUM <= 5, 1, 0), iff(ST_YD = 'Filled' and HOURS_YESTERDAY_NUM > 5, 1, 0),
         iff(ST_YD <> 'On leave' and coalesce(TASK_RAISED_L1D, 0) = 0, 1, 0),
         iff(ST_YD <> 'On leave', coalesce(TASK_RAISED_L1D, 0), 0),
         iff(ST_YD = 'Filled', HOURS_YESTERDAY_NUM, null),
         current_timestamp()::timestamp_ntz
  from e;
  n_snap := sqlrowcount;
  commit;

  create or replace table V2RETAIL.SILVER.KARMA_EMP_DAY
    comment = 'Karma: employee x day - checklist hours, lines, maker-checker qty, tasks raised / received / completed. Built by SILVER.SP_KARMA_BUILD.' as
  with yn as (
    select upper(trim(EMP_ID)) E_CODE, ACT_DATE DT, count(*) LINES, sum(coalesce(TTL_HRS, 0)) HOURS,
           sum(coalesce(try_to_double(BGT_QTY), 0)) MC_BGT, sum(coalesce(try_to_double(ACT_QTY), 0)) MC_ACT,
           sum(coalesce(LINKED_TASKS, 0)) LINKED_TASKS, sum(coalesce(TD_CLOSED_TASKS, 0)) CLOSED_TASKS,
           count(distinct KRA) KRAS
    from V2RETAIL.KARMA.YES_NO_REPORT where EMP_ID is not null and ACT_DATE <= :rd group by 1, 2),
  raised as (
    select upper(trim("ASSIGNED BY ECODE")) E_CODE, try_to_date("DATE", 'DD-MM-YYYY') DT, count(*) RAISED
    from V2RETAIL.KARMA.TASKS_REPORT where "ASSIGNED BY ECODE" is not null group by 1, 2),
  recv as (
    select upper(trim("ASSIGNED TO ECODE")) E_CODE, try_to_date("DATE", 'DD-MM-YYYY') DT, count(*) RECEIVED
    from V2RETAIL.KARMA.TASKS_REPORT where "ASSIGNED TO ECODE" is not null group by 1, 2),
  done as (
    select upper(trim("ASSIGNED TO ECODE")) E_CODE, try_to_date("ACTION DATE", 'DD-MM-YYYY') DT, count(*) COMPLETED
    from V2RETAIL.KARMA.TASKS_REPORT where "STATUS NAME" = 'Completed' and "ASSIGNED TO ECODE" is not null group by 1, 2),
  k as (select E_CODE, DT from yn union select E_CODE, DT from raised union select E_CODE, DT from recv
        union select E_CODE, DT from done),
  emp as (select * from V2RETAIL.SILVER.KARMA_EMP_SNAP where REPORT_DATE = :rd)
  select k.DT, k.E_CODE, e.EMP_NAME, coalesce(e.DEPT, 'Not in Karma employee list') DEPT, e.SUB_DEPT_1, e.SUB_DEPT_2,
         e.SUB_DEPT_3, e.LOC, coalesce(e.LOC_GROUP, 'Unknown') LOC_GROUP, e.DESIGNATION, e.MANAGER, e.MANAGER_ECODE,
         coalesce(yn.LINES, 0) LINES, coalesce(yn.HOURS, 0) HOURS, coalesce(yn.MC_BGT, 0) MC_BGT, coalesce(yn.MC_ACT, 0) MC_ACT,
         coalesce(yn.LINKED_TASKS, 0) LINKED_TASKS, coalesce(yn.CLOSED_TASKS, 0) CLOSED_TASKS, coalesce(yn.KRAS, 0) KRAS,
         coalesce(r.RAISED, 0) RAISED, coalesce(v.RECEIVED, 0) RECEIVED, coalesce(d.COMPLETED, 0) COMPLETED,
         iff(coalesce(yn.HOURS, 0) > 0, k.E_CODE, null) FILLER_ECODE,
         iff(coalesce(yn.HOURS, 0) > 0, yn.HOURS, null) HOURS_FILLED,
         case when coalesce(yn.HOURS, 0) = 0 then '1 · Not filled' when yn.HOURS <= 5 then '2 · Up to 5 h'
              when yn.HOURS < 7 then '3 · 5-7 h' else '4 · 7 h and more' end HOURS_BAND,
         current_timestamp()::timestamp_ntz BUILT_AT
  from k
  left join yn     on yn.E_CODE = k.E_CODE and yn.DT = k.DT
  left join raised r on r.E_CODE = k.E_CODE and r.DT = k.DT
  left join recv v   on v.E_CODE = k.E_CODE and v.DT = k.DT
  left join done d   on d.E_CODE = k.E_CODE and d.DT = k.DT
  left join emp e    on e.E_CODE = k.E_CODE
  where k.DT is not null and k.DT between '2025-12-01' and :rd;
  n_day := sqlrowcount;

  return 'report date ' || rd || ' | snapshot rows ' || n_snap || ' | employee-day rows ' || n_day;
end;
$$
;

create or replace view V2RETAIL.PULSE.V_BI_KARMA_EMP comment = 'V2Pulse dataset: Karma - employee productivity (daily snapshot)'
as select * from V2RETAIL.SILVER.KARMA_EMP_SNAP;

create or replace view V2RETAIL.PULSE.V_BI_KARMA_DAY comment = 'V2Pulse dataset: Karma - employee x day activity'
as select * from V2RETAIL.SILVER.KARMA_EMP_DAY;

grant select on view V2RETAIL.PULSE.V_BI_KARMA_EMP to role PULSE_SVC_ROLE;
grant select on view V2RETAIL.PULSE.V_BI_KARMA_DAY to role PULSE_SVC_ROLE;

create or replace task V2RETAIL.SILVER.TASK_KARMA_DAILY
  warehouse = COMPUTE_WH
  schedule = 'USING CRON 45 9 * * * Asia/Kolkata'
  comment = 'Karma productivity (V2Pulse): SILVER.SP_KARMA_BUILD()'
as call V2RETAIL.SILVER.SP_KARMA_BUILD();
alter task V2RETAIL.SILVER.TASK_KARMA_DAILY resume;
