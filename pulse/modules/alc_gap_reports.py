"""Allocation gap reports in the report library: the allocation team's 22 gap formats (Desktop\\New folder\\Allocation),
each opened as a table with the format's own columns, in the format's order, plus its GAP flag.

The rows are SILVER.ALC_GAP_ROWS (one row per report x checked store / store-MJ / MJ / RDC x generic-colour), read
through PULSE.V_BI_ALC_GAP and rebuilt after every ARS allocation run (load_ars.py, 07:30 and 13:30 IST). The flags are
SP_ALC_GAP_BUILD's, with thresholds in SILVER.ALC_GAP_CONFIG, so they match the allocation gap dashboards.

They are filed in an "Allocation" folder of the Merchandising & Planning tree (an existing "Allocation" folder of the
report library is reused), with links to the overview dashboard and the trend first. Like the B&M reports, a deploy
refreshes the query definitions and files a report once; it never moves or renames what an admin has organised."""
import json
import uuid

V = "V2RETAIL.PULSE.V_BI_ALC_GAP"
TREE = "dept:MERCH"
FOLDER = "Allocation"
FOLDER_DESC = ("The allocation team's 22 gap reports on the latest ARS allocation run, in their own formats. "
               "Gaps only = Y shows the flagged rows; pick a run date for an earlier run.")
ZONES = ["Z1", "Z2", "Z3", "Z4", "Z5", "Z6"]


def P(label, type_="str", choices=None, default=None, max_len=60):
    p = {"type": type_, "label": label, "required": False, "max_len": max_len}
    if choices:
        p["choices"] = choices
    if default is not None:
        p["default"] = default
    return p


_BASE_PARAMS = {"run_date": P("Run date (blank = latest)", "date"),
                "gaps_only": P("Gaps only", choices=["Y", "N"], default="Y")}
_STORE_PARAMS = {"zone": P("Zone", choices=ZONES), "rdc": P("RDC", choices=["DH24", "DW01"]),
                 "store": P("Store code", max_len=10)}
_MJ_PARAMS = {"segment": P("Segment", choices=["APP", "GM"]), "division": P("Division"),
              "major_category": P("Major category (contains)")}

_W_STORE = ("(%(zone)s is null or ZONE_CODE = %(zone)s) and (%(rdc)s is null or RDC = %(rdc)s)"
            " and (%(store)s is null or ST_CD = upper(%(store)s))")
_W_MJ = ("(%(segment)s is null or SEG = %(segment)s) and (%(division)s is null or upper(DIV) = upper(%(division)s))"
         " and (%(major_category)s is null or MAJ_CAT ilike '%%' || %(major_category)s || '%%')")

# columns: (format heading, SQL expression)
_ST = [("ST-CD", "ST_CD"), ("ST-STAT", "ST_STAT"), ("ST-OP-DT", "ST_OP_DT"), ("ST-NM", "ST_NM")]
_SM = [("ST-CD", "ST_CD"), ("ST-STAT", "ST_STAT"), ("ST-NM", "ST_NM"), ("SEG", "SEG"), ("DIV", "DIV"),
       ("SUB DIV", "SUB_DIV"), ("MJ", "MAJ_CAT"), ("SSN", "SSN")]
_CM = [("SEG", "SEG"), ("DIV", "DIV"), ("SUB DIV", "SUB_DIV"), ("MJ", "MAJ_CAT"), ("SSN", "SSN")]


def q(x, d=0):
    return "round(%s, %d)" % (x, d)


def pct(num, den):
    return "iff(coalesce(%s, 0) > 0, round(%s / %s * 100, 1), 0)" % (den, num, den)


_CO6 = [("MJ MBQ", q("MBQ")), ("ALC-Q", q("ALC_Q")), ("SHORT AFT ALC", q("SHORT_AFT")),
        ("OPT SHORT AFT ALC", q("OPT_SHORT"))]
_REM = [("REM MSA", q("REM_MSA")), ("REM MSA OPT COUNT", q("REM_MSA_OPT"))]

