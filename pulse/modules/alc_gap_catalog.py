"""Allocation gap report: the 22 gap formats of the allocation team (Desktop\\New folder\\Allocation) checked on every
ARS allocation run, rebuilt from Snowflake instead of the gap-allocation workbook.

  alc_gap        one row per report x checked entity (store, store x MJ, MJ, or RDC x generic-colour) for each run day,
                 OK and GAP rows alike, with the format's columns (SILVER.ALC_GAP_ROWS). The newest run day is shown
                 unless a run date is picked.
  alc_gap_trend  gaps per run day x report x segment x division (SILVER.ALC_GAP_DAILY).

Built by SILVER.SP_ALC_GAP_BUILD, called by load_ars.py after it loads a session's run files from
\\\\Mis\\01-mis\\ALLOCATION REPORT\\ARS_RAW_DATA (the same files the workbook macro pastes). The masters the workbook keeps
by hand (budget display and gandola, installed fixtures, schedule days, MJ status) are loaded from the workbook by
load_masters.py. Thresholds are rows of SILVER.ALC_GAP_CONFIG, so a limit can change without a deploy.

Dashboards: one overview (the workbook's DASHBOARD sheet), one trend, and one per gap format with that format's columns,
all in the "Allocation gap report" category of the report library and on the Analytics page."""
from .bi_catalog import D, M, V

CATEGORY = "Allocation gap report"
AUDIENCE = ["EXEC", "MIS_CENTRAL", "PLATFORM", "DEPT_MIS_OWNER", "MERCH", "SOURCING", "WH_OPS", "ZONAL_HEAD",
            "REGIONAL_HEAD"]
# Company-level reports (major category, generic-colour) carry no store, so store-scoped roles would see nothing.
HO_AUDIENCE = [r for r in AUDIENCE if r not in ("ZONAL_HEAD", "REGIONAL_HEAD")]
_R, _S, _P, _G = "Report", "Store", "Merchandise", "Gap"

