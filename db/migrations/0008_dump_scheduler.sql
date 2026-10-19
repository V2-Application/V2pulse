-- Dump Scheduler (ported from DFS / sale-plan): scheduled CSV / Excel exports of V2Pulse masters and MIS
-- reports into a folder per schedule (<folder>\YYYY_MM_DD\<file>). Runs are executed by the V2Pulse worker
-- (job dump_dispatch); failures are e-mailed to the owner and logged in ops.dump_mail_log.

create table ops.dump_schedule (
  id              bigserial primary key,
  name            text not null,
  kind            text not null check (kind in ('master', 'mis_report', 'mis_book')),
  target          text not null,                    -- master code | MIS report slug | comma list of slugs (book)
  config          jsonb not null default '{}',      -- per kind: filters, date rule, sheet names, layout ...
  format          text not null default 'xlsx' check (format in ('csv', 'xlsx')),
  folder          text not null,                    -- base folder; files go to <folder>\YYYY_MM_DD\
  file_name       text,                             -- without extension; default = schedule name
  cron_expr       text not null,                    -- 5-field cron, evaluated in `timezone`
  timezone        text not null default 'Asia/Kolkata',
  enabled         boolean not null default true,
  description     text,
  start_at        timestamptz,
  end_at          timestamptz,
  run_once        boolean not null default false,
  timeout_min     integer check (timeout_min between 1 and 600),
  allowed_roles   text[] not null default '{}',     -- roles that may see / run it besides the owner
  owner_user_id   uuid not null,
  owner_emp_code  text not null,
  created_by      text not null,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  next_run_at     timestamptz,
  last_run_at     timestamptz,
  last_status     text,
  last_run_id     bigint
);
create index dump_schedule_owner on ops.dump_schedule (owner_emp_code);
create index dump_schedule_due on ops.dump_schedule (next_run_at) where enabled;

create table ops.dump_run (
  id              bigserial primary key,
  schedule_id     bigint not null references ops.dump_schedule(id) on delete cascade,
  trigger_type    text not null check (trigger_type in ('schedule', 'manual')),
  triggered_by    text,
  status          text not null default 'QUEUED' check (status in ('QUEUED', 'RUNNING', 'DONE', 'FAILED', 'SKIPPED', 'STOPPED')),
  stop_requested  boolean not null default false,
  host            text,
  queued_at       timestamptz not null default now(),
  started_at      timestamptz,
  heartbeat_at    timestamptz,
  completed_at    timestamptz,
  duration_ms     bigint,
  files           integer not null default 0,
  rows_written    bigint not null default 0,
  file_paths      text[] not null default '{}',
  progress        text,
  error_msg       text
);
create index dump_run_sched on ops.dump_run (schedule_id, id desc);
create index dump_run_live on ops.dump_run (status) where status in ('QUEUED', 'RUNNING');

create table ops.dump_mail_log (
  id              bigserial primary key,
  run_id          bigint,
  schedule_id     bigint,
  schedule_name   text,
  status          text not null check (status in ('SENT', 'FAILED', 'NO_RECIPIENT', 'NOT_CONFIGURED')),
  mail_to         text,
  mail_cc         text,
  subject         text,
  error_msg       text,
  host            text,
  duration_ms     integer,
  created_at      timestamptz not null default now()
);
create index dump_mail_log_run on ops.dump_mail_log (run_id);

grant select, insert, update, delete on ops.dump_schedule, ops.dump_run, ops.dump_mail_log to pulse_app;
grant usage on all sequences in schema ops to pulse_app;

insert into ops.job (code, name, schedule, next_run_at)
values ('dump_dispatch', 'Dump Scheduler: start due and requested dumps (runs them in the background)', 'every:1m', now())
on conflict (code) do nothing;
