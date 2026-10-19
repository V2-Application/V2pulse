-- Dynamic (Power BI-style) reports.
--
-- A dataset is a governed semantic model over one curated Snowflake view in V2RETAIL.PULSE: named
-- dimensions and measures (sum, count, ratios ...). Queries are built only from these names, aggregated
-- in Snowflake, cached, and limited to the caller's store scope.
--
-- A dynamic report is a JSON definition (pages -> visuals + slicers) over one dataset. Library reports
-- are designed by admins (reports.design) and published to the Dynamic section; personal reports are
-- built by any user with reports.personal, saved under their own id, and visible only to them.

create schema if not exists bi;

create table bi.dataset (
  code            text primary key check (code ~ '^[a-z][a-z0-9_]{1,40}$'),
  name            text not null,
  description     text not null default '',
  source_object   text not null check (source_object ~ '^V2RETAIL\.PULSE\.[A-Z0-9_]+$'),
  dimensions      jsonb not null default '[]',   -- [{key, label, col, type: text|date|number, group}]
  measures        jsonb not null default '[]',   -- [{key, label, agg: sum|avg|min|max|count|count_distinct|ratio|growth, col | num+den, format}]
  snapshot_dim    text,                          -- dimension key of a snapshot date: queries default to its latest value
  scope_column    text,                          -- store column for row scope; null = not scoped
  refresh_note    text not null default '',
  status          text not null default 'live' check (status in ('draft','live','deprecated')),
  audience_roles  text[] not null default '{}',  -- empty = anyone who can open reports
  cache_seconds   integer not null default 900,
  updated_at      timestamptz not null default now(),
  updated_by      uuid references iam.users(id)
);

create table bi.report (
  id            uuid primary key default gen_random_uuid(),
  visibility    text not null check (visibility in ('library','personal')),
  owner_id      uuid references iam.users(id) on delete cascade,  -- personal reports only
  name          text not null,
  description   text not null default '',
  category      text not null default 'General',
  dataset_code  text not null references bi.dataset(code) on update cascade,
  definition    jsonb not null,
  status        text not null default 'draft' check (status in ('draft','published')),
  audience_roles text[] not null default '{}',
  copied_from   uuid,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  updated_by    uuid references iam.users(id),
  check ((visibility = 'personal') = (owner_id is not null))
);
create index report_owner on bi.report (owner_id) where visibility = 'personal';
create index report_library on bi.report (category, name) where visibility = 'library';

grant usage on schema bi to pulse_app;
grant select, insert, update, delete on all tables in schema bi to pulse_app;