GAP_DATASET = dict(
    code="alc_gap", name="Allocation gap report",
    description="The allocation team's 22 gap checks on each ARS allocation run: store fill rate, gandola, schedule and "
                "display gaps, store x major-category allocation checks, company major-category MSA / GRT / hold gaps, "
                "and generic-colour MSA gaps. Every checked row is kept with its GAP or OK flag.",
    view="V_BI_ALC_GAP", sql="select * from V2RETAIL.SILVER.ALC_GAP_ROWS", snapshot_dim="run_date", scope="ST_CD",
    cache=3600, audience=AUDIENCE,
    refresh="After every ARS allocation run (loaded from the MIS share at 07:30 and 13:30 IST). Quantities are pieces. "
            "Budget display, gandola, installed fixtures and schedule days come from the allocation team's masters.",
    dimensions=[
        D("run_date", "Run date", "RUN_DATE", "date", "Time"),
        D("report_code", "Report code", "REPORT_CODE", group=_R), D("report", "Gap report", "REPORT_NAME", group=_R),
        D("level", "Level", "LEVEL", group=_R), D("subject", "Subject", "SUBJECT", group=_R),
        D("session", "ARS session", "SESSION_ID", group=_R),
        D("gap_status", "Gap / OK", "GAP_STATUS", group=_G), D("gap_flag", "Gap", "GAP_FLAG", group=_G),
        D("zone", "Zone", "ZONE_CODE", group=_S), D("region", "Region", "REGION_CODE", group=_S),
        D("rdc", "RDC", "RDC", group=_S), D("store", "ST-CD", "ST_CD", group=_S), D("store_name", "ST-NM", "ST_NM", group=_S),
        D("store_status", "ST-STAT", "ST_STAT", group=_S), D("store_op_date", "ST-OP-DT", "ST_OP_DT", "date", _S),
        D("segment", "SEG", "SEG", group=_P), D("division", "DIV", "DIV", group=_P),
        D("sub_division", "SUB-DIV", "SUB_DIV", group=_P), D("major_category", "MJ", "MAJ_CAT", group=_P),
        D("season", "SSN", "SSN", group=_P), D("mj_status", "MJ-STS", "MJ_STS", group=_P),
        D("size_applicable", "SZ-APP", "SZ_APP", group=_P), D("generic_article", "GEN", "GEN_ART", group=_P),
        D("colour", "CLR", "CLR", group=_P),
    ],
    measures=[
        M("checked", "Rows checked", "REPORT_CODE", agg="count"), M("gaps", "Gaps", "IS_GAP"),
        M("gap_pct", "Gap %", agg="ratio", num="gaps", den="checked", fmt="pct"),
        M("gap_qty", "Gap qty", "GAP_QTY"),
        M("gap_stores", "Stores with a gap", "GAP_ST_CD", agg="count_distinct"),
        M("gap_mjs", "Major categories with a gap", "GAP_MAJ_CAT", agg="count_distinct"),
        M("schld", "SCHLD ST", "SCHLD"),
        M("bgt_disp", "BGT DISP", "BGT_DISP"), M("alc_disp", "ALC DISP", "ALC_DISP"), M("diff_q", "DIFF", "DIFF_Q"),
        M("bgt_alc_pct", "ALC vs BGT DISP %", agg="growth", num="alc_disp", den="bgt_disp", fmt="pct"),
        M("installed_gnd", "INSTALLED GND", "INSTALLED_GND"), M("alc_gnd", "ALC GND", "ALC_GND"),
        M("bgt_gnd", "BGT GND", "BGT_GND"),
        M("acc_dens", "ACC DENSITY", "ACC_DENS", agg="max"), M("pak_sz", "PAK SZ", "PAK_SZ", agg="max"),
        M("mbq", "MBQ", "MBQ"), M("before_stk", "BEFORE STK", "BEFORE_STK"), M("alc_q", "ALC-Q", "ALC_Q"),
        M("after_stk", "AFTER STK", "AFTER_STK"),
        M("fr_before", "FR% BEFORE", agg="ratio", num="before_stk", den="mbq", fmt="pct"),
        M("fr_after", "FR% AFTER", agg="ratio", num="after_stk", den="mbq", fmt="pct"),
        M("short_aft", "SHORT AFT ALC", "SHORT_AFT"), M("excess_aft", "EXCESS AFT ALC", "EXCESS_AFT"),
        M("excess_alc", "EXCESS ALC-Q (110%)", "EXCESS_ALC"), M("opt_short", "OPT SHORT AFT ALC", "OPT_SHORT"),
        M("rem_msa", "REM MSA", "REM_MSA"), M("rem_msa_opt", "REM MSA OPT COUNT", "REM_MSA_OPT"),
        M("hold_msa", "REM HOLD MSA", "HOLD_MSA"), M("ttl_msa", "MSA + HOLD", "TTL_MSA"),
        M("hold_contb", "HOLD VS TTL-MSA CONTB%", agg="ratio", num="hold_msa", den="ttl_msa", fmt="pct"),
        M("proc_grt", "PROC GRT STK", "PROC_GRT"), M("proc_grt_opt", "PROC GRT OPT COUNT", "PROC_GRT_OPT"),
        M("unproc_grt", "UN-PROC GRT STK (0032)", "UNPROC_GRT"),
        M("unproc_grt_opt", "UN-PROC GRT OPT COUNT", "UNPROC_GRT_OPT"),
    ],
)

