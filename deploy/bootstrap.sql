-- Run ONCE as the Postgres superuser on the V2Pulse instance (port 5435 on .27).
-- Replace the two passwords first; keep them in env.bat only.
create role pulse_owner login password 'CHANGE_ME_OWNER';
create role pulse_app   login password 'CHANGE_ME_APP' nobypassrls;
create database pulse owner pulse_owner encoding 'UTF8' template template0;
\connect pulse
revoke all on schema public from public;
grant connect on database pulse to pulse_app;
