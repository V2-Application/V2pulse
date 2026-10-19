-- Folder-tree names are data, not code. One row per library section and tree: 'central', or 'dept:<CODE>'
-- for a department tree. A tree without a row shows its default (Central / the department's name).
-- Renamed from the Library trees page (/manage/trees) or PUT /api/v1/<section>/trees/<tree>.

create table if not exists nav.tree_name (
  section     text not null check (section in ('reports','masters')),
  tree        text not null check (tree = 'central' or tree ~ '^dept:[A-Z_]+$'),
  name        text not null check (length(btrim(name)) between 1 and 60),
  name_hi     text check (name_hi is null or length(btrim(name_hi)) between 1 and 60),
  updated_at  timestamptz not null default now(),
  updated_by  uuid references iam.users(id),
  primary key (section, tree)
);

-- The two Central trees start with the name they had before, so nothing changes until someone renames them.
insert into nav.tree_name (section, tree, name) values ('reports', 'central', 'Central'), ('masters', 'central', 'Central')
  on conflict do nothing;

grant select, insert, update, delete on nav.tree_name to pulse_app;
