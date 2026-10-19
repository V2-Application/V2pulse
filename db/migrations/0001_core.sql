-- V2Pulse Phase 0 schema. Runs as pulse_owner. The application connects as pulse_app,
-- which is not the owner and has no BYPASSRLS, so row-level security always applies.
-- No contrib extensions are used (the embedded dev Postgres has none); gen_random_uuid()
-- is core since PG 13.

create schema if not exists core;
create schema if not exists iam;
create schema if not exists portal;
create schema if not exists audit;
create schema if not exists data;
create schema if not exists snap;
create schema if not exists ops;

-- Scope: the request sets these with SET LOCAL inside its transaction.
--   pulse.scope_all   'on' when the caller's scope covers the whole company
--   pulse.scope_sites comma list of store / site codes the caller may see
--   pulse.writer      'on' only inside snapshot refresh jobs
create or replace function core.scope_allows_site(site text) returns boolean
language sql stable as $$
  select coalesce(current_setting('pulse.scope_all', true), '') = 'on'
      or site = any (string_to_array(coalesce(current_setting('pulse.scope_sites', true), ''), ','))
$$;

create or replace function core.is_writer() returns boolean
language sql stable as $$
  select coalesce(current_setting('pulse.writer', true), '') = 'on'
$$;

-- ---------------------------------------------------------------- identity and scope
create table iam.org_node (
  id          uuid primary key default gen_random_uuid(),
  node_type   text not null check (node_type in ('company','zone','region','cluster','store','site','dept_root','dept','hr_dept')),
  code        text not null,
  name        text not null,
  parent_id   uuid references iam.org_node(id),
  path        text not null,              -- '/<id>/<id>/…/' ; descendants: path like ancestor.path || '%'
  attrs       jsonb not null default '{}',
  is_active   boolean not null default true,
  source      text not null default 'manual',
  updated_at  timestamptz not null default now(),
  unique (node_type, code)
);
create index org_node_path on iam.org_node (path text_pattern_ops);
create index org_node_parent on iam.org_node (parent_id);

create table iam.dept_map (
  hr_department_id   integer primary key,
  hr_department_name text not null,
  portal_dept_code   text            -- one of the dept nodes (HR, SOURCING, …); null = unassigned
);