TREND_DATASET = dict(
    code="alc_gap_trend", name="Allocation gap report - trend",
    description="Gaps found on each ARS allocation run day, by gap report, segment and division.",
    view="V_BI_ALC_GAP_TREND", sql="select * from V2RETAIL.SILVER.ALC_GAP_DAILY", snapshot_dim=None, scope=None,
    cache=3600, audience=AUDIENCE, refresh="After every ARS allocation run; 30 run days are kept.",
    dimensions=[D("month", "Month", "RUN_DATE", "date", "Time", "month"), D("week", "Week", "RUN_DATE", "date", "Time", "week"),
                D("run_date", "Run date", "RUN_DATE", "date", "Time"),
                D("report_code", "Report code", "REPORT_CODE", group=_R), D("report", "Gap report", "REPORT_NAME", group=_R),
                D("level", "Level", "LEVEL", group=_R), D("subject", "Subject", "SUBJECT", group=_R),
                D("segment", "SEG", "SEG", group=_P), D("division", "DIV", "DIV", group=_P)],
    measures=[M("checked", "Rows checked", "CHECKED"), M("gaps", "Gaps", "GAPS"),
              M("gap_pct", "Gap %", agg="ratio", num="gaps", den="checked", fmt="pct"),
              M("gap_qty", "Gap qty", "GAP_QTY"), M("gap_stores", "Stores with a gap (sum over reports)", "GAP_STORES"),
              M("run_days", "Run days", "RUN_DATE", agg="count_distinct")],
)

DATASETS = [GAP_DATASET, TREND_DATASET]

# ------------------------------------------------------------------------------------------------ the 22 formats
_ST = ["store", "store_status", "store_op_date", "store_name"]
_SM = ["store", "store_status", "store_name", "segment", "division", "sub_division", "major_category", "season"]
_CM = ["segment", "division", "sub_division", "major_category", "season"]
_GC = ["segment", "division", "sub_division", "major_category", "generic_article", "colour", "season", "rdc"]
_CO_BASE = ["mbq", "alc_q", "short_aft", "opt_short", "rem_msa", "rem_msa_opt"]

