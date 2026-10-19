-- V2Pulse Snowflake Part B, ready to run as ACCOUNTADMIN (generated on DataCenter 2026-10-13).
-- Same as PART B of snowflake_setup.sql, uncommented, with the server's PULSE_SVC public key filled in.
use role ACCOUNTADMIN;
create role if not exists PULSE_SVC_ROLE comment = 'V2Pulse service role: read PULSE views only';
grant role PULSE_SVC_ROLE to role SYSADMIN;
grant usage on warehouse COMPUTE_WH to role PULSE_SVC_ROLE;
grant usage on database V2RETAIL to role PULSE_SVC_ROLE;
grant usage on schema V2RETAIL.PULSE to role PULSE_SVC_ROLE;
grant select on all views in schema V2RETAIL.PULSE to role PULSE_SVC_ROLE;
grant select on future views in schema V2RETAIL.PULSE to role PULSE_SVC_ROLE;
create user if not exists PULSE_SVC type = SERVICE default_role = PULSE_SVC_ROLE default_warehouse = COMPUTE_WH
  comment = 'V2Pulse intranet portal (DataCenter 192.168.151.27)';
grant role PULSE_SVC_ROLE to user PULSE_SVC;
-- Public key generated on the server by setup.bat (the private key stays in keys on the server):
alter user PULSE_SVC set rsa_public_key = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAqhlJj/N5t0cJELeWle/AImsX81eg12IfsW/BshtkjvhNNCVZNV12igcR1VZFSzxaDooiad7Tn3gAjPScSrePbawuxcbof7uFL4WWAK3Wm1LZOlSXafoclFHNBhvR1Fc8oCP0DG7gS4KUTky2k41cwkaZEBp2yiIjX48Gi2JSQTZBscxj5ejxC90WJvcwXyurrRWP4hkVajsfgssfzA2KHjecYMsTr90W7I0aqjkftAFXD0WmQk6cT0UDoaMTaoEPdFBDG4JIhJ6itS0ispoYZ2yLs1jaCU1qiXHOYq+q/UbLvkLOmt84TGu9zV+is/S4suTcqGtAlepm5H9yfv+NYQIDAQAB';
