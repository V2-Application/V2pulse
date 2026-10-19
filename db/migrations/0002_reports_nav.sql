-- Report library: a report catalog plus custom folder trees for navigating it.
--
-- Trees: one 'central' tree and one per department ('dept:HR', 'dept:SOURCING', …). Folders
-- nest to any depth. A report lives once in rpt.report and can be placed in any number of
-- folders (in any tree) through nav.item, which can also hold portal links and plain links.

create schema if not exists rpt;
create schema if not exists nav;

create table rpt.report (
  id              uuid primary key default gen_random_uuid(),
  code            text not null unique,
  name            text not null,
  description     text not null default '',
  layer           text not null default 'department' check (layer in ('central','department')),
  owner_dept      text,
  owner_name      text,
  kind            text not null check (kind in ('table','link','portal','file','control_tower')),
  -- kind=table         query_code -> data.query_def, rendered as a scoped, paged table
  -- kind=link          url, opened in a new tab
  -- kind=portal        portal_code -> portal.portal
  -- kind=file          file_path under PULSE_FILES_ROOT (e.g. the 01-DAILY share), downloaded
  -- kind=control_tower ct_report_id -> a Report Portal catalog entry, shown with its live status
  query_code      text references data.query_def(code) on update cascade,
  url             text,
  portal_code     text,
  file_path       text,
  ct_report_id    text,
  status          text not null default 'live' check (status in ('draft','live','deprecated')),
  audience_roles  text[] not null default '{}',     -- empty = anyone who can see the folder
  tags            text[] not null default '{}',
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  updated_by      uuid references iam.users(id)
);

create table nav.folder (
  id              uuid primary key default gen_random_uuid(),
  tree            text not null check (tree = 'central' or tree ~ '^dept:[A-Z_]+$'),
  parent_id       uuid references nav.folder(id) on delete cascade,
  name            text not null,
  name_hi         text,
  description     text not null default '',
  icon            text,
  sort_order      integer not null default 100,
  audience_roles  text[] not null default '{}',     -- empty = inherits: anyone who can see the tree
  path            text not null,                    -- '/<id>/<id>/' for subtree queries
  is_archived     boolean not null default false,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  updated_by      uuid references iam.users(id)
);
create index folder_tree on nav.folder (tree, parent_id, sort_order);
create index folder_path on nav.folder (path text_pattern_ops);
-- sibling names are unique (case-insensitive) among live folders, so a pasted design merges cleanly
create unique index folder_sibling_name on nav.folder (tree, coalesce(parent_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name))
  where not is_archived;

create table nav.item (
  id           uuid primary key default gen_random_uuid(),
  folder_id    uuid not null references nav.folder(id) on delete cascade,
  kind         text not null check (kind in ('report','portal','link')),
  report_id    uuid references rpt.report(id) on delete cascade,
  portal_id    uuid references portal.portal(id) on delete cascade,
  url          text,
  title        text,                                -- overrides the report / portal name in this folder
  sort_order   integer not null default 100,
  created_at   timestamptz not null default now(),
  updated_by   uuid references iam.users(id),
  check ((kind = 'report' and report_id is not null) or (kind = 'portal' and portal_id is not null)
         or (kind = 'link' and url is not null))
);
create index item_folder on nav.item (folder_id, sort_order);

-- A pasted design is kept with its outcome, so "who restructured the tree" is answerable later.
create table nav.import (
  id           bigserial primary key,
  tree         text not null,
  target_folder_id uuid,
  mode         text not null check (mode in ('merge','replace')),
  source_text  text not null,
  summary      jsonb not null default '{}',
  applied_at   timestamptz not null default now(),
  applied_by   uuid references iam.users(id)
);

grant usage on schema rpt, nav to pulse_app;
grant select, insert, update, delete on all tables in schema rpt, nav to pulse_app;
grant usage on all sequences in schema nav to pulse_app;

-- Each session keeps its own idle window (4 h for elevated roles, 10 h otherwise), so a
-- sliding refresh never stretches an admin session to the normal length.
alter table iam.session add column idle_seconds integer not null default 36000;
