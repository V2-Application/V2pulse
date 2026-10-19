# Allocation gap report (V2Pulse)

The allocation team's 22 gap formats (`Desktop\New folder\Allocation\*.xlsx`) are checked on every ARS allocation run and
published in V2Pulse under the **Allocation gap report** category of the report library. They also appear as the first
group on the Analytics page. Reference workbook: `gap-allocation.xlsb` (run 2026-10-15, session 20261015_175510_150).

## Data flow

1. `\\Mis\01-mis\ALLOCATION REPORT\ARS_RAW_DATA\data\<date>\<session>\` holds the run files the workbook macro pastes.
2. `load_ars.py` loads the last complete session of each day: `ARS_GRID_MJ_ALLRDC`, `MSA_OP_CL` and, when present,
   `usp_ars_alloc_batch_summary`. They go into `BRONZE.ALC_ARS_GRID_MJ`, `BRONZE.ALC_ARS_MSA_OP_CL` and
   `BRONZE.ALC_ARS_BATCH_SUMMARY`. Windows task `Allocation-Gap-Daily` on the laptop runs it at 07:30 and 13:30 IST.
3. `load_masters.py <workbook>` loads the hand-kept masters from the workbook's DATA and SCH-ST sheets into
   `BRONZE.ALC_GAP_MST_*`: budget display and gandola, allocation gandola, installed fixtures, store master, MJ status,
   size-applicable and schedule days. Re-run it when the team changes them.
4. `SILVER.SP_ALC_GAP_BUILD(session)` writes `SILVER.ALC_GAP_ROWS`, with one row per report and checked entity, both OK
   and GAP rows. It also writes `SILVER.ALC_GAP_DAILY` for the trend. 30 run days are kept, one session per day (the
   latest). DC GRT stock comes from the day before the run: `V_DC_BIN_STOCK_CLASS_SNAP` `GRT STK` and `ET_STOCK_DATA`
   SLOC 0032.
5. V2Pulse reads `PULSE.V_BI_ALC_GAP` and `PULSE.V_BI_ALC_GAP_TREND` (datasets `alc_gap`, `alc_gap_trend`, defined in
   `pulse/modules/alc_gap_catalog.py`).

Thresholds live in `SILVER.ALC_GAP_CONFIG`. They default to the format files (for example fill rate 80% / 120% and
GND ±10). Change a row there and the next build uses it. Logs: `BRONZE.ALC_GAP_LOAD_LOG`, `SILVER.ALC_GAP_BUILD_LOG`.

## Deploy

Files: `pulse/modules/alc_gap_catalog.py` (new), plus a few lines each in `hero_catalog.py` (extends the seed) and
`analytics.py` (Analytics page group). Originals are in `deploy\backup-alcgap-2026-10-16`. Snowflake objects are
already live. **Run `deploy\update.bat` over RDP** to seed the 2 datasets and 24 dashboards. Rollback: copy the
backup files back and run update.bat.

## Tie-out against the workbook (2026-10-15)

| Figure | Result |
|---|---|
| Store x MJ MBQ, ALC-Q, stock after, short, ALC display | exact (APP + GM; NT and MKT are outside the report) |
| Excess after allocation, and excess allocation at 110% | 7,577,766 and 26,202, exact |
| Company MJ option short, UN-PROC GRT | exact on 704 of 704 MJs |
| PROC GRT | 99.8%; the snapshot timing differs |
| Remaining MSA | the engine's closing MSA. It matches the workbook's own GEN-CLR sheet. The workbook's 03-CO-MJ column differs on 28 winter MJs |
| PROC-GRT vs CO-short, UN-PROC-GRT vs CO-short | the same 154 and 209 MJs as the workbook |

Where the workbook's own flag columns disagree with the format files, the format files are followed:

- Display 0 with allocation done: the workbook says OKAY on 126 rows that meet its rule.
- Fill-rate bands: the workbook uses 90% / 110%.
- Zero allocation: the workbook flags MJs that are short by 0 to 5 pieces.