# code, title, level, table dims, table measures, rule shown on the dashboard
FORMATS = [
    ("ST01", "ST-ONE-LINER FILLRATE GAP", "ST", _ST, ["mbq", "after_stk", "fr_after", "short_aft", "excess_aft"],
     "Store fill rate after allocation (total stock / MBQ, APP + GM) above 120% or below 80%."),
    ("ST02", "INSTALLED VS ALC GND", "ST", _ST, ["installed_gnd", "alc_gnd", "diff_q"],
     "Installed gandola minus allocation gandola beyond +/- 10, or either of them not given."),
    ("ST03", "ZERO ALC IN SCHEDULE ST", "ST", _ST + ["rdc"],
     ["schld", "before_stk", "alc_q", "after_stk", "short_aft", "excess_aft"],
     "Store scheduled for the run's weekday (SCH-ST master, RUNNING stores) that got zero allocation."),
    ("ST04", "DISP GIVEN BUT ALC NOT DONE", "ST", _ST, ["bgt_disp", "alc_q", "mbq", "short_aft"],
     "Store has budget display but got zero allocation."),
    ("SM01", "ST-MJ DISPLAY NOT GIVEN STILL ALC DONE", "ST-MJ", _SM, ["bgt_disp", "alc_q"],
     "Budget display of the store x major category is zero, yet allocation was done."),
    ("SM02", "DISP VS ACC-DENSITY", "ST-MJ", _SM, ["acc_dens", "bgt_disp", "diff_q"],
     "Budget display is below the accessory density (DIFF = BGT DISP - ACC DENSITY < 0)."),
    ("SM03", "EXCESS ALC @ ST-MJ LEVEL", "ST-MJ", _SM,
     ["mbq", "before_stk", "fr_before", "alc_q", "after_stk", "fr_after", "short_aft", "excess_aft", "rem_msa"],
     "Fill rate before the run was above 110%, yet allocation was done."),
    ("SM04", "MSA AVAILABLE VS STORE SHORT", "ST-MJ", _SM, ["mbq", "after_stk", "short_aft", "rem_msa"],
     "Store x major category is short after allocation while MSA of the major category remains at the RDCs."),
    ("SM05", "ST-MJ FILLRATE GAP", "ST-MJ", _SM, ["mbq", "after_stk", "fr_after", "short_aft", "excess_aft"],
     "Fill rate after allocation above 120% or below 80%. Rows with stock but zero MBQ are flagged NO MBQ, not GAP."),
    ("CM01", "ZERO ALLOCATION GAP (MAJ-CAT)", "CO-MJ", _CM, _CO_BASE,
     "Major category short across stores with MSA remaining, and zero allocation."),
    ("CM02", "ZERO OR LOW MSA GAP", "CO-MJ", _CM, _CO_BASE, "Short above 3,000 with remaining MSA below 3,000."),
    ("CM03", "MSA STK AVAILABLE VS NO ALC (CO-MJ)", "CO-MJ", _CM, _CO_BASE,
     "Short above 100, remaining MSA above 1,000, and zero allocation."),
    ("CM04", "GRT STOCK AVAILABLE BUT ST-SHORT (CO-MJ)", "CO-MJ", _CM, _CO_BASE + ["proc_grt", "proc_grt_opt"],
     "Short above 100, no MSA left, but processed GRT stock is available."),
    ("CM05", "PROC-GRT VS CO-SHORT", "CO-MJ", _CM, ["mbq", "alc_q", "short_aft", "opt_short", "proc_grt", "proc_grt_opt"],
     "Short above 1,000 and processed GRT stock above 1,000: prioritise these major-category bins in GRT."),
    ("CM06", "UN-PROC-GRT VS CO-SHORT", "CO-MJ", _CM,
     ["mbq", "alc_q", "short_aft", "opt_short", "unproc_grt", "unproc_grt_opt"],
     "Short above 1,000 and un-processed GRT stock (0032) above 1,000: prioritise these major-category bins in GRT."),
    ("CM07", "HOLD GAP", "CO-MJ", _CM, ["mbq", "short_aft", "opt_short", "rem_msa", "hold_msa", "hold_contb"],
     "Short while hold is more than 30% of remaining MSA + hold."),
    ("CM08", "HOLD MSA GAP", "CO-MJ", _CM, ["mbq", "short_aft", "opt_short", "rem_msa", "hold_msa"],
     "GM with any hold (GM-HOLD-GAP); others: hold present, no free MSA, and short."),
    ("CM09", "MSA AVAILABLE BUT DISP NOT GIVEN", "CO-MJ", _CM, ["bgt_disp", "rem_msa"],
     "No budget display for the major category while remaining MSA is above 1,000."),
    ("CM10", "BGT-DISP VS ALC-DISP", "CO-MJ", _CM, ["bgt_disp", "alc_disp", "diff_q", "bgt_alc_pct"],
     "Allocation display minus budget display above 3,000 or below -1,000, or more than 5% either way."),
    ("CM11", "PACK-SIZE GAP", "CO-MJ", _CM + ["mj_status", "size_applicable"], ["acc_dens", "pak_sz", "rem_msa"],
     "GM, non-SSNL, active major category with MSA above 100 whose half accessory density is below the pack size."),
    ("GC01", "HIGH MSA GAP", "GEN-CLR", _GC, ["rem_msa"],
     "Remaining MSA of a generic-colour at an RDC above 5,000 (GM) or 3,000 (others)."),
    ("GC02", "LOW MSA-Q, ALC NOT DONE (MJ-RDC)", "GEN-CLR", _GC, _CO_BASE,
     "Generic-colour with under 50 pcs of MSA left unallocated while its major category is short at that RDC: "
     "manual push. MBQ, short and option short are the major category's at the RDC."),
]


def _breakdown(level):
    """Summary-page visuals that make sense for the level."""
    if level == "ST":
        return [("zone", "Gaps by zone"), ("rdc", "Gaps by RDC"), ("store_status", "Gaps by store status")]
    if level == "ST-MJ":
        return [("division", "Gaps by division"), ("zone", "Gaps by zone"), ("rdc", "Gaps by RDC")]
    if level == "GEN-CLR":
        return [("division", "Gaps by division"), ("rdc", "Gaps by RDC"), ("segment", "Gaps by segment")]
    return [("division", "Gaps by division"), ("sub_division", "Gaps by sub-division"), ("segment", "Gaps by segment")]


