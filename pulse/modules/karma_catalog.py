"""Karma productivity (Analytics module): the HO Y/N checklist and task KPIs of the NEW-KARMA REVIEW report, rebuilt
from Snowflake (the Excel figures are not used).

  karma_emp  one row per employee per report day (SILVER.KARMA_EMP_SNAP, from KARMA.EMP_REPORT). The day reported
             is yesterday (T-1D): an 'L' is on leave, hours above zero is filled. Windows (1D / 7D / 30D) come from Karma.
  karma_day  employee x day since Dec 2025 (SILVER.KARMA_EMP_DAY): checklist hours, lines, maker-checker qty, and
             tasks raised (assigned by), received and completed (assigned to). Adds up over any dates picked.

Both are built by SILVER.SP_KARMA_BUILD (task SILVER.TASK_KARMA_DAILY, 09:45 IST). Neither is store-scoped:
people data is limited by audience instead (management, MIS, HR, data platform, department MIS owners)."""
from .bi_catalog import D, M, V

CATEGORY = "Analytics"
AUDIENCE = ["EXEC", "MIS_CENTRAL", "PLATFORM", "HR", "DEPT_MIS_OWNER"]
HO_ONLY = [{"dim": "location_group", "op": "in", "values": ["HO"]}]

_T, _O, _P, _S = "Time", "Organisation", "Person", "Status"


def _org_dims():
    return [D("location_group", "Location group", "LOC_GROUP", group=_O), D("location", "Location", "LOC", group=_O),
            D("dept", "Department", "DEPT", group=_O), D("sub_dept_1", "Sub-department 1", "SUB_DEPT_1", group=_O),
            D("sub_dept_2", "Sub-department 2", "SUB_DEPT_2", group=_O), D("sub_dept_3", "Sub-department 3", "SUB_DEPT_3", group=_O),
            D("designation", "Designation", "DESIGNATION", group=_P), D("manager", "Reporting manager", "MANAGER", group=_P),
            D("employee", "Employee", "EMP_NAME", group=_P), D("ecode", "E-code", "E_CODE", group=_P)]


