# Analytics module (V2Pulse) — Hero Articles, added 2026-10-16

**Update (same day, second deploy):** the sidebar tab is **Analytics** (permission `analytics.view`, replacing
`hero.view`). It opens the dashboard **Analytics** (dataset `analytics`, table `SILVER.HERO_ANALYTICS`), whose 12
page tabs are the navigation: Overview · Top 100 · Locations · Products · Period top 100 · Trend · Time x merchandise
· Store actions · Store heroes · Replenishment · RDC & vendor · Drivers. The five focused dashboards stay, filed under
the category Analytics. Folder icons in Reports / Masters are Microsoft File Explorer yellow via
`pulse/theme/overrides.css`, which `app.py` injects into every page (kept outside `static/`, so a web-app rebuild
keeps it). Second backup: `deployackup-analytics-2026-10-16\`. Run `update.bat` again to activate.


Top-selling generic articles across locations, the merchandise hierarchy and time, the stock behind them,
the gaps, and the signals that move next month's sale. Built on the existing BI engine (no SPA rebuild needed).

## Activate
Run `deploy\update.bat` as Administrator on the server (restarts V2Pulse + worker; the seed adds the two
datasets, five dashboards and the `hero.view` permission). The sidebar then shows **Libraries › Hero Articles**,
now **Analytics**, which opens `/reports/dynamic/c5c0576d-7410-5542-bab8-3735e67bd63b`.

If an admin has customised a system role in Admin › Roles, that role keeps its own permission list: tick
`hero.view` for it there.

## What changed in code
| File | Change |
|---|---|
| `pulse/modules/hero_catalog.py` | **new**: datasets `hero_position`, `hero_sales`; 5 dashboards with fixed ids; `seed()` |
| `pulse/iam/roles.py` | `hero.view` permission, "Hero Articles" module, granted to the 13 roles below |
| `pulse/modules/home.py` | sidebar entry (needs `hero.view`) |
| `pulse/seed.py` | `hero_catalog.seed(cur)` before `bi.seed(cur)` |
| `pulse/modules/bi.py` | pre-warm skips `hero_sales` (like `dfs_sale`: browser queries carry a date preset) |
| `pulse/modules/bi_catalog.py` | `bi-sql` generator includes the hero views |

Rollback: copy the files in `deploy\backup-hero-2026-10-16\` back (`modules_bi.py` → `pulse\modules\bi.py`, etc.),
delete `pulse\modules\hero_catalog.py`, run `update.bat`.

## Dashboards (library category "Hero Articles")
| Dashboard | Audience (roles) | Report filter |
|---|---|---|
| Top 100 command centre (Overview, Top 100 list, Gaps by location, Gaps by product, What drives next month) | all 13 | company top 100 |
| Explorer (top 100 of any slice, category heroes, store heroes) | all 13 | merchandise only |
| Store action list (actions, by store, by action) | EXEC, MIS_CENTRAL, PLATFORM, ZONAL/REGIONAL_HEAD, AREA_MGR, STORE_MGR | actionable gaps |
| Replenishment & buying (DC backing, by RDC, by vendor, excess to re-deploy) | EXEC, MIS_CENTRAL, PLATFORM, DEPT_MIS_OWNER, MERCH, SOURCING, WH_OPS | top 500 |
| Sales over time (top 100 of any period, trend, time x merchandise, locations) | all 13 | merchandise only |

All 13 = EXEC, MIS_CENTRAL, PLATFORM, DEPT_MIS_OWNER, HO_STAFF, MERCH, SOURCING, FINANCE, ZONAL_HEAD,
REGIONAL_HEAD, AREA_MGR, STORE_MGR, WH_OPS. Store scope (ST_CD) is applied by the engine.

## Snowflake (run once, already applied 2026-10-16 as ACCOUNTADMIN)
`01_hero_tables.sql` → `02_hero_build_proc.sql` → `03_hero_views_task.sql` (task) → `04_hero_views_from_catalog.sql` (views).
- `SILVER.SP_HERO_BUILD(full)`, task `SILVER.TASK_HERO_DAILY` 09:30 and 13:30 IST on COMPUTE_WH (~2 min).
- `SILVER.HERO_SALE_DAILY` (157M rows, day x store x generic article, LY = same key 364 days earlier),
  `SILVER.HERO_POSITION` (~6.2M rows per day, 14 days kept), `HERO_DIM_GENART`, `HERO_TIER_CURRENT`,
  `HERO_SALE_SOURCE_DAY`. Views `PULSE.V_BI_HERO_POSITION`, `PULSE.V_BI_HERO_SALES`.

## Definitions
- **Hero tier**: company rank by sale value of the last 30 days. A = top 100, B = 101-500, C = 501-2000.
  Gift / marketing, non-trading, trims & accessories, fabric and scrap are never ranked (tier X).
- **Rate of sale** = 0.6 × L7D/7 + 0.4 × L30D/30. **Season factor** = last year's next-30-day ÷ last-30-day qty
  of the major category at the store (company fallback, clamped 0.5-3). **Forecast 30d** = ROS × 30 × factor.
- **Available** = floor (SLoc 0001) + backroom (0002) + picked for the store (DH24/DW01 PRD, hub PRD, PTL)
  + on the road (hub in-transit HUs). **Gap** = forecast − available, floored at 0.
- Gap types, in priority: out of stock (sold in 30d, no store stock) › hero not ranged (top 500, store has
  nothing) › low cover (<7 days) › broken sizes (<60% of core sizes) › excess (>90 days) › healthy.
- DC stock = unrestricted SLoc 0001 at DH24 / DW01 / DK04, shown per article (max), never summed over articles.

## Known limits
- Day-wise sale feed is near-empty Apr-Jun 2025 and Oct 2025-Jan 2026; those days come from
  `GOLD.FACT_STORE_STOCK_SALE_DAILY` (no GM / discount, ~4% above the MIS feed, 0.3% unmapped articles).
  LY growth for Oct-Jan therefore reads ~4% low. No LY before 1 Apr 2025.
- Position-dataset LY is like-for-like per article; use Sales over time for LY totals.
- Stock only covers articles that sold somewhere in the last 30 days (dead stock is out of scope).
- WH_OPS users are scoped to their own site code, so they see nothing until given a company or region scope grant.

## Library tree names (third deploy, 2026-10-16)
Tree names (Central and each department tree, in Reports and in Masters) are data in `nav.tree_name`
(migration `0006_tree_names.sql`, which starts both Central trees as "Central"). Rename them at
**http://192.168.151.27:8875/manage/trees** (needs the tree's edit permission: `reports.tree.central` /
`.tree.dept` / `.tree.all`, and the same for masters), or `PUT /api/v1/<reports|masters>/trees/<tree>`
with `{"name": ..., "name_hi": ...}`; an empty name resets to the default. Names must be unique per library;
every rename is in the action log (`reports.tree.rename`). Files: `pulse/modules/reports.py`,
`pulse/modules/workspace.py`, `pulse/app.py`, `pulse/manage/trees.{html,js}`. Backup: `deploy\backup-treenames-2026-10-16\`.

## Analytics landing page + Karma (fourth deploy, 2026-10-16)
- Sidebar **Analytics** now opens **/analytics**, a landing page (`pulse/analytics/`, API `GET /api/v1/analytics/catalog`
  in `pulse/modules/analytics.py`). It has headline KPIs and a menu of every Analytics dashboard and its pages. Each user
  sees only the dashboards their role may open. The 12-page hero dashboard is now "Hero articles · All pages" (same id).
- Blank sidebar label fixed by `pulse/theme/overrides.js` (injected by `app.py`). The web app has no translation for
  new tabs; the script fills the label from `/api/v1/me` and loads `/analytics` and `/manage/*` as full pages.
- **Karma** (`pulse/modules/karma_catalog.py`): datasets `karma_emp` (SILVER.KARMA_EMP_SNAP, daily history of
  KARMA.EMP_REPORT from 15 Oct 2026) and `karma_day` (SILVER.KARMA_EMP_DAY, employee x day since Dec 2025 from
  YES_NO_REPORT + TASKS_REPORT). Dashboards: "Karma · HO productivity" (Overview, Department summary, Employee report,
  Tasks, Maker-checker, Exceptions) and "Karma · Trends". Both are HO-only, for EXEC, MIS_CENTRAL, PLATFORM, HR and
  DEPT_MIS_OWNER. Snowflake: `deploy\10_karma_build.sql`, task SILVER.TASK_KARMA_DAILY at 09:45 IST.
- HR gets `analytics.view` (Karma only; hero dashboards are not in HR's audience).
- Backup: `deploy\backup-analytics-home-2026-10-16\`. Activate with `update.bat`.

## AI suggestions (fifth deploy, 2026-10-16)
- Snowflake `deploy\20_ai_suggestions.sql` (+ `21_ai_view_from_catalog.sql` for the view): `SILVER.SP_AI_SUGGESTIONS(true)`
  runs as task `SILVER.TASK_AI_SUGGESTIONS` right AFTER `TASK_HERO_DAILY` (09:30 / 13:30 IST). Rules write
  `SILVER.AI_SUGGESTION` (~39k rows/day: buy, send, move, store issues, range fix, DC placement); then Claude
  (`claude-sonnet-4-5` on Snowflake Cortex, cross-region) writes `SILVER.AI_BRIEF` (company, 6 zones, 4 owner teams = 11 calls).
- V2Pulse: `pulse/modules/ai_catalog.py`, dataset `ai_suggestions` (store-scoped; company and DC rows need company / DC
  scope), dashboard **AI suggestions** (Claude's brief, What to buy, What to send, Move between stores, What is wrong in
  stores, Range fix, DC placement). The Analytics landing page shows it first, with Claude's brief and AI KPIs.
- Backup: `deploy\backup-ai-2026-10-16\`. Activate with `update.bat`.