# code, format title, level, columns, extra where, rule (shown as the description)
FORMATS = [
    ("ST01", "ST-ONE-LINER FR% (H OR L)", "ST",
     _ST + [("ST-MBQ", q("MBQ")), ("ST TTL STK", q("AFTER_STK")), ("ST FR%", pct("AFTER_STK", "MBQ")),
            ("SHORT AFT ALC", q("SHORT_AFT")), ("EXCESS AFT ALC", q("EXCESS_AFT"))], None,
     "Store fill rate after allocation (total stock / MBQ) above 120% or below 80%."),
    ("ST02", "INSTALLED VS ALLOCATION GND", "ST",
     _ST + [("INSTALLED GND", q("INSTALLED_GND", 1)), ("ALC GND", q("ALC_GND", 1)),
            ("INT VS ALC GND", q("DIFF_Q", 1))], None,
     "Installed gandola minus allocation gandola beyond +/- 10, or either of them not given."),
    ("ST03", "ZERO ALC IN SCHEDULE ST", "ST",
     _ST + [("RDC", "RDC"), ("SCHLD ST", "SCHLD"), ("BEFORE ALC ST-STK", q("BEFORE_STK")), ("ALC-Q", q("ALC_Q")),
            ("AFTER ALC ST-STK", q("AFTER_STK")), ("SHORT AFT ALC", q("SHORT_AFT")), ("EXCESS AFT ALC", q("EXCESS_AFT"))],
     "SCHLD > 0", "Store scheduled for the run's weekday that got zero allocation."),
    ("ST04", "DISP GIVEN BUT ALC NOT DONE", "ST",
     _ST + [("BGT DISP", q("BGT_DISP")), ("ALC-Q", q("ALC_Q"))], None,
     "Store has budget display but got zero allocation."),
    ("SM01", "DISPLAY NOT MADE BUT ALC DONE (ST-MJ)", "ST-MJ",
     _SM + [("BGT DISP-Q", q("BGT_DISP")), ("ALC-Q", q("ALC_Q"))], None,
     "Budget display of the store x major category is zero, yet allocation was done."),
    ("SM02", "DISP VS ACC-DENSITY", "ST-MJ",
     _SM + [("FINAL ACC-DENS", q("ACC_DENS")), ("BGT DISP", q("BGT_DISP")), ("DIFF", q("DIFF_Q"))], None,
     "Budget display is below the accessory density (DIFF = BGT DISP - ACC-DENS < 0)."),
    ("SM03", "EXCESS ALC @ ST-MJ LEVEL", "ST-MJ",
     _SM + [("ST-MJ MBQ", q("MBQ")), ("ST-MJ BEFORE STK", q("BEFORE_STK")),
            ("ST-MJ BEFORE FR%", pct("BEFORE_STK", "MBQ")), ("ALC-Q", q("ALC_Q")), ("ST-MJ AFTER STK", q("AFTER_STK")),
            ("ST-MJ AFT FR%", pct("AFTER_STK", "MBQ")), ("SHORT AFT ALC", q("SHORT_AFT")),
            ("EXCESS AFT ALC", q("EXCESS_AFT")), ("REM MSA", q("REM_MSA"))], None,
     "Fill rate before the run was above 110%, yet allocation was done."),
    ("SM04", "MSA STK AVAILABLE VS STORE SHORT (ST-MJ)", "ST-MJ",
     _SM + [("ST-MJ MBQ", q("MBQ")), ("TTL ST-MJ STK", q("AFTER_STK")), ("SHORT AFT ALC", q("SHORT_AFT")),
            ("REM MSA", q("REM_MSA"))], None,
     "Store x major category short after allocation while MSA of the major category remains at the RDCs."),
    ("SM05", "ST-MAJ_CAT FR% (H OR L)", "ST-MJ",
     _SM + [("ST-MJ MBQ", q("MBQ")), ("ST-MJ TTL STK", q("AFTER_STK")), ("ST-MJ FR%", pct("AFTER_STK", "MBQ"))], None,
     "Fill rate after allocation above 120% or below 80%. Stock with zero MBQ is NO MBQ, not a gap."),
    ("CM01", "ZERO ALLOCATION GAP REPORT (MAJ-CAT)", "CO-MJ", _CM + _CO6 + _REM, None,
     "Major category short across stores with MSA remaining, and zero allocation."),
    ("CM02", "ZERO OR LOW MSA", "CO-MJ", _CM + _CO6 + _REM, None,
     "Short above 3,000 with remaining MSA below 3,000."),
    ("CM03", "MSA STK AVAILABLE VS NO ALC (CO_MAJ)", "CO-MJ", _CM + _CO6 + _REM, None,
     "Short above 100, remaining MSA above 1,000, and zero allocation."),
    ("CM04", "GRT STOCK AVAILABLE BUT ST-SHORT (CO-MJ)", "CO-MJ",
     _CM + _CO6 + _REM + [("PROC GRT STK", q("PROC_GRT")), ("GRT OPT COUNT", q("PROC_GRT_OPT"))], None,
     "Short above 100, no MSA left, but processed GRT stock is available."),
    ("CM05", "PROC-GRT VS CO-SHORT", "CO-MJ",
     _CM + _CO6 + [("PROC GRT STK", q("PROC_GRT")), ("GRT OPT COUNT", q("PROC_GRT_OPT"))], None,
     "Short above 1,000 and processed GRT stock above 1,000: need priority of these major-category bins in GRT."),
    ("CM06", "UN-PROC-GRT VS CO-SHORT", "CO-MJ",
     _CM + _CO6 + [("UN-PROC GRT STK", q("UNPROC_GRT")), ("GRT OPT COUNT", q("UNPROC_GRT_OPT"))], None,
     "Short above 1,000 and un-processed GRT stock (0032) above 1,000: need priority of these major-category bins in GRT."),
    ("CM07", "HOLD GAP", "CO-MJ",
     _CM + [("MJ MBQ", q("MBQ")), ("SHORT AFT ALC", q("SHORT_AFT")), ("OPT SHORT AFT ALC", q("OPT_SHORT")),
            ("REM MSA", q("REM_MSA")), ("REM HOLD MSA", q("HOLD_MSA")),
            ("HOLD VS TTL-MSA CONTB%", pct("HOLD_MSA", "REM_MSA + HOLD_MSA"))], None,
     "Short while hold is more than 30% of remaining MSA + hold."),
    ("CM08", "HOLD MSA GAP", "CO-MJ",
     _CM + [("MJ MBQ", q("MBQ")), ("SHORT AFT ALC", q("SHORT_AFT")), ("OPT SHORT AFT ALC", q("OPT_SHORT")),
            ("REM MSA", q("REM_MSA")), ("REM HOLD MSA", q("HOLD_MSA"))], None,
     "GM with any hold (GM-HOLD-GAP); others: hold present, no free MSA, and short."),
    ("CM09", "MSA AVAILABLE BUT DISP NOT GIVEN", "CO-MJ",
     _CM + [("BGT DISP", q("BGT_DISP")), ("REM MSA", q("REM_MSA"))], None,
     "No budget display for the major category while remaining MSA is above 1,000."),
    ("CM10", "BGT-DISP VS ALC-DISP", "CO-MJ",
     [("SEG", "SEG"), ("DIV", "DIV"), ("SUB DIV", "SUB_DIV"), ("MAJ-CAT", "MAJ_CAT"), ("SSN", "SSN"),
      ("BGT DISP", q("BGT_DISP")), ("ALC DISP", q("ALC_DISP")), ("DIFF", q("DIFF_Q")),
      ("DIFF %", "iff(coalesce(BGT_DISP, 0) > 0, round((ALC_DISP / BGT_DISP - 1) * 100, 1), 0)")], None,
     "Allocation display minus budget display above 3,000 or below -1,000, or more than 5% either way."),
    ("CM11", "PACK-SIZE GAP", "CO-MJ",
     [("SEG", "SEG"), ("DIV", "DIV"), ("SUB-DIV", "SUB_DIV"), ("MAJ_CAT", "MAJ_CAT"), ("SSN", "SSN"),
      ("MJ-STS", "MJ_STS"), ("SZ-APP", "SZ_APP"), ("ACC DENSITY", q("ACC_DENS")), ("PAK_SZ", q("PAK_SZ")),
      ("REM MSA", q("REM_MSA"))], "SEG = 'GM'",
     "GM, non-SSNL, active major category with MSA above 100 whose half accessory density is below the pack size."),
    ("GC01", "HIGH MSA", "GEN-CLR",
     _CM + [("GEN", "GEN_ART"), ("CLR", "CLR"), ("REM MSA", q("REM_MSA"))], None,
     "Remaining MSA of a generic-colour at an RDC above 5,000 (GM) or 3,000 (others)."),
    ("GC02", "LOW MSA-Q, ALC NOT DONE (MJ-RDC WISE)", "GEN-CLR",
     [("SEG", "SEG"), ("DIV", "DIV"), ("SUB DIV", "SUB_DIV"), ("MJ", "MAJ_CAT"), ("GEN", "GEN_ART"), ("CLR", "CLR"),
      ("SSN", "SSN")] + _CO6 + _REM, "REM_MSA > 0",
     "Generic-colour with under 50 pcs of MSA left unallocated while its major category is short at that RDC (manual "
     "push). MJ MBQ, short and option short are the major category's at that RDC."),
]