EMP_DATASET = dict(
    code="karma_emp", name="Karma - employee productivity",
    description="Every employee in Karma for each report day: yesterday's Y/N checklist hours and fill status, leave, "
                "average hours over 7 and 30 days, maker-checker quantity against budget, pending and overaged tasks, and "
                "tasks raised, completed and delegated over 1, 7 and 30 days.",
    view="V_BI_KARMA_EMP", sql="select * from V2RETAIL.SILVER.KARMA_EMP_SNAP", snapshot_dim="report_date", scope=None,
    cache=3600, audience=AUDIENCE,
    refresh="Daily 09:45 IST from the Karma app's EMP_REPORT (refreshed ~04:00 IST). History is kept from 15 Oct 2026.",
    dimensions=[D("report_date", "Report date", "REPORT_DATE", "date", _T)] + _org_dims() + [
        D("status_yd", "Yesterday's status", "STATUS_YD", group=_S), D("status_td", "Today's status", "STATUS_TD", group=_S),
        D("attendance_yd", "Attendance yesterday", "ATTENDANCE_YD", group=_S),
        D("attendance_td", "Attendance today", "ATTENDANCE_TD", group=_S),
        D("hours_band", "Hours band (yesterday)", "HOURS_BAND", group=_S), D("task_band", "Tasks raised (yesterday)", "TASK_BAND", group=_S),
        D("pending_band", "Overaged tasks", "PENDING_BAND", group=_S), D("projects", "Projects", "PROJECT_COUNT", "number", _P)],
    measures=[
        M("employees", "Employees", "EMP_F"), M("filled", "Filled", "FILLED_F"), M("not_filled", "Not filled", "NOT_FILLED_F"),
        M("on_leave", "On leave", "LEAVE_F"), M("active", "Present (not on leave)", "ACTIVE_F"),
        M("upto5", "Filled up to 5 h", "UPTO5_F"), M("over5", "Filled over 5 h", "OVER5_F"),
        M("no_task", "Present, no task raised", "NO_TASK_F"), M("raised_yd", "Tasks raised yesterday (present)", "RAISED_YD"),
        M("fill_rate", "Fill rate % (of present)", agg="ratio", num="filled", den="active", fmt="pct"),
        M("fill_rate_total", "Fill rate % (of all)", agg="ratio", num="filled", den="employees", fmt="pct"),
        M("hours_yd", "Hours yesterday", "HOURS_YD_FILLED"), M("hours_td", "Hours today", "HOURS_TD"),
        M("avg_hours", "Average hours (filled)", agg="ratio", num="hours_yd", den="filled"),
        M("avg_7d", "Average hours 7D", "AVG_HOURS_7D", agg="avg"), M("avg_30d", "Average hours 30D", "AVG_HOURS_30D", agg="avg"),
        M("eff_days_7d", "Working days 7D", "EFF_DAYS_7D"), M("fill_days_7d", "Days filled 7D", "FILL_DAYS_7D"),
        M("fill_rate_7d", "Fill rate % 7D", agg="ratio", num="fill_days_7d", den="eff_days_7d", fmt="pct"),
        M("mc_bgt_1d", "Maker-checker budget 1D", "MC_BGT_1D"), M("mc_act_1d", "Maker-checker actual 1D", "MC_ACT_1D"),
        M("mc_bgt_7d", "Maker-checker budget 7D", "MC_BGT_7D"), M("mc_act_7d", "Maker-checker actual 7D", "MC_ACT_7D"),
        M("mc_bgt_30d", "Maker-checker budget 30D", "MC_BGT_30D"), M("mc_act_30d", "Maker-checker actual 30D", "MC_ACT_30D"),
        M("mc_ach_1d", "Maker-checker ach % 1D", agg="ratio", num="mc_act_1d", den="mc_bgt_1d", fmt="pct"),
        M("mc_ach_7d", "Maker-checker ach % 7D", agg="ratio", num="mc_act_7d", den="mc_bgt_7d", fmt="pct"),
        M("mc_ach_30d", "Maker-checker ach % 30D", agg="ratio", num="mc_act_30d", den="mc_bgt_30d", fmt="pct"),
        M("pending_overaged", "Pending - overaged", "PENDING_OVERAGED"), M("pending_open", "Pending - in time", "PENDING_OPEN"),
        M("raised_1d", "Tasks raised 1D", "RAISED_1D"), M("raised_7d", "Tasks raised 7D", "RAISED_7D"),
        M("raised_30d", "Tasks raised 30D", "RAISED_30D"),
        M("completed_1d", "Tasks completed 1D", "COMPLETED_1D"), M("completed_7d", "Tasks completed 7D", "COMPLETED_7D"),
        M("completed_30d", "Tasks completed 30D", "COMPLETED_30D"),
        M("delegated_1d", "Tasks delegated 1D", "DELEGATED_1D"), M("delegated_7d", "Tasks delegated 7D", "DELEGATED_7D"),
        M("delegated_30d", "Tasks delegated 30D", "DELEGATED_30D"),
    ],
)

DAY_DATASET = dict(
    code="karma_day", name="Karma - daily activity",
    description="Employee x day since December 2025: Y/N checklist hours and lines, maker-checker quantity against budget, "
                "and tasks raised, received and completed. Figures add up over the dates you pick.",
    view="V_BI_KARMA_DAY", sql="select * from V2RETAIL.SILVER.KARMA_EMP_DAY", snapshot_dim=None, scope=None, cache=3600,
    audience=AUDIENCE,
    refresh="Daily 09:45 IST from the Karma app's YES_NO_REPORT and TASKS_REPORT. Department, manager and location are "
            "today's, from the Karma employee list.",
    dimensions=[D("fiscal_year", "Fiscal year", "DT", "date", _T, "fy"), D("fiscal_quarter", "Fiscal quarter", "DT", "date", _T, "fq"),
                D("month", "Month", "DT", "date", _T, "month"), D("week", "Week", "DT", "date", _T, "week"),
                D("weekday", "Day of week", "DT", "number", _T, "dow"), D("date", "Date", "DT", "date", _T)]
               + _org_dims() + [D("hours_band", "Hours band", "HOURS_BAND", group=_S)],
    measures=[
        M("hours", "Checklist hours", "HOURS", time_agg="sum"), M("lines", "Checklist lines", "LINES", time_agg="sum"),
        M("kras", "KRAs worked", "KRAS", time_agg="sum"),
        M("mc_bgt", "Maker-checker budget", "MC_BGT", time_agg="sum"), M("mc_act", "Maker-checker actual", "MC_ACT", time_agg="sum"),
        M("mc_ach", "Maker-checker ach %", agg="ratio", num="mc_act", den="mc_bgt", fmt="pct"),
        M("raised", "Tasks raised", "RAISED", time_agg="sum"), M("received", "Tasks received", "RECEIVED", time_agg="sum"),
        M("completed", "Tasks completed", "COMPLETED", time_agg="sum"),
        M("linked", "Linked tasks", "LINKED_TASKS", time_agg="sum"), M("closed", "Tasks closed in checklist", "CLOSED_TASKS", time_agg="sum"),
        M("fillers", "Employees who filled", "FILLER_ECODE", agg="count_distinct", time_agg="sum"),
        M("filler_days", "Filled employee-days", "FILLER_ECODE", agg="count", time_agg="sum"),
        M("hours_filled", "Hours on filled days", "HOURS_FILLED", time_agg="sum"),
        M("days", "Days", "DT", agg="count_distinct", time_agg="sum"),
        M("avg_hours", "Average hours per filled day", agg="ratio", num="hours_filled", den="filler_days"),
        M("fillers_per_day", "Average fillers per day", agg="ratio", num="filler_days", den="days"),
        M("raised_per_day", "Tasks raised per day", agg="ratio", num="raised", den="days"),
        M("completion_ratio", "Completed vs received", agg="ratio", num="completed", den="received", fmt="pct"),
    ],
)

