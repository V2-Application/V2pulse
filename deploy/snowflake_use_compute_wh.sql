-- V2Pulse: switch PULSE_SVC to the existing COMPUTE_WH and remove the PULSE_WH / RM_PULSE that Part B created.
-- Run as ACCOUNTADMIN. PULSE_WH was created on 2026-10-13 and only V2Pulse could use it.
use role ACCOUNTADMIN;
grant usage on warehouse COMPUTE_WH to role PULSE_SVC_ROLE;
alter user PULSE_SVC set default_warehouse = COMPUTE_WH;
revoke usage on warehouse PULSE_WH from role PULSE_SVC_ROLE;
drop warehouse if exists PULSE_WH;
drop resource monitor if exists RM_PULSE;
