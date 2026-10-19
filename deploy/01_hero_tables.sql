-- Hero Articles module for V2Pulse: Snowflake layer.
-- SILVER holds the built tables (transformation lives here); PULSE holds the read-only views V2Pulse queries.
-- Run as ACCOUNTADMIN on COMPUTE_WH. Every statement is safe to re-run.
--
--   SILVER.HERO_DIM_GENART   generic article attributes (hierarchy, MRP, vendor, fabric, fit, age)
--   SILVER.HERO_SALE_DAILY   DT x store x generic article sale, with last-year (364-day shifted) twin columns
--   SILVER.HERO_POSITION     daily as-of snapshot per store x generic article: sales windows, stock at every
--                            stage of the pipeline, forecast, gap, ranks and gap flags (last 14 snapshots kept)
--   SILVER.SP_HERO_BUILD     builds all three; called by SILVER.TASK_HERO_DAILY at 09:30 and 13:30 IST

use role ACCOUNTADMIN;
use warehouse COMPUTE_WH;

create table if not exists V2RETAIL.SILVER.HERO_SALE_DAILY (
  DT              date          not null,
  ST_CD           varchar(10)   not null,
  GEN_ART_NUMBER  number(18,0)  not null,
  SEG             varchar(40),
  DIV             varchar(60),
  SUB_DIV         varchar(60),
  MAJ_CAT         varchar(80),
  SALE_V          number(18,2),
  SALE_Q          number(18,3),
  GM_V            number(18,2),
  DISC_V          number(18,2),
  LY_SALE_V       number(18,2),   -- same store x article, 364 days earlier (weekday aligned)
  LY_SALE_Q       number(18,3),
  LY_GM_V         number(18,2),
  BUILT_AT        timestamp_ntz
) cluster by (DT)
  comment = 'Hero Articles: daily store x generic-article sale with LY (DT-364) twin columns. Built by SILVER.SP_HERO_BUILD.';

create table if not exists V2RETAIL.SILVER.HERO_POSITION (
  AS_OF_DT        date          not null,   -- sale anchor: last complete sale day
  STOCK_DT        date,                     -- stock snapshot used (latest ET_STOCK_DATA day <= AS_OF_DT)
  ST_CD           varchar(10)   not null,
  GEN_ART_NUMBER  number(18,0)  not null,
  SEG             varchar(40),
  DIV             varchar(60),
  SUB_DIV         varchar(60),
  MAJ_CAT         varchar(80),
  -- ranks (company-wide, by sale value of the last 30 days)
  CO_RANK         number(10,0),
  MC_RANK         number(10,0),
  ST_RANK         number(10,0),
  HERO_TIER       varchar(30),
  MC_HERO         varchar(40),
  ST_TOP100       varchar(1),
  -- sales windows
  SALE_V_L7D      number(18,2),  SALE_Q_L7D   number(18,3),
  SALE_V_P7D      number(18,2),  SALE_Q_P7D   number(18,3),
  SALE_V_L30D     number(18,2),  SALE_Q_L30D  number(18,3),
  GM_V_L30D       number(18,2),  DISC_V_L30D  number(18,2),  MRP_V_L30D number(18,2),
  SALE_V_MTD      number(18,2),
  LY_SALE_V_L30D  number(18,2),  LY_SALE_Q_L30D number(18,3),
  -- stock along the pipeline (units)
  FLOOR_Q         number(18,3),   -- store SLoc 0001 (sales area)
  BACKROOM_Q      number(18,3),   -- store SLoc 0002 (store DC)
  PIPELINE_Q      number(18,3),   -- picked / ready to dispatch for this store at RDC or hub (PRD, PTL, HUB PRD)
  INTRANSIT_Q     number(18,3),   -- on the road to this store (hub in-transit HUs)
  RDC_Q           number(18,3),   -- unrestricted stock of the article at the store's own RDC (SLoc 0001)
  ALL_DC_Q        number(18,3),   -- unrestricted stock of the article across DH24, DW01, DK04
  -- demand and gap
  ROS_Q           number(18,4),   -- blended daily rate of sale: 0.6 x L7D/7 + 0.4 x L30D/30
  SEASON_FACTOR   number(10,3),   -- LY next-30-day vs LY last-30-day demand of the major category at this store
  FCST_30_Q       number(18,3),   -- ROS x 30 x season factor
  AVAIL_Q         number(18,3),   -- floor + backroom + pipeline + in transit
  GAP_Q           number(18,3),   -- forecast not covered by available stock (>= 0)
  ASP             number(18,2),
  GAP_V           number(18,2),
  LOST_7D_V       number(18,2),   -- out-of-stock stores only: 7 days of sale at the current rate
  EXCESS_Q        number(18,3),   -- stock above 90 days of forecast cover
  -- size health
  CORE_SIZES      number(6,0),    -- sizes that make >= 5% of the article's company sale (L30D)
  CORE_SIZES_STK  number(6,0),    -- of those, sizes with stock in this store
  -- gap flags (0/1, so they add up to store counts)
  SELLING_F       number(1,0),
  STOCKED_F       number(1,0),
  OOS_F           number(1,0),
  LOW_COVER_F     number(1,0),
  BROKEN_SIZE_F   number(1,0),
  NOT_RANGED_F    number(1,0),
  EXCESS_F        number(1,0),
  GAP_TYPE        varchar(40),
  ACTION          varchar(80),
  -- article drivers
  AGE_DAYS        number(10,0),
  FIRST_SALE_DT   date,
  BUILT_AT        timestamp_ntz
) cluster by (AS_OF_DT)
  comment = 'Hero Articles: daily as-of position per store x generic article (sales windows, stock pipeline, forecast, gap, ranks). Built by SILVER.SP_HERO_BUILD; 14 snapshots kept.';
