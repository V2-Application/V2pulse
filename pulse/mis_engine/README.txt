MIS engine - copied from DFS (GitHub V2-Application/sale-plan, branch main, commit 57454e8 of 16-Oct-2026)
==========================================================================================================

Copied unchanged from ml-engine/app:
  sf_cancel.py, mis_run_log.py
  routes/sf_var_combined.py, sf_mis_report.py, sf_mis_trend.py, sf_day_trend.py, sf_day_trend_report.py,
  routes/sf_mis_monitor.py, sf_mis_lineage.py, sf_prod_master.py

Changed for V2Pulse:
  routes/sf_mis_report.py, routes/sf_day_trend_report.py  _who(): the report owner is the signed-in V2Pulse
                                                          employee code (CURRENT_USER), not the browser's value.

V2Pulse shims (replace DFS modules the copied files import):
  worker/auto_gandola_sf.py   Snowflake connections -> pulse.data.snowflake.connect(profile="mis")
                              (PULSE_SVC user, role PULSE_MIS_SF_ROLE, warehouses PULSE_MIS_READ/COMPUTE_WAREHOUSE)
  routes/sf_gandola.py        the CSV helpers only (_stream_csv, _na_row, _text_cols)
  routes/sf_vert_inv_art.py   the condition helper only (_cond)

V2Pulse additions:
  __init__.py                 CURRENT_USER
  export.py                   Excel / workbook / template output (DFS did these in Node with exceljs)

Mounted by pulse/modules/mis.py under /api/v1/mis/<section>/... with V2Pulse sign-in and mis.* permissions.
To take a newer DFS version: copy the files listed above again, re-apply the two _who() changes, and check
that no new DFS module is imported (grep "from \.\." and "from \.").
