-- V2Pulse Snowflake setup.
-- Part A (curated views) can be run by DATA_PLATFORM_ADMIN. Part B (service identity,
-- warehouse, monitor) needs ACCOUNTADMIN. Every statement is idempotent.

-- ============================================================ PART A: views V2Pulse reads
use role DATA_PLATFORM_ADMIN;
create schema if not exists V2RETAIL.PULSE comment = 'Curated read-only views for the V2Pulse intranet portal';

-- Employee directory. Deliberately excludes Password / PasswordHash, bank, address, family and ID-proof
-- columns: V2Pulse never needs them, and a view is the only way to keep a SELECT grant column-safe.
create or replace view V2RETAIL.PULSE.V_HR_EMPLOYEE as
select upper(trim(e."Ecode"))                                                         as emp_code,
       coalesce(nullif(trim(e."FULL NAME"), ''),
                trim(concat_ws(' ', e."FirstName", e."MiddleName", e."LastName")))      as display_name,
       lower(nullif(trim(e."EMAIL ADDRESS"), ''))                                       as email,
       nullif(trim(e."MOBILE"), '')                                                     as mobile,
       d."DesignationName"                                                              as designation,
       e."DepartmentId"                                                                 as department_id,
       dp."DepartmentName"                                                              as department_name,
       l."STCode"                                                                       as location_code,
       l."LocationName"                                                                 as location_name,
       upper(nullif(trim(e."ReportHeadEcode"), ''))                                     as reports_to,
       (e."IsActive" and not coalesce(e."IsDeleted", false)
        and coalesce(e."isresgined", false) = false and e."DateOfLeft" is null)         as is_active,
       e."UpdatedOn"                                                                    as updated_on
from V2RETAIL.BRONZE.HRMS_EMP_MASTER e
left join V2RETAIL.BRONZE.HRMS_LOCATION_MASTER l     on l."LocationId" = e."LocationId"
left join V2RETAIL.BRONZE.HRMS_DESIGNATION_MASTER d  on d."DesignationId" = e."DesignationId"
left join V2RETAIL.BRONZE.HRMS_DEPT_MASTER dp        on dp."DepartmentId" = e."DepartmentId"
where e."Ecode" is not null and coalesce(e."IsDeleted", false) = false;

create or replace view V2RETAIL.PULSE.V_HR_LOCATION as
select "LocationId" as location_id, "STCode" as location_code, "LocationName" as location_name,
       "LocationType" as location_type, "IsActive" as is_active
from V2RETAIL.BRONZE.HRMS_LOCATION_MASTER
where coalesce("IsDeleted", false) = false and nullif(trim("STCode"), '') is not null;

create or replace view V2RETAIL.PULSE.V_HR_DEPARTMENT as
select "DepartmentId" as department_id, "DepartmentName" as department_name, "DepartmentCode" as department_code,
       "isActive" as is_active
from V2RETAIL.BRONZE.HRMS_DEPT_MASTER
where coalesce("isDeleted", false) = false;

-- Store hierarchy (Zone > Region > Cluster > Store) from the Retail Hub app.
-- Note: in the source, ZONE_NAME / REGION_NAME hold people's names, not area names; V2Pulse
-- labels areas by their codes and takes the area heads from the *_EMP_ECODE columns.
-- The source also lists non-stores: RH01 / RH03 (named "TEST"; RH01 is head office), RH02 (training),
-- DHYANU, a water park, and ~17 hubs. They are excluded here so HO and hub staff are not treated as
-- store staff (hubs come in as sites from the HRMS locations). About 16 real stores have no zone /
-- region / cluster yet; they are kept and sit directly under the company until Retail Hub maps them.
create or replace view V2RETAIL.PULSE.V_STORE_HIERARCHY as
select upper(trim(STORE_CODE)) as store_code, STORE_NAME as store_name, nullif(STORE_STATE, '') as state,
       ZONE_CODE as zone_code, REGION_CODE as region_code, CLUSTER_CODE as cluster_code,
       upper(nullif(trim(ZONE_EMP_ECODE), '')) as zone_head_ecode,
       upper(nullif(trim(REGION_EMP_ECODE), '')) as region_head_ecode,
       upper(nullif(trim(CLUSTER_EMP_ECODE), '')) as cluster_head_ecode, CLUSTER_EMP_NAME as cluster_head_name,
       SYNCED_AT as synced_at
from V2RETAIL.RETAIL_HUB.STORE_HIERARCHY
where nullif(trim(STORE_CODE), '') is not null
  and upper(trim(STORE_CODE)) not in ('RH01', 'RH02', 'RH03', 'DHYANU', 'WATER PARK')
  and upper(STORE_NAME) not like '%HUB%';

-- ============================================================ PART B: service identity (ACCOUNTADMIN)
-- use role ACCOUNTADMIN;
-- create role if not exists PULSE_SVC_ROLE comment = 'V2Pulse service role: read PULSE views only';
-- grant role PULSE_SVC_ROLE to role SYSADMIN;
-- grant usage on warehouse COMPUTE_WH to role PULSE_SVC_ROLE;
-- grant usage on database V2RETAIL to role PULSE_SVC_ROLE;
-- grant usage on schema V2RETAIL.PULSE to role PULSE_SVC_ROLE;
-- grant select on all views in schema V2RETAIL.PULSE to role PULSE_SVC_ROLE;
-- grant select on future views in schema V2RETAIL.PULSE to role PULSE_SVC_ROLE;
-- create user if not exists PULSE_SVC type = SERVICE default_role = PULSE_SVC_ROLE default_warehouse = COMPUTE_WH
--   comment = 'V2Pulse intranet portal (DataCenter 192.168.151.27)';
-- grant role PULSE_SVC_ROLE to user PULSE_SVC;
-- Then, with the public key that deploy\keygen.bat prints on the server:
-- alter user PULSE_SVC set rsa_public_key = 'MIIB...';