DATASETS = [EMP_DATASET, DAY_DATASET]

_EMP_TABLE = ["hours_td", "hours_yd", "avg_7d", "avg_30d", "fill_days_7d", "eff_days_7d", "fill_rate_7d",
              "mc_bgt_1d", "mc_act_1d", "mc_ach_1d", "mc_bgt_30d", "mc_act_30d", "mc_ach_30d",
              "pending_overaged", "pending_open", "raised_1d", "raised_30d", "completed_30d", "delegated_30d"]
_ORG_SLICERS = [{"dim": "dept"}, {"dim": "sub_dept_1"}, {"dim": "designation"}, {"dim": "manager"}]

PRODUCTIVITY = dict(
    name="Karma · HO productivity", category=CATEGORY, dataset="karma_emp", audience=AUDIENCE,
    description="Yesterday's Y/N checklist compliance and task productivity for head office, by department, sub-department, "
                "manager and employee: fill rate, leave, hours, maker-checker achievement, pending and overaged tasks.",
    definition={"version": 1, "filters": HO_ONLY, "pages": [
        {"id": "p1", "name": "Overview", "slicers": _ORG_SLICERS + [{"dim": "status_yd"}],
         "visuals": [
             V("k1", "kpi", "Employees", 0, 0, 2, 2, measures=["employees"]),
             V("k2", "kpi", "Filled", 2, 0, 2, 2, measures=["filled"]),
             V("k3", "kpi", "Not filled", 4, 0, 2, 2, measures=["not_filled"]),
             V("k4", "kpi", "On leave", 6, 0, 2, 2, measures=["on_leave"]),
             V("k5", "kpi", "Fill rate % (of present)", 8, 0, 2, 2, measures=["fill_rate"]),
             V("k6", "kpi", "Fill rate % (of all)", 10, 0, 2, 2, measures=["fill_rate_total"]),
             V("k7", "kpi", "Filled up to 5 h", 0, 2, 2, 2, measures=["upto5"]),
             V("k8", "kpi", "Filled over 5 h", 2, 2, 2, 2, measures=["over5"]),
             V("k9", "kpi", "Average hours (filled)", 4, 2, 2, 2, measures=["avg_hours"]),
             V("k10", "kpi", "Tasks raised yesterday", 6, 2, 2, 2, measures=["raised_yd"]),
             V("k11", "kpi", "Present, no task raised", 8, 2, 2, 2, measures=["no_task"]),
             V("k12", "kpi", "Overaged pending tasks", 10, 2, 2, 2, measures=["pending_overaged"]),
             V("c1", "column", "Fill rate % by department", 0, 4, 7, 6, dims=["dept"], measures=["fill_rate"],
               sort={"by": "fill_rate", "dir": "asc"}, limit=30),
             V("d1", "donut", "Employees by yesterday's status", 7, 4, 5, 6, dims=["status_yd"], measures=["employees"],
               sort={"by": "employees", "dir": "desc"}),
             V("b1", "bar", "Tasks raised yesterday by department", 0, 10, 6, 7, dims=["dept"], measures=["raised_yd"],
               sort={"by": "raised_yd", "dir": "desc"}, limit=20),
             V("c2", "column", "Employees by hours band", 6, 10, 6, 7, dims=["hours_band"], measures=["employees"],
               sort={"by": "hours_band", "dir": "asc"}),
         ]},
        {"id": "p2", "name": "Department summary", "slicers": _ORG_SLICERS,
         "visuals": [
             V("t1", "table", "HO department summary - checklist compliance and task productivity", 0, 0, 12, 10,
               dims=["dept"], measures=["employees", "filled", "not_filled", "on_leave", "upto5", "over5", "no_task",
                                        "raised_yd", "fill_rate", "fill_rate_total", "avg_hours", "pending_overaged"],
               sort={"by": "employees", "dir": "desc"}, limit=100),
             V("t2", "table", "By sub-department", 0, 10, 12, 9, dims=["dept", "sub_dept_1"],
               measures=["employees", "filled", "not_filled", "on_leave", "no_task", "raised_yd", "fill_rate", "avg_hours"],
               sort={"by": "employees", "dir": "desc"}, limit=500),
         ]},
        {"id": "p3", "name": "Employee report", "slicers": _ORG_SLICERS + [{"dim": "status_yd"}, {"dim": "hours_band"}],
         "visuals": [
             V("t1", "table", "Y/N productivity by employee", 0, 0, 12, 16,
               dims=["ecode", "employee", "dept", "sub_dept_1", "designation", "manager", "status_yd"],
               measures=_EMP_TABLE, sort={"by": "hours_yd", "dir": "desc"}, limit=2000),
         ]},
        {"id": "p4", "name": "Tasks", "slicers": _ORG_SLICERS + [{"dim": "pending_band"}],
         "visuals": [
             V("k1", "kpi", "Raised 1D", 0, 0, 2, 2, measures=["raised_1d"]),
             V("k2", "kpi", "Raised 7D", 2, 0, 2, 2, measures=["raised_7d"]),
             V("k3", "kpi", "Raised 30D", 4, 0, 2, 2, measures=["raised_30d"]),
             V("k4", "kpi", "Completed 30D", 6, 0, 2, 2, measures=["completed_30d"]),
             V("k5", "kpi", "Delegated 30D", 8, 0, 2, 2, measures=["delegated_30d"]),
             V("k6", "kpi", "Pending - overaged", 10, 0, 2, 2, measures=["pending_overaged"]),
             V("t1", "table", "Tasks by department", 0, 2, 12, 8, dims=["dept"],
               measures=["raised_1d", "raised_7d", "raised_30d", "completed_1d", "completed_7d", "completed_30d",
                         "delegated_1d", "delegated_7d", "delegated_30d", "pending_open", "pending_overaged"],
               sort={"by": "raised_30d", "dir": "desc"}, limit=100),
             V("b1", "bar", "Top 15 employees by overaged tasks", 0, 10, 6, 8, dims=["employee"], measures=["pending_overaged"],
               sort={"by": "pending_overaged", "dir": "desc"}, limit=15),
             V("c1", "column", "Employees by overaged tasks", 6, 10, 6, 8, dims=["pending_band"], measures=["employees"],
               sort={"by": "pending_band", "dir": "asc"}),
         ]},
        {"id": "p5", "name": "Maker-checker", "slicers": _ORG_SLICERS,
         "visuals": [
             V("k1", "kpi", "Ach % 1D", 0, 0, 4, 2, measures=["mc_ach_1d"]),
             V("k2", "kpi", "Ach % 7D", 4, 0, 4, 2, measures=["mc_ach_7d"]),
             V("k3", "kpi", "Ach % 30D", 8, 0, 4, 2, measures=["mc_ach_30d"]),
             V("t1", "table", "Maker-checker by department", 0, 2, 12, 7, dims=["dept"],
               measures=["mc_bgt_1d", "mc_act_1d", "mc_ach_1d", "mc_bgt_7d", "mc_act_7d", "mc_ach_7d", "mc_bgt_30d",
                         "mc_act_30d", "mc_ach_30d"], sort={"by": "mc_bgt_30d", "dir": "desc"}, limit=100),
             V("t2", "table", "Maker-checker by employee", 0, 9, 12, 9, dims=["ecode", "employee", "dept", "manager"],
               measures=["mc_bgt_1d", "mc_act_1d", "mc_ach_1d", "mc_bgt_30d", "mc_act_30d", "mc_ach_30d"],
               sort={"by": "mc_bgt_30d", "dir": "desc"}, limit=1000),
         ]},
        {"id": "p6", "name": "Exceptions", "slicers": _ORG_SLICERS + [{"dim": "status_yd"}, {"dim": "attendance_yd"}],
         "visuals": [
             V("t1", "table", "Employees by 7-day fill rate, lowest first (pick 'Not filled' above for yesterday's gaps)",
               0, 0, 12, 15, dims=["ecode", "employee", "dept", "manager", "attendance_yd", "status_yd"],
               measures=["hours_yd", "avg_7d", "fill_days_7d", "eff_days_7d", "fill_rate_7d", "pending_overaged",
                         "raised_7d"], sort={"by": "fill_rate_7d", "dir": "asc"}, limit=1000),
         ]},
    ]},
)

