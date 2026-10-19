-- Allow admin-created top-level trees. A new tree code 'custom:<slug>' (lowercase alphanumeric) joins
-- the existing 'central' and 'dept:<CODE>' trees. Relax the tree CHECK on nav.folder and nav.tree_name.
-- Drop the existing tree check by whatever name it has (inline column checks are auto-named), then re-add
-- a widened one with a fixed name.
do $$
declare c text;
begin
  for c in
    select conname from pg_constraint
    where conrelid = 'nav.folder'::regclass and contype = 'c' and pg_get_constraintdef(oid) ilike '%dept:[A-Z_]%'
  loop
    execute format('alter table nav.folder drop constraint %I', c);
  end loop;
  for c in
    select conname from pg_constraint
    where conrelid = 'nav.tree_name'::regclass and contype = 'c' and pg_get_constraintdef(oid) ilike '%dept:[A-Z_]%'
  loop
    execute format('alter table nav.tree_name drop constraint %I', c);
  end loop;
end $$;

alter table nav.folder
  add constraint folder_tree_check
  check (tree = 'central' or tree ~ '^dept:[A-Z_]+$' or tree ~ '^custom:[a-z0-9]+$');

alter table nav.tree_name
  add constraint tree_name_tree_check
  check (tree = 'central' or tree ~ '^dept:[A-Z_]+$' or tree ~ '^custom:[a-z0-9]+$');
