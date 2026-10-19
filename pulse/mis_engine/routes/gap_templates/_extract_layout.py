"""Turn the 26 GAP-report templates into one machine-readable layout spec.

WHY A SPEC AND NOT THE FILES THEMSELVES. The obvious way to "fill the template" is to open the
.xlsx with openpyxl and write rows into it. That works for the samples in these files (a dozen
rows each) and falls over on the real thing: the store x maj-cat reports are 255,638 rows on the
current session and openpyxl holds every cell as a Python object. The writer therefore uses
xlsxwriter in constant-memory mode, which streams one row at a time and never looks back — and to
do that it needs the layout as DATA rather than as a workbook to mutate.

So this script reads each template ONCE and records everything the writer has to reproduce: the
title, where the header sits, the header text (newlines and all — 'SUB\\nDIV' is two lines in one
cell, not a typo), every column's width and number format, and the GAP formula with its row number
turned into a placeholder. The result is gap_layout.json, checked in beside the templates.

RUN IT AGAIN whenever a template changes — the .xlsx files next to this script are the source of
truth for the LAYOUT, and this spec is a build artefact of them. What the columns MEAN, and which
Snowflake column feeds each one, lives in sf_alloc_gap.py; that is deliberately not here, because
a template can be restyled without changing where its numbers come from.
"""
import json
import os
import re

import openpyxl
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "gap_layout.json")


def _header_row(ws):
    """The row the column names sit on — the first with four or more text cells.

    Four is enough to separate it from the title (one cell in A1) and from a stray note, and
    every one of the 26 has at least eight named columns. Measured: 24 of them put it on row 4
    and the other two (BGT-DISP, PACK-SIZE) on row 3."""
    for r in range(1, 9):
        n = sum(1 for c in range(1, ws.max_column + 1)
                if isinstance(ws.cell(r, c).value, str) and ws.cell(r, c).value.strip())
        if n >= 4:
            return r
    raise SystemExit(f"{ws.title}: no header row found")


def _first_formula_row(ws, hdr, last_col):
    """The row the GAP formula is seeded on.

    In every template the author left one row above the sample data carrying the formula and
    nothing else — that is the row the writer copies down.

    IT IS NOT SIMPLY THE FIRST FORMULA UNDER THE HEADER. BGT-DISP puts a totals strip in the two
    rows between its header and its seed, and the last column of the first of them holds
    COUNTIFS(K7:K1048575,"GAP") — a formula, so a naive scan stops there and mistakes the totals
    row for the data row. The two are told apart by what they point AT: a per-row formula
    references its own row (K6 reads I6), a totals formula references a range starting somewhere
    else. So the seed is the first row whose last-column formula mentions its own row number.

    Falling back to the row after the header keeps a template without a seed from failing."""
    for r in range(hdr + 1, ws.max_row + 1):
        v = ws.cell(r, last_col).value
        if isinstance(v, str) and v.startswith("=") and re.search(rf"[A-Z]\$?{r}(?![\d])", v):
            return r
    return hdr + 1


def _deref(formula, row):
    """A1-style references on the formula's OWN row become {r}, so the writer can re-point them.

    Only same-row references are rewritten. An absolute row ($7) or a reference to a different
    row is a deliberate anchor — BGT-DISP's totals point at G7:G1048575 from row 4 — and must
    survive untouched, so the pattern requires the row number to match and no '$' before it."""
    return re.sub(rf"(?<![\$\d]){row}(?![\d])", "{r}", formula)


def _fill(cell):
    """The cell's solid fill as RRGGBB, or None.

    Theme-indexed fills raise on .rgb rather than returning a colour, so the access is guarded:
    a header that loses its grey is cosmetic, a build that dies on it is not."""
    try:
        if not cell.fill or cell.fill.fill_type != "solid":
            return None
        rgb = cell.fill.start_color.rgb
        if not isinstance(rgb, str) or len(rgb) < 6:
            return None
        return rgb[-6:]
    except Exception:
        return None


def extract(path):
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    hdr = _header_row(ws)
    ncol = ws.max_column
    seed = _first_formula_row(ws, hdr, ncol)

    headers, widths, fills = [], [], []
    for c in range(1, ncol + 1):
        headers.append(ws.cell(hdr, c).value)
        dim = ws.column_dimensions.get(get_column_letter(c))
        widths.append(round(dim.width, 1) if dim and dim.width else None)
        fills.append(_fill(ws.cell(hdr, c)))

    # Number formats come from the SEED row, not the header: the header is always General and the
    # seed is where the author set 0% on a fill rate and 0 on a quantity.
    formats = [ws.cell(seed, c).number_format for c in range(1, ncol + 1)]

    # Every formula that travels down with the data, not just the GAP one. DISP VS ACC-DENSITY
    # computes its DIFF in L and its verdict in M; EXCESS ALC computes three.
    #
    # THE SEED ROW IS NOT ALWAYS COMPLETE. EXCESS ALC seeds L, O and P on row 7 but leaves N
    # blank there — its AFTER-STK formula (=+K8+M8) appears first on the sample row below. Taking
    # only the seed row therefore loses a column that has to be computed, and the writer would
    # emit it as an empty cell. So the seed row is read first and the sample rows beneath it fill
    # in any column the seed left out; the seed wins wherever both have one.
    formulas = {}
    for r in range(seed, min(seed + 4, ws.max_row + 1)):
        for c in range(1, ncol + 1):
            letter = get_column_letter(c)
            if letter in formulas:
                continue
            v = ws.cell(r, c).value
            if isinstance(v, str) and v.startswith("="):
                formulas[letter] = _deref(v, r)

    # Rows between the header and the data — the totals strip. BGT-DISP is the only template that
    # fills them (SUM over the whole column, SUBTOTAL over what a filter leaves, COUNTIFS of the
    # GAP column), and its formulas are recorded verbatim so the writer can re-point them at the
    # real last row instead of the template's.
    strip = {}
    for r in range(hdr + 1, seed):
        row = {}
        for c in range(1, ncol + 1):
            v = ws.cell(r, c).value
            if v is not None:
                row[get_column_letter(c)] = v
        if row:
            strip[str(r - hdr)] = row       # keyed by offset from the header, not absolute row

    title = ws.cell(1, 1).value if hdr > 1 else None
    return {
        "file": os.path.basename(path),
        "sheet": ws.title,
        "title": title,
        "title_row": 1 if title else None,
        "header_row": hdr,
        "data_row": seed,
        "n_cols": ncol,
        "headers": headers,
        "widths": widths,
        "formats": formats,
        "header_fills": fills,
        "formulas": formulas,
        "strip": strip,
        "header_font_size": ws.cell(hdr, 1).font.size or 9,
        "header_row_height": (ws.row_dimensions[hdr].height
                              if hdr in ws.row_dimensions else None),
    }


def main():
    specs = {}
    for fn in sorted(os.listdir(HERE)):
        if not fn.lower().endswith(".xlsx") or fn.startswith("~"):
            continue
        spec = extract(os.path.join(HERE, fn))
        specs[os.path.splitext(fn)[0]] = spec
        print(f"{fn[:48]:50} hdr=r{spec['header_row']} data=r{spec['data_row']} "
              f"cols={spec['n_cols']} formulas={','.join(spec['formulas']) or '-'}")
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(specs, fh, indent=1, ensure_ascii=False)
    print(f"\n{len(specs)} templates -> {OUT}")


if __name__ == "__main__":
    main()