TRENDS = dict(
    name="Karma · Trends", category=CATEGORY, dataset="karma_day", audience=AUDIENCE,
    description="Day-by-day Y/N checklist and task activity for head office since December 2025: how many filled, hours, "
                "maker-checker achievement and tasks raised and completed, by department, month and employee.",
    definition={"version": 1, "filters": HO_ONLY, "pages": [
        {"id": "p1", "name": "Daily trend",
         "slicers": [{"dim": "date", "mode": "range", "preset": "mtd"}, {"dim": "dept"}, {"dim": "sub_dept_1"}, {"dim": "manager"}],
         "visuals": [
             V("k1", "kpi", "Average fillers per day", 0, 0, 3, 2, measures=["fillers_per_day"]),
             V("k2", "kpi", "Average hours per filled day", 3, 0, 3, 2, measures=["avg_hours"]),
             V("k3", "kpi", "Tasks raised per day", 6, 0, 3, 2, measures=["raised_per_day"]),
             V("k4", "kpi", "Maker-checker ach %", 9, 0, 3, 2, measures=["mc_ach"]),
             V("l1", "line", "Employees who filled, by day", 0, 2, 12, 6, dims=["date"], measures=["fillers"],
               sort={"by": "date", "dir": "asc"}),
             V("l2", "line", "Tasks raised and completed, by day", 0, 8, 6, 6, dims=["date"], measures=["raised", "completed"],
               sort={"by": "date", "dir": "asc"}),
             V("l3", "line", "Checklist hours, by day", 6, 8, 6, 6, dims=["date"], measures=["hours"],
               sort={"by": "date", "dir": "asc"}),
         ]},
        {"id": "p2", "name": "Department x month",
         "slicers": [{"dim": "date", "mode": "range", "preset": "fytd"}, {"dim": "dept"}, {"dim": "designation"}],
         "visuals": [
             V("m1", "matrix", "Average fillers per day: department by month", 0, 0, 12, 8, dims=["dept", "month"],
               measures=["fillers_per_day"]),
             V("t1", "table", "Departments in the period", 0, 8, 12, 8, dims=["dept"],
               measures=["fillers", "fillers_per_day", "hours", "avg_hours", "lines", "mc_bgt", "mc_act", "mc_ach", "raised",
                         "received", "completed", "completion_ratio"], sort={"by": "hours", "dir": "desc"}, limit=100),
         ]},
        {"id": "p3", "name": "Employees",
         "slicers": [{"dim": "date", "mode": "range", "preset": "mtd"}, {"dim": "dept"}, {"dim": "sub_dept_1"},
                     {"dim": "manager"}, {"dim": "designation"}],
         "visuals": [
             V("t1", "table", "Employees in the period", 0, 0, 12, 16, dims=["ecode", "employee", "dept", "manager"],
               measures=["filler_days", "hours", "avg_hours", "lines", "kras", "mc_bgt", "mc_act", "mc_ach", "raised",
                         "received", "completed", "completion_ratio"], sort={"by": "hours", "dir": "desc"}, limit=2000),
         ]},
        {"id": "p4", "name": "Weekly pattern",
         "slicers": [{"dim": "date", "mode": "range", "preset": "fytd"}, {"dim": "dept"}],
         "visuals": [
             V("c1", "column", "Average fillers by day of week", 0, 0, 6, 6, dims=["weekday"], measures=["fillers_per_day"],
               sort={"by": "weekday", "dir": "asc"}),
             V("c2", "column", "Tasks raised per day by day of week", 6, 0, 6, 6, dims=["weekday"],
               measures=["raised_per_day"], sort={"by": "weekday", "dir": "asc"}),
             V("l1", "line", "Weekly hours and tasks raised", 0, 6, 12, 6, dims=["week"], measures=["hours", "raised"],
               sort={"by": "week", "dir": "asc"}),
         ]},
    ]},
)

REPORTS = [PRODUCTIVITY, TRENDS]