def _report(code, title, level, cols, extra_where, rule):
    if level in ("ST", "ST-MJ"):
        params = {**_BASE_PARAMS, **_STORE_PARAMS, **(_MJ_PARAMS if level == "ST-MJ" else {})}
        where = [_W_STORE] + ([_W_MJ] if level == "ST-MJ" else [])
        scope = "ST_CD"
    else:
        params = {**_BASE_PARAMS, **_MJ_PARAMS, **({"rdc": _STORE_PARAMS["rdc"]} if level == "GEN-CLR" else {})}
        where = [_W_MJ] + (["(%(rdc)s is null or RDC = %(rdc)s)"] if level == "GEN-CLR" else [])
        scope = None
    cols = cols + [("GAP", "GAP_FLAG")] + ([("RDC", "RDC")] if level == "GEN-CLR" else [])
    cols = cols + [("RUN DATE", "RUN_DATE"), ("ARS SESSION", "SESSION_ID")]
    if extra_where:
        where.append(extra_where)
    # headings like "ST FR%" carry a literal %, which the driver's pyformat binding needs doubled
    sql = ("select " + ",\n       ".join('%s "%s"' % (expr, head.replace("%", "%%")) for head, expr in cols) +
           "\nfrom %s\nwhere %s and REPORT_CODE = '%s'" % (V, "{scope}" if scope else "1=1", code) +
           "\n  and RUN_DATE = coalesce(%%(run_date)s, (select max(RUN_DATE) from %s))" % V +
           "\n  and (%(gaps_only)s = 'N' or IS_GAP = 1)\n  and " + "\n  and ".join(where) +
           "\norder by IS_GAP desc, GAP_QTY desc nulls last")
    return dict(code="alc-" + code.lower(), name="%s %s" % (code, title), level=level, sql=sql, params=params,
                scope=scope, description="%s (%s level). %s Live from the latest ARS allocation run; Gaps only = Y "
                                         "shows the flagged rows." % (title, level, rule))


