-- Masters library and admin-managed access control.
--
-- Masters: a catalog of master-data sets (store, article, vendor, ...). Each master reads one curated
-- Snowflake view in V2RETAIL.PULSE and is browsed with server-side search, filters and paging. Masters
-- are filed in their own folder trees (section 'masters'): one Central tree and one per department,
-- reusing nav.folder / nav.item from the report library.
--
-- RBAC: admins edit role permissions, create custom roles and maintain the HR auto-assignment rules.
-- A system role an admin has changed is marked customised, so a deploy no longer resets it.

create schema if not exists mst;

-- ---------------------------------------------------------------- folder trees get a section
alter table nav.folder add column section text not null default 'reports' check (section in ('reports','masters'));
drop index nav.folder_sibling_name;
create unique index folder_sibling_name on nav.folder (section, tree, coalesce(parent_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name))
  where not is_archived;
drop index nav.folder_tree;
create index folder_tree on nav.folder (section, tree, parent_id, sort_order);
alter table nav.import add column section text not null default 'reports' check (section in ('reports','masters'));

-- ---------------------------------------------------------------- master catalog
create table mst.master (
  id              uuid primary key default gen_random_uuid(),
  code            text not null unique check (code ~ '^[a-z0-9][a-z0-9_.-]{1,60}$'),
  name            text not null,
  description     text not null default '',
  layer           text not null default 'department' check (layer in ('central','department')),
  owner_dept      text,
  steward_name    text,                              -- who answers questions about this master
  source_object   text not null check (source_object ~ '^V2RETAIL\.PULSE\.[A-Z0-9_]+$'),
  columns         jsonb not null default '[]',       -- [{name, label, type, key, searchable, filterable, hidden}]
  default_sort    text,
  scope_column    text,                              -- store/site column for row scope; null = not scoped
  sensitivity     text not null default 'internal' check (sensitivity in ('internal','restricted','confidential')),
  refresh_note    text not null default '',          -- how and when the source is refreshed, for people
  status          text not null default 'live' check (status in ('draft','live','deprecated')),
  audience_roles  text[] not null default '{}',      -- empty = anyone who can see the folder
  tags            text[] not null default '{}',
  cache_seconds   integer not null default 900,
  row_count       bigint,
  profiled_at     timestamptz,
  profile_error   text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  updated_by      uuid references iam.users(id)
);

-- nav.item can now point at a master
alter table nav.item add column master_id uuid references mst.master(id) on delete cascade;
do $$
declare c record;
begin
  for c in select conname from pg_constraint where conrelid = 'nav.item'::regclass and contype = 'c' loop
    execute format('alter table nav.item drop constraint %I', c.conname);
  end loop;
end $$;
alter table nav.item add constraint item_kind_check check (kind in ('report','portal','link','master'));
alter table nav.item add constraint item_ref_check check (
  (kind = 'report' and report_id is not null) or (kind = 'portal' and portal_id is not null)
  or (kind = 'link' and url is not null) or (kind = 'master' and master_id is not null));

-- ---------------------------------------------------------------- RBAC
alter table iam.role add column default_permissions text[] not null default '{}';
alter table iam.role add column is_customized boolean not null default false;
alter table iam.role add column created_at timestamptz not null default now();
alter table iam.role add column updated_by uuid references iam.users(id);
alter table iam.role_rule add column updated_at timestamptz not null default now();
alter table iam.role_rule add column updated_by uuid references iam.users(id);

grant usage on schema mst to pulse_app;
grant select, insert, update, delete on all tables in schema mst to pulse_app;
