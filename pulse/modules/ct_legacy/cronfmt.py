"""Turn a Snowflake task SCHEDULE into plain English, always expressed in IST.

Snowflake schedules on this account mix three time zones (Asia/Kolkata, UTC and
America/Los_Angeles), so "0 1 * * * UTC" and "30 6 * * * Asia/Kolkata" are the same
06:30 IST run written two ways. Showing every schedule in IST is the whole point here.
"""
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _expand(field, lo, hi):
    """Expand one cron field into a sorted list of ints, or None for '*'."""
    if field == "*":
        return None
    out = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/")
            step = int(s)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-"))
        else:
            a = b = int(part)
        out.update(range(a, b + 1, step))
    return sorted(out)


def _to_ist(h, m, tz):
    """Convert h:m in tz to IST, using today's date (so DST in Los Angeles is applied)."""
    src = datetime.combine(date.today(), datetime.min.time()).replace(hour=h, minute=m, tzinfo=ZoneInfo(tz))
    d = src.astimezone(IST)
    shift = (d.date() - src.date()).days
    return d.hour, d.minute, shift


def describe(schedule, predecessors=None):
    """Return (summary, detail) for a task schedule."""
    if not schedule:
        if predecessors:
            names = ", ".join(p.split(".")[-1] for p in predecessors)
            return ("After " + names, "Runs as a child step: starts automatically when %s finishes successfully." % names)
        return ("Manual only", "No schedule. Runs only when someone executes it.")
    s = schedule.strip()
    up = s.upper()
    if up.endswith("MINUTE") or up.endswith("MINUTES"):
        n = int(s.split()[0])
        if n % 60 == 0:
            h = n // 60
            return ("Every hour" if h == 1 else "Every %d hours" % h, "Interval schedule (%s), counted from when the task was last resumed, not clock-aligned." % s)
        return ("Every %d min" % n, "Interval schedule (%s), counted from when the task was last resumed, not clock-aligned." % s)
    if not up.startswith("USING CRON"):
        return (s, s)
    parts = s.split()[2:]
    if len(parts) < 6:
        return (s, s)
    mi, hr, dom, mon, dow, tz = parts[:6]
    raw = "Cron %s %s %s %s %s in %s" % (mi, hr, dom, mon, dow, tz)
    try:
        mins = _expand(mi, 0, 59)
        hrs = _expand(hr, 0, 23)
        doms = _expand(dom, 1, 31)
        dows = _expand(dow.replace("7", "0"), 0, 6)
    except ValueError:
        return (raw, raw)

    if hrs is None:
        if mins and len(mins) == 1:
            off = _to_ist(0, mins[0], tz)[1]
            summary = "Hourly at :%02d" % off
        else:
            summary = "Every minute" if mins is None else "Hourly at " + ", ".join(":%02d" % m for m in mins)
    else:
        times, shifted = [], False
        for h in hrs:
            for m in (mins or [0]):
                ih, im, sh = _to_ist(h, m, tz)
                shifted = shifted or sh != 0
                times.append((ih, im))
        times = sorted(set(times))
        if len(times) > 6:
            tlist = "%d times a day (%02d:%02d to %02d:%02d)" % (len(times), times[0][0], times[0][1], times[-1][0], times[-1][1])
        else:
            tlist = ", ".join("%02d:%02d" % t for t in times)
        if doms is None and dows is None:
            summary = "Daily " + tlist + " IST"
        elif dows is not None and doms is None:
            summary = "/".join(DOW[d] for d in dows) + " " + tlist + " IST"
            if shifted:
                summary += " (+1 day in IST)"
        else:
            summary = "Day %s of month, %s IST" % (",".join(str(d) for d in doms), tlist)
    tzn = {"Asia/Kolkata": "IST", "UTC": "UTC", "America/Los_Angeles": "US Pacific"}.get(tz, tz)
    detail = raw + (". Written in %s; shown here converted to IST." % tzn if tz != "Asia/Kolkata" else ".")
    return (summary, detail)