REPORTS = [_report(*f) for f in FORMATS]


GAP_FOLDER = "Allocation gap reports"


def _new_root(cur, tree, name):
    fid = uuid.uuid4()
    cur.execute("insert into nav.folder (id, tree, name, description, path, sort_order) values (%s,%s,%s,%s,%s,"
                " (select coalesce(max(sort_order), 0) + 10 from nav.folder where section = 'reports' and tree = %s"
                " and parent_id is null))", (fid, tree, name, FOLDER_DESC, "/%s/" % fid, tree))
    return fid


def _folder(cur):
    """Where the reports are filed. The admins' own "Allocation" section of the library (a custom tree) comes first:
    its gap-report folder, created at the top of that tree if missing. Without that section, an "Allocation" folder of
    the Merchandising & Planning tree (created if missing)."""
    cur.execute("select tree from nav.tree_name where section = 'reports' and tree like 'custom:%%'"
                " and lower(trim(name)) like 'allocation%%' order by length(name), tree limit 1")
    row = cur.fetchone()
    if row:
        tree = row["tree"]
        cur.execute("select id from nav.folder where section = 'reports' and tree = %s and parent_id is null"
                    " and not is_archived and (lower(name) = lower(%s) or lower(name) like '%%gap%%')"
                    " order by (lower(name) = lower(%s)) desc, sort_order limit 1", (tree, GAP_FOLDER, GAP_FOLDER))
        f = cur.fetchone()
        return (f["id"] if f else _new_root(cur, tree, GAP_FOLDER)), tree
    cur.execute("select id from nav.folder where section = 'reports' and tree = %s and parent_id is null"
                " and lower(name) = lower(%s) and not is_archived", (TREE, FOLDER))
    f = cur.fetchone()
    return (f["id"] if f else _new_root(cur, TREE, FOLDER)), TREE