create table iam.users (
  id                uuid primary key default gen_random_uuid(),
  emp_code          text not null unique,
  display_name      text not null,
  email             text,
  mobile            text,
  designation       text,
  hr_department     text,
  hr_department_id  integer,
  location_code     text,
  location_name     text,
  reports_to_code   text,
  home_node_id      uuid references iam.org_node(id),
  dept_node_id      uuid references iam.org_node(id),
  status            text not null default 'active' check (status in ('active','suspended','left')),
  source            text not null default 'hrms' check (source in ('hrms','manual','service')),
  email_locked      boolean not null default false,   -- admin-linked email the HR sync must not overwrite
  prefs             jsonb not null default '{}',
  last_hr_sync_at   timestamptz,
  last_login_at     timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create unique index users_email_lc on iam.users (lower(email)) where email is not null and status = 'active';
create index users_location on iam.users (location_code);

create table iam.role (
  code         text primary key,
  name         text not null,
  description  text not null default '',
  permissions  text[] not null default '{}',
  is_system    boolean not null default true,
  updated_at   timestamptz not null default now()
);

create table iam.user_role (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references iam.users(id) on delete cascade,
  role_code   text not null references iam.role(code) on update cascade,
  source      text not null check (source in ('rule','manual')),
  valid_from  timestamptz not null default now(),
  valid_to    timestamptz,
  granted_by  uuid references iam.users(id),
  reason      text,
  created_at  timestamptz not null default now(),
  unique (user_id, role_code, source)
);

create table iam.scope_grant (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references iam.users(id) on delete cascade,
  node_id     uuid not null references iam.org_node(id) on delete cascade,
  source      text not null check (source in ('rule','manual')),
  valid_to    timestamptz,
  granted_by  uuid references iam.users(id),
  reason      text,
  created_at  timestamptz not null default now(),
  unique (user_id, node_id, source)
);

create table iam.data_class_grant (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references iam.users(id) on delete cascade,
  data_class  text not null check (data_class in ('SALARY','PERSONAL','FINANCE_DETAIL')),
  valid_to    timestamptz,
  granted_by  uuid references iam.users(id),
  reason      text,
  created_at  timestamptz not null default now(),
  unique (user_id, data_class)
);

-- Rules that give roles automatically from HR data. First match per rule row applies;
-- several rules can match one user (roles are a union).
create table iam.role_rule (
  id          serial primary key,
  priority    integer not null default 100,
  role_code   text not null references iam.role(code) on update cascade,
  match       jsonb not null,   -- {"location_codes":[…]} | {"location_kind":"store"} | {"designation_like":"%STORE MANAGER%"} | {"hierarchy_head":"cluster"}
  note        text,
  is_active   boolean not null default true
);

create table iam.session (
  id                   uuid primary key default gen_random_uuid(),
  token_hash           bytea not null unique,
  user_id              uuid not null references iam.users(id) on delete cascade,
  provider             text not null,
  created_at           timestamptz not null default now(),
  last_seen_at         timestamptz not null default now(),
  expires_at           timestamptz not null,
  absolute_expires_at  timestamptz not null,
  ip                   text,
  user_agent           text,
  revoked_at           timestamptz
);
create index iam_session_user on iam.session (user_id);

create table iam.local_credential (
  user_id        uuid primary key references iam.users(id) on delete cascade,
  password_hash  text not null,
  failed_count   integer not null default 0,
  locked_until   timestamptz,
  set_by         uuid references iam.users(id),
  updated_at     timestamptz not null default now()
);

create table iam.oidc_state (
  state          text primary key,
  nonce          text not null,
  code_verifier  text not null,
  next_url       text not null default '/',
  created_at     timestamptz not null default now()
);

-- ---------------------------------------------------------------- portal directory
create table portal.portal (
  id                 uuid primary key default gen_random_uuid(),
  code               text not null unique,
  name               text not null,
  description        text not null default '',
  category           text not null,
  url                text not null,
  health_url         text,
  owner_dept         text,
  owner_name         text,
  login              text not null default 'own' check (login in ('own','sso','none')),
  network            text not null default 'lan' check (network in ('lan','internet')),
  icon               text not null default 'app',
  tags               text[] not null default '{}',
  audience_roles     text[] not null default '{}',   -- empty = every signed-in user
  how_to_get_access  text not null default '',
  is_active          boolean not null default true,
  is_placeholder     boolean not null default false, -- URL not yet confirmed by the owner
  sort_order         integer not null default 100,
  status             text not null default 'unchecked' check (status in ('up','down','degraded','unknown','unchecked')),
  status_checked_at  timestamptz,
  latency_ms         integer,
  status_detail      text,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  updated_by         uuid references iam.users(id)
);

create table portal.pin (
  user_id    uuid not null references iam.users(id) on delete cascade,
  portal_id  uuid not null references portal.portal(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, portal_id)
);

create table portal.usage (
  id         bigserial primary key,
  user_id    uuid not null references iam.users(id) on delete cascade,
  portal_id  uuid not null references portal.portal(id) on delete cascade,
  ts         timestamptz not null default now()
);
create index usage_user_ts on portal.usage (user_id, ts desc);

-- ---------------------------------------------------------------- audit (append-only)
-- Two logs, both partitioned by month so old months drop cheaply and recent months stay fast.
--   audit.event   ACTIONS: sign-in/out, failed sign-in, every create/update/delete, portal opens,
--                 grants, refreshes, exports. Retained 36 months.
--   audit.access  VIEWS: every API request and every SPA page view, with status and timing.
--                 Retained 13 months.
-- pulse_app may only INSERT and SELECT (no UPDATE / DELETE), so the app cannot rewrite history.
-- Partitions are created ahead by the audit_partitions job; the DEFAULT partition catches
-- anything written before that job has run, so a write never fails for a missing month.
create table audit.event (
  id              bigint generated always as identity,
  ts              timestamptz not null default now(),
  request_id      text,
  actor_id        uuid,
  actor_emp_code  text,
  action          text not null,              -- e.g. auth.login, portal.open, admin.role.grant
  outcome         text not null default 'ok' check (outcome in ('ok','denied','failed')),
  object_type     text,
  object_id       text,
  ip              text,
  user_agent      text,
  details         jsonb not null default '{}',
  primary key (id, ts)
) partition by range (ts);
create table audit.event_default partition of audit.event default;
create index audit_event_ts on audit.event (ts desc);
create index audit_event_actor on audit.event (actor_id, ts desc);
create index audit_event_action on audit.event (action, ts desc);
create index audit_event_object on audit.event (object_type, object_id, ts desc);

create table audit.access (
  id              bigint generated always as identity,
  ts              timestamptz not null default now(),
  request_id      text,
  actor_id        uuid,
  actor_emp_code  text,
  kind            text not null check (kind in ('api','page')),
  method          text,
  path            text not null,              -- API path, or SPA route for page views
  query           text,
  status          integer,
  ms              integer,
  ip              text,
  user_agent      text,
  session_id      uuid
) partition by range (ts);
create table audit.access_default partition of audit.access default;
create index audit_access_ts on audit.access (ts desc);
create index audit_access_actor on audit.access (actor_id, ts desc);
create index audit_access_path on audit.access (path, ts desc);

-- Creates monthly partitions from the current month to months_ahead; safe to re-run.
create or replace function audit.ensure_partitions(months_ahead integer default 2) returns integer
language plpgsql as $$
declare
  m date := date_trunc('month', now())::date;
  t text; made integer := 0; i integer;
begin
  for i in 0..months_ahead loop
    foreach t in array array['event','access'] loop
      if to_regclass(format('audit.%s_%s', t, to_char(m, 'YYYYMM'))) is null then
        execute format('create table audit.%I partition of audit.%I for values from (%L) to (%L)',
                       t || '_' || to_char(m, 'YYYYMM'), t, m, (m + interval '1 month')::date);
        made := made + 1;
      end if;
    end loop;
    m := (m + interval '1 month')::date;
  end loop;
  return made;
end $$;

-- Drops whole months older than the retention window. Runs as the owner (job uses owner URL).
create or replace function audit.drop_expired(event_months integer default 36, access_months integer default 13) returns integer
language plpgsql as $$
declare r record; dropped integer := 0; cutoff date;
begin
  for r in select c.relname from pg_inherits i join pg_class c on c.oid = i.inhrelid
           join pg_class p on p.oid = i.inhparent join pg_namespace n on n.oid = p.relnamespace
           where n.nspname = 'audit' and p.relname in ('event','access') and c.relname ~ '_[0-9]{6}$' loop
    cutoff := date_trunc('month', now() - make_interval(months => case when r.relname like 'event_%' then event_months else access_months end))::date;
    if to_date(right(r.relname, 6), 'YYYYMM') < cutoff then
      execute format('drop table audit.%I', r.relname);
      dropped := dropped + 1;
    end if;
  end loop;
  return dropped;
end $$;

select audit.ensure_partitions(2);

-- ---------------------------------------------------------------- data platform
create table data.contract (
  code              text primary key,
  name              text not null,
  description       text not null default '',
  owner_dept        text,
  source_object     text not null,             -- what the data comes from, for people
  source_sql        text not null,             -- Snowflake query; must return the target columns
  freshness_column  text not null,             -- business-date column in the result that proves freshness
  target_table      text not null,             -- snap.<name>
  cadence_minutes   integer not null,
  min_rows          integer not null default 1,
  max_swing_pct     numeric(6,2) not null default 30,
  allow_backward    boolean not null default false,  -- a newer refresh with an older as_of is rejected unless true
  enabled           boolean not null default true,
  updated_at        timestamptz not null default now()
);

create table data.dataset_state (
  dataset          text primary key references data.contract(code) on delete cascade,
  as_of            timestamptz,
  rows             integer,
  last_ok_at       timestamptz,
  last_refresh_id  bigint
);

create table data.refresh (
  id            bigserial primary key,
  dataset       text not null references data.contract(code) on delete cascade,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  verdict       text not null default 'running' check (verdict in ('running','ok','rejected','failed')),
  rows          integer,
  prev_rows     integer,
  as_of         timestamptz,
  prev_as_of    timestamptz,
  reason        text,
  sf_query_id   text,
  triggered_by  text
);
create index refresh_dataset on data.refresh (dataset, started_at desc);

create table data.query_def (
  code           text primary key,
  name           text not null,
  sql_template   text not null,        -- Snowflake SQL with %(param)s binds and a {scope} placeholder
  params         jsonb not null default '{}',   -- {"name": {"type": "str|int|date", "required": bool, "max_len": n}}
  scope_column   text,                 -- column the {scope} predicate filters on; null = not scoped
  permission     text not null,
  cache_seconds  integer not null default 300,
  max_rows       integer not null default 50000,
  enabled        boolean not null default true
);

create table data.query_cache (
  key         text primary key,
  result      jsonb not null,
  created_at  timestamptz not null default now(),
  expires_at  timestamptz not null
);

create table data.query_log (
  id           bigserial primary key,
  ts           timestamptz not null default now(),
  user_id      uuid,
  query_code   text not null,
  params       jsonb not null default '{}',
  rows         integer,
  ms           integer,
  cache_hit    boolean not null default false,
  sf_query_id  text,
  error        text
);

create table data.rate_bucket (
  user_id       uuid not null,
  bucket        text not null,
  window_start  timestamptz not null,
  count         integer not null default 0,
  primary key (user_id, bucket, window_start)
);

-- ---------------------------------------------------------------- snapshots (RLS)
create table snap.store_directory (
  store_code          text primary key,
  store_name          text not null,
  state               text,
  zone_code           text,
  region_code         text,
  cluster_code        text,
  cluster_head_ecode  text,
  cluster_head_name   text,
  region_head_ecode   text,
  zone_head_ecode     text,
  synced_at           timestamptz
);
alter table snap.store_directory enable row level security;
alter table snap.store_directory force row level security;
create policy scope_read  on snap.store_directory for select using (core.scope_allows_site(store_code));
create policy job_insert  on snap.store_directory for insert with check (core.is_writer());
create policy job_delete  on snap.store_directory for delete using (core.is_writer());

-- ---------------------------------------------------------------- operations
create table ops.ct_snapshot (
  id               bigserial primary key,
  taken_at         timestamptz not null default now(),
  payload          jsonb not null,
  refresh_seconds  numeric(8,1),
  error            text
);

create table ops.job (
  code        text primary key,
  name        text not null,
  schedule    text not null,      -- "every:15m" | "daily:07:30,13:45" (IST) | "manual"
  enabled     boolean not null default true,
  timeout_s   integer not null default 900,
  next_run_at timestamptz
);

create table ops.job_run (
  id            bigserial primary key,
  job_code      text not null references ops.job(code) on delete cascade,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  status        text not null default 'running' check (status in ('running','ok','failed')),
  message       text,
  triggered_by  text not null default 'schedule',
  host          text
);
create index job_run_code on ops.job_run (job_code, started_at desc);
create table ops.job_request (
  id            bigserial primary key,
  job_code      text not null references ops.job(code) on delete cascade,
  requested_at  timestamptz not null default now(),
  requested_by  uuid,
  claimed_at    timestamptz
);

-- ---------------------------------------------------------------- grants for pulse_app
grant usage on schema core, iam, portal, audit, data, snap, ops to pulse_app;
grant execute on all functions in schema core to pulse_app;
grant select, insert, update, delete on all tables in schema iam, portal, data, ops to pulse_app;
grant select, insert, delete on all tables in schema snap to pulse_app;
grant select, insert on all tables in schema audit to pulse_app;   -- append-only: no update, no delete
alter default privileges in schema audit grant select, insert on tables to pulse_app;  -- future monthly partitions
grant usage on all sequences in schema iam, portal, audit, data, ops to pulse_app;
