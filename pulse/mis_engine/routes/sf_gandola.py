"""The CSV helpers the MIS routes import from DFS routes/sf_gandola.py (copied unchanged; the rest of
that 1,700-line module - Gandola reports and dumps - is not part of MIS)."""
import csv
import io

from fastapi.responses import StreamingResponse

# Blank text cells are written as 'NA' in every report file (= ISNULL(col,'NA')). Only TEXT columns:
# a numeric NULL stays empty, so Excel formulas over the file keep working.
NA = "NA"
_SF_TEXT = 2          # snowflake-connector FIELD type code of VARCHAR/TEXT


def _text_cols(cur):
    """Per result column: is it TEXT? Read off the cursor's description (after execute)."""
    return [getattr(d, "type_code", d[1] if len(d) > 1 else None) == _SF_TEXT
            for d in (getattr(cur, "description", None) or [])]


def _na_row(r, text):
    """A result row with NULL/blank text cells as 'NA'; numeric NULLs as ''."""
    out = []
    for i, v in enumerate(r):
        if v is None:
            out.append(NA if i < len(text) and text[i] else "")
        elif v.__class__ is str and not v.strip():
            out.append(NA)
        else:
            out.append(v)
    return out


def _stream_csv(cur, cols, filename):
    text = _text_cols(cur)

    def generate():
        buf = io.StringIO(); w = csv.writer(buf)
        w.writerow(cols); yield buf.getvalue(); buf.seek(0); buf.truncate(0)
        while True:
            batch = cur.fetchmany(2000)
            if not batch:
                break
            for r in batch:
                w.writerow(_na_row(r, text))
            yield buf.getvalue(); buf.seek(0); buf.truncate(0)
    return StreamingResponse(generate(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})