def _retire_merch_folder(cur, keep_tree):
    """An earlier deploy filed these reports in an "Allocation" folder of the Merchandising & Planning tree. Once they
    live in the Allocation section, retire that folder if it holds nothing but these reports and has no sub-folders."""
    if keep_tree == TREE:
        return
    cur.execute("select id from nav.folder where section = 'reports' and tree = %s and parent_id is null"
                " and lower(name) = lower(%s) and description = %s and not is_archived", (TREE, FOLDER, FOLDER_DESC))
    old = cur.fetchone()
    if not old:
        return
    cur.execute("select count(*) n from nav.item i left join rpt.report r on r.id = i.report_id where i.folder_id = %s"
                " and (r.code is null or r.code not like 'alc-%%')", (old["id"],))
    if cur.fetchone()["n"]:
        return
    cur.execute("select count(*) n from nav.folder where parent_id = %s and not is_archived", (old["id"],))
    if cur.fetchone()["n"]:
        return
    cur.execute("delete from nav.item where folder_id = %s", (old["id"],))
    cur.execute("update nav.folder set is_archived = true, updated_at = now() where id = %s", (old["id"],))


def seed(cur, rid):
    """Refresh the query definitions, make sure each report exists, and file each one (and the two dashboard links)
    in the Allocation folder once. rid(r) gives a dashboard's fixed id."""
    from . import alc_gap_catalog as cat
    for r in REPORTS:
        cur.execute("insert into data.query_def (code, name, sql_template, params, scope_column, permission, cache_seconds, max_rows)"
                    " values (%s,%s,%s,%s,%s,'reports.view',1800,100000) on conflict (code) do update set name = excluded.name,"
                    " sql_template = excluded.sql_template, params = excluded.params, scope_column = excluded.scope_column",
                    (r["code"], r["name"], r["sql"], json.dumps(r["params"]), r["scope"]))
        cur.execute("insert into rpt.report (code, name, description, layer, owner_dept, owner_name, kind, query_code, tags)"
                    " values (%s,%s,%s,'department','MERCH','Data Platform','table',%s,%s) on conflict (code) do update"
                    " set description = excluded.description",
                    (r["code"], r["name"], r["description"], r["code"], ["Allocation", "live"]))
    links = [("alc-dashboard", cat.OVERVIEW), ("alc-trend", cat.TREND)]
    for code, d in links:
        cur.execute("insert into rpt.report (code, name, description, layer, owner_dept, owner_name, kind, url, tags)"
                    " values (%s,%s,%s,'department','MERCH','Data Platform','link',%s,%s) on conflict (code) do update"
                    " set url = excluded.url",
                    (code, d["name"], d["description"], "/reports/dynamic/%s" % rid(d), ["Allocation", "dashboard"]))
    fid, tree = _folder(cur)
    for code in [c for c, _ in links] + [r["code"] for r in REPORTS]:
        cur.execute("select id from rpt.report where code = %s", (code,))
        rep = cur.fetchone()["id"]
        cur.execute("select 1 from nav.item i join nav.folder f on f.id = i.folder_id where f.section = 'reports'"
                    " and f.tree = %s and not f.is_archived and i.report_id = %s", (tree, rep))
        if cur.fetchone():
            continue
        cur.execute("insert into nav.item (folder_id, kind, report_id, sort_order) values (%s,'report',%s,"
                    " (select coalesce(max(sort_order), 0) + 10 from nav.item where folder_id = %s))", (fid, rep, fid))
