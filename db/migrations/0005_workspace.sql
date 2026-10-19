-- Workspace features: favourites, announcements. (Recently opened comes from audit.access page views;
-- usage analytics read the audit tables; neither needs new storage.)

create table core.favorite (
  user_id     uuid not null references iam.users(id) on delete cascade,
  kind        text not null check (kind in ('report','master','bi_report','portal')),
  ref         text not null,                 -- report code, master code, dynamic report id, portal id
  created_at  timestamptz not null default now(),
  primary key (user_id, kind, ref)
);

create table core.announcement (
  id              uuid primary key default gen_random_uuid(),
  title           text not null,
  body            text not null default '',
  severity        text not null default 'info' check (severity in ('info','success','warning','critical')),
  link_url        text,
  link_label      text,
  audience_roles  text[] not null default '{}',   -- empty = everyone
  audience_depts  text[] not null default '{}',   -- functional departments; empty = all
  pinned          boolean not null default false,  -- shown as a banner on Home until read
  starts_at       timestamptz not null default now(),
  ends_at         timestamptz,
  created_by      uuid references iam.users(id),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  updated_by      uuid references iam.users(id)
);
create index announcement_live on core.announcement (starts_at desc);

create table core.announcement_read (
  user_id          uuid not null references iam.users(id) on delete cascade,
  announcement_id  uuid not null references core.announcement(id) on delete cascade,
  read_at          timestamptz not null default now(),
  primary key (user_id, announcement_id)
);

grant select, insert, update, delete on core.favorite, core.announcement, core.announcement_read to pulse_app;
