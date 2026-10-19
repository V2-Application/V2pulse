-- V2Pulse Karma FM / LOBM Yes-No: rebuild of the snapshot table behind the FM and LOBM reports.
-- Run as ACCOUNTADMIN / DATA_PLATFORM_ADMIN on a Medium+ warehouse (about 90M answer rows; a few minutes). Safe to re-run.
-- The views V_KARMA_FMLOBM_FILL, V_WM_KARMA_FM_DETAIL / _SUMMARY, V_WM_KARMA_LOBM_DETAIL / _SUMMARY and V_BI_KARMA_FMLOBM_SHEET
-- read this table live, so only this statement needs re-running to refresh them. Nothing in SILVER or GOLD is touched.
--   Source : V2RETAIL.RETAIL_HUB.RPT_KARMA_SUBMISSIONS_MV (answers; same cleaning as SILVER.FACT_KARMA_ANSWER)
--   Budget : V2RETAIL.GOLD.DIM_KARMA_ROLE_BUDGET (FM_BGT / LOBM_BGT = headcount per store; budget/day = headcount x sheets)
--   Fill   : a distinct submission per store x sheet x day; for FM physical gondola review (one submission per gondola) a
--            distinct employee. Matches the Excel FM / LOBM store-wise reports (LOBM HK11 MTD 98.75% in both).
-- To list the view definitions:  select get_ddl('VIEW', 'V2RETAIL.PULSE.V_WM_KARMA_FM_SUMMARY');   (etc.)

create or replace table V2RETAIL.PULSE.KARMA_FMLOBM_SUB comment = 'V2Pulse: FM / LOBM Yes-No checklist submissions (one row per submission), built from RETAIL_HUB.RPT_KARMA_SUBMISSIONS_MV. Additive; GOLD/SILVER untouched. Rebuild to refresh.' as
with p as (
  select distinct process_name, case when process_name ilike 'LOBM%' then 'LOBM_YN' else 'FM_YN' end report_key,
    case left(process_name, 2)
      when '1.' then '1.FM Y_N FLOOR WALK' when '2.' then '2.FM Y_N MAJ CAT COUNTING' when '3.' then '3.FM Y_N C ARTICLE IMPL & LIQUIDATION'
      when '4.' then '4.FM Y_N PHY GND REVIEW' when '5.' then '5.FM Y_N ONE MAJ CAT REVIEW' when '6.' then '6.FM Y_N GAP REPORT WORKING & ATR'
      when '7.' then '7.FM Y_N DPN & PLANOGRAM BGT VS ACT' when '8.' then '8.FM Y_N L ARTICLE & MIX ARTICLE WORKING'
      when '9.' then '9.FM Y_N DAY END CHECKLIST'
      else case when process_name ilike '%C ARTICLE%' then 'LOBM Y_N C ARTICLE DISPLAY AS PER SOP'
                when process_name ilike '%CLEANING%' then 'LOBM Y_N GND CLEANING & DUSTING'
                when process_name ilike '%GANDOLA%' then 'LOBM Y_N PHYSICAL GANDOLA REVIEW'
                else 'LOBM Y_N STOCK DISPLAY & MAINTENANCE' end end sheet_name,
    case left(process_name, 2) when '1.' then 1 when '2.' then 2 when '3.' then 3 when '4.' then 4 when '5.' then 5 when '6.' then 6
      when '7.' then 7 when '8.' then 8 when '9.' then 9
      else case when process_name ilike '%PHYSICAL GANDOLA%' then 1 when process_name ilike '%STOCK DISPLAY%' then 2
                when process_name ilike '%C ARTICLE%' then 3 else 4 end end sheet_order
  from V2RETAIL.RETAIL_HUB.RPT_KARMA_SUBMISSIONS_MV where process_name ilike '%FM Y%' or process_name ilike 'LOBM YN%'),
hier as (select distinct upper(trim(STORE_CODE)) STORE_CODE from V2RETAIL.RETAIL_HUB.STORE_HIERARCHY),
a as (
  select m.SUBMISSION_ID, p.report_key, p.sheet_name, p.sheet_order, m.PROCESS_NAME,
    coalesce(nullif(upper(trim(m.STORE_CODE)), ''), h.STORE_CODE) STORE_CODE, upper(trim(m.SUBMITTED_BY)) SUBMITTED_BY, m.REPORT_DATE,
    convert_timezone('Asia/Kolkata', m.SUBMITTED_AT)::timestamp_ntz SUBMITTED_AT_IST, m.STATUS, m.APPROVAL_STATUS, m.QUESTION_NUMBERING,
    nullif(trim(coalesce(m.ANSWER_ENGLISH, m.ANSWER_VALUE)), '') ans,
    iff(m.EVIDENCE_URLS is null or trim(m.EVIDENCE_URLS) = '', 0, array_size(split(m.EVIDENCE_URLS, ' | '))) ev
  from V2RETAIL.RETAIL_HUB.RPT_KARMA_SUBMISSIONS_MV m join p on p.process_name = m.PROCESS_NAME
  left join hier h on upper(trim(m.SUBMITTED_BY)) = h.STORE_CODE
  where m.REPORT_DATE between '2024-01-01' and dateadd(day, 1, current_date()))
select SUBMISSION_ID, max(report_key) REPORT_KEY, max(sheet_name) SHEET_NAME, max(sheet_order) SHEET_ORDER, max(REPORT_DATE) REPORT_DATE,
  left(SUBMISSION_ID, 12) REF_NO, max(STORE_CODE) STORE_CODE, max(SUBMITTED_BY) SUBMITTED_BY, max(SUBMITTED_AT_IST) SUBMITTED_DATETIME_IST,
  max(STATUS) STATUS, max(APPROVAL_STATUS) APPROVAL_STATUS,
  count_if(ans is not null) QUESTIONS_ATTEMPTED, count(*) TOTAL_QUESTIONS,
  iff(count(*) = 0, 0, round(count_if(ans is not null) / count(*) * 100, 1)) COMPLETION_PCT,
  count_if(upper(ans) in ('YES','Y')) YES_COUNT, count_if(upper(ans) in ('NO','N')) NO_COUNT, count_if(upper(ans) in ('NA','N/A')) NA_COUNT,
  sum(ev) EVIDENCE_COUNT,
  iff(sum(ev) > 0, 'https://retailapi.v2kart.in/functions/v1/karma-evidence-gallery?sid=' || SUBMISSION_ID, null) PROOF_URL,
  upper(max(iff((QUESTION_NUMBERING = '1.1.1' and PROCESS_NAME not ilike '5.FM%') or (QUESTION_NUMBERING = '1.1.2' and PROCESS_NAME ilike '5.FM%'), ans, null))) PERSON,
  max(iff(QUESTION_NUMBERING = '1.1.2' and PROCESS_NAME ilike '4.FM%', upper(ans), null)) EMP_ID,
  current_timestamp() BUILT_AT
from a group by SUBMISSION_ID;