def _slicers(level):
    s = [{"dim": "gap_status"}, {"dim": "gap_flag"}]
    if level in ("ST", "ST-MJ"):
        s += [{"dim": "zone"}, {"dim": "rdc"}, {"dim": "store_status"}]
    if level in ("ST-MJ", "CO-MJ", "GEN-CLR"):
        s += [{"dim": "segment"}, {"dim": "division"}, {"dim": "major_category"}]
    if level == "GEN-CLR":
        s += [{"dim": "rdc"}]
    return s + [{"dim": "run_date"}]


def _format_report(code, title, level, dims, measures, rule):
    b1, b2, b3 = _breakdown(level)
    return dict(
        name="Allocation gap · %s %s" % (code, title), category=CATEGORY, dataset="alc_gap",
        audience=AUDIENCE if level in ("ST", "ST-MJ") else HO_AUDIENCE,
        description="%s (%s level) %s" % (title, level, rule),
        definition={"version": 1, "filters": [{"dim": "report_code", "op": "in", "values": [code]}], "pages": [
            {"id": "p1", "name": "Report", "slicers": _slicers(level),
             "visuals": [
                 V("k1", "kpi", "Gaps", 0, 0, 3, 2, measures=["gaps"]),
                 V("k2", "kpi", "Rows checked", 3, 0, 3, 2, measures=["checked"]),
                 V("k3", "kpi", "Gap %", 6, 0, 3, 2, measures=["gap_pct"]),
                 V("k4", "kpi", "Gap qty", 9, 0, 3, 2, measures=["gap_qty"]),
                 V("t1", "table", "%s - biggest gaps first (pick GAP above for gaps only). Rule: %s" % (title, rule),
                   0, 2, 12, 16, dims=dims + ["gap_flag"], measures=["gap_qty"] + measures,
                   sort={"by": "gap_qty", "dir": "desc"}, limit=2000),
             ]},
            {"id": "p2", "name": "Summary", "slicers": _slicers(level),
             "visuals": [
                 V("d1", "donut", "Rows by flag", 0, 0, 4, 6, dims=["gap_flag"], measures=["checked"],
                   sort={"by": "checked", "dir": "desc"}),
                 V("b1", "bar", b1[1], 4, 0, 8, 6, dims=[b1[0]], measures=["gaps"],
                   sort={"by": "gaps", "dir": "desc"}, limit=30),
                 V("b2", "column", b2[1], 0, 6, 6, 6, dims=[b2[0]], measures=["gaps"],
                   sort={"by": "gaps", "dir": "desc"}, limit=30),
                 V("b3", "column", b3[1], 6, 6, 6, 6, dims=[b3[0]], measures=["gaps"],
                   sort={"by": "gaps", "dir": "desc"}, limit=30),
                 V("t1", "table", "Gaps by %s" % b1[1].split(" by ")[-1], 0, 12, 12, 7, dims=[b1[0]],
                   measures=["checked", "gaps", "gap_pct", "gap_qty"] + measures[:4],
                   sort={"by": "gaps", "dir": "desc"}, limit=200),
             ]},
        ]},
    )


_DASH_TABLE = ["checked", "gaps", "gap_pct", "gap_qty", "gap_stores", "gap_mjs"]

