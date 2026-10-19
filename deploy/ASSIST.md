# Ask me a question (Claude panel)

The "Ask me a question" button is on every V2Pulse page except sign-in, Admin, My activity and the report designer.
It opens a side panel where the user can ask Claude about the report that is open. Claude can:

- explain what the report shows;
- find gaps, outliers and top or bottom items;
- run its own calculations over the report's data;
- suggest actions;
- offer follow-up questions.

Added 2026-10-19.

## What Claude reads

| Page | Data |
|---|---|
| `/reports/view/<code>` (table reports) | Every row the report's query returns, with the filters the user applied. The rows come through the data gateway, so they are usually a cache hit. Claude gets a column profile and the first 25 rows, and can group, filter or sort all rows in Python. |
| `/reports/dynamic/<id>` (dashboards) | The definition, the fields and measures, and the results of the visuals' last queries, including the user's slicers. Claude can run more `bi_query`s through the normal BI engine. |
| Any other page (MIS, Analytics, Simulation, Control Tower, Stores…) | The text on the screen. |

Everything runs with the user's own permissions and store scope. Claude never writes SQL. Its requests are a small
JSON spec that is checked before anything runs.

**Model:** `claude-sonnet-4-5` through Snowflake Cortex (`SNOWFLAKE.CORTEX.COMPLETE`), as `PULSE_SVC`. `CORTEX_USER` is
granted to PUBLIC.

**Limits:**
- 60 questions per user per hour (rate bucket `assist`);
- up to 4 calculation rounds per question;
- typically 15–25 s per answer.

**Logging:** every question is logged as `assist.ask` in `audit.event` with the question, page, step count, tokens and time. The
conversation itself stays in the browser tab (sessionStorage).

## Files

- `pulse/modules/assist.py`: `GET /api/v1/assist/status` and `POST /api/v1/assist/ask`.
- `pulse/theme/assist.js` and `assist.css`: the panel. It records the page's own `/reports/<code>/data` and `/bi/query`
  calls so that Claude sees the same filters as the user.
- `pulse/app.py`:
  - the routes `/theme/assist.js` and `/theme/assist.css`;
  - injects the panel into the web app (`spa`) and into the Analytics, Simulation and MIS pages (`with_assist`);
  - includes the `assist` router.

## Go live

Run `deploy\update.bat` (restart). `assist.js` and `assist.css` changes after that need no restart, because they are
served per request.

## Roll back

Copy `deploy\backup-assist-2026-10-19\app.py` over `pulse\app.py` and run `update.bat`. `assist.py` and the
theme files can stay, because nothing loads them without the `app.py` changes.
