-- Dump Scheduler: the DFS dump kinds beyond MIS reports (pulse/dumps/dfs_kinds.py). A MIS day-wise load writes no
-- files, so it has no folder.
alter table ops.dump_schedule drop constraint if exists dump_schedule_kind_check;
alter table ops.dump_schedule add constraint dump_schedule_kind_check
  check (kind in ('master', 'mis_report', 'mis_book', 'day_trend', 'inv_report', 'bin_wise', 'tna', 'mis_day_load'));