OVERVIEW = dict(
    name="Allocation gap · Dashboard", category=CATEGORY, dataset="alc_gap", audience=AUDIENCE,
    description="All 22 allocation gap checks on the latest ARS run in one place: how many gaps each report found, at "
                "which level, and where they sit by store, RDC, division and major category.",
    definition={"version": 1, "filters": [], "pages": [
        {"id": "p1", "name": "Dashboard", "slicers": [{"dim": "level"}, {"dim": "subject"}, {"dim": "run_date"}],
         "visuals": [
             V("k1", "kpi", "Gaps (all reports)", 0, 0, 3, 2, measures=["gaps"]),
             V("k2", "kpi", "Gap qty", 3, 0, 3, 2, measures=["gap_qty"]),
             V("k3", "kpi", "Stores with a gap", 6, 0, 3, 2, measures=["gap_stores"]),
             V("k4", "kpi", "Major categories with a gap", 9, 0, 3, 2, measures=["gap_mjs"]),
             V("t1", "table", "Gap count per report (open the report's own dashboard for its rows)", 0, 2, 12, 11,
               dims=["report_code", "level", "subject", "report"], measures=_DASH_TABLE,
               sort={"by": "report_code", "dir": "asc"}, limit=50),
             V("c1", "bar", "Gaps by report", 0, 13, 12, 8, dims=["report"], measures=["gaps"],
               sort={"by": "gaps", "dir": "desc"}, limit=25),
         ]},
        {"id": "p2", "name": "Stores", "slicers": [{"dim": "zone"}, {"dim": "rdc"}, {"dim": "store_status"},
                                                   {"dim": "report"}, {"dim": "run_date"}],
         "visuals": [
             V("m1", "matrix", "Gaps: store by report (store and store x MJ reports)", 0, 0, 12, 10,
               dims=["store", "report_code"], measures=["gaps"]),
             V("t1", "table", "Stores with the most gaps", 0, 10, 12, 9,
               dims=["store", "store_name", "store_status", "zone", "rdc"], measures=["gaps", "gap_qty"],
               sort={"by": "gaps", "dir": "desc"}, limit=600),
         ]},
        {"id": "p3", "name": "Major categories", "slicers": [{"dim": "segment"}, {"dim": "division"},
                                                             {"dim": "report"}, {"dim": "run_date"}],
         "visuals": [
             V("b1", "bar", "Gaps by division", 0, 0, 6, 7, dims=["division"], measures=["gaps"],
               sort={"by": "gaps", "dir": "desc"}, limit=20),
             V("b2", "bar", "Gaps by sub-division", 6, 0, 6, 7, dims=["sub_division"], measures=["gaps"],
               sort={"by": "gaps", "dir": "desc"}, limit=30),
             V("t1", "table", "Major categories with the most gaps", 0, 7, 12, 12,
               dims=["segment", "division", "sub_division", "major_category"], measures=["gaps", "gap_qty", "gap_stores"],
               sort={"by": "gaps", "dir": "desc"}, limit=1000),
         ]},
        {"id": "p4", "name": "All gap rows", "slicers": [{"dim": "gap_status"}, {"dim": "report"},
                                                         {"dim": "rdc"}, {"dim": "division"}, {"dim": "run_date"}],
         "visuals": [
             V("t1", "table", "Every row checked, biggest gap first (pick GAP above for gaps only)", 0, 0, 12, 18,
               dims=["report_code", "gap_flag", "rdc", "store", "store_name", "division", "major_category",
                     "generic_article", "colour"],
               measures=["gap_qty", "mbq", "alc_q", "short_aft", "rem_msa", "bgt_disp", "alc_disp"],
               sort={"by": "gap_qty", "dir": "desc"}, limit=2000),
         ]},
    ]},
)

TREND = dict(
    name="Allocation gap · Trend", category=CATEGORY, dataset="alc_gap_trend", audience=AUDIENCE,
    description="How many gaps each allocation run left, day by day, by report, segment and division.",
    definition={"version": 1, "filters": [], "pages": [
        {"id": "p1", "name": "Trend", "slicers": [{"dim": "run_date", "mode": "range", "preset": "mtd"}, {"dim": "level"},
                                                  {"dim": "report"}, {"dim": "segment"}, {"dim": "division"}],
         "visuals": [
             V("l1", "line", "Gaps per run day", 0, 0, 12, 6, dims=["run_date"], measures=["gaps"],
               sort={"by": "run_date", "dir": "asc"}),
             V("l2", "line", "Gap qty per run day", 0, 6, 12, 6, dims=["run_date"], measures=["gap_qty"],
               sort={"by": "run_date", "dir": "asc"}),
             V("m1", "matrix", "Gaps: report by run day", 0, 12, 12, 9, dims=["report_code", "run_date"],
               measures=["gaps"]),
         ]},
    ]},
)

REPORTS = [OVERVIEW, TREND] + [_format_report(*f) for f in FORMATS]
