-- MIS pages: saved views (column layout, filters, sort) per user and page. DFS kept these in SQL Server
-- USER_TABLE_PREFS / USER_COL_PRESETS; V2Pulse keeps them here.
create table ops.mis_view (
  id          bigserial primary key,
  user_id     uuid not null,
  page        text not null,
  name        text not null,
  state       jsonb not null default '{}',
  is_default  boolean not null default false,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (user_id, page, name)
);
grant select, insert, update, delete on ops.mis_view to pulse_app;
grant usage on all sequences in schema ops to pulse_app;
