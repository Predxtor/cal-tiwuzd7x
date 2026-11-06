#!/usr/bin/env python3
"""Read public iCloud calendar links and write upcoming events as JSON for the hub.

Usage: ICAL_URLS="url1 url2" python3 ical_sync.py > /tmp/apple.json
The links and the events are private: never commit them or print them anywhere public.
Handles all-day and timed events, TZID / UTC times, and simple RRULEs
(DAILY / WEEKLY with BYDAY / MONTHLY / YEARLY, INTERVAL, COUNT, UNTIL) plus EXDATE.
Standard library only.
"""
import json, os, re, sys, urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo("Europe/London")
PAST, AHEAD = 1, 62            # days of events to keep around today
DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def fetch(url):
    url = re.sub(r"^webcal:", "https:", url.strip(), flags=re.I)
    req = urllib.request.Request(url, headers={"User-Agent": "hub-calendar-sync"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def props(block):
    out = {}
    for line in re.sub(r"\r?\n[ \t]", "", block).splitlines():
        if ":" not in line:
            continue
        head, val = line.split(":", 1)
        name, *params = head.split(";")
        p = dict(x.split("=", 1) for x in params if "=" in x)
        out.setdefault(name.upper(), []).append((p, val))
    return out


def when(p, v):
    """Return (local datetime or date, all_day)."""
    if p.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", v):
        return date(int(v[:4]), int(v[4:6]), int(v[6:8])), True
    dt = datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    if v.endswith("Z"):
        dt = dt.replace(tzinfo=timezone.utc).astimezone(LOCAL)
    elif p.get("TZID"):
        try:
            dt = dt.replace(tzinfo=ZoneInfo(p["TZID"])).astimezone(LOCAL)
        except Exception:
            pass
    return dt.replace(tzinfo=None), False


def text(v):
    return v.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def occurrences(start, rule, lo, hi, exdates):
    if not rule:
        yield start
        return
    r = dict(x.split("=", 1) for x in rule.split(";") if "=" in x)
    freq, step = r.get("FREQ"), int(r.get("INTERVAL", "1"))
    count = int(r["COUNT"]) if "COUNT" in r else None
    until = None
    if "UNTIL" in r:
        u, _ = when({}, r["UNTIL"])
        until = u if isinstance(u, date) and not isinstance(u, datetime) else u.date()
    sd = start.date() if isinstance(start, datetime) else start
    byday = [DAYS.index(d[-2:]) for d in r.get("BYDAY", "").split(",") if d[-2:] in DAYS] if freq == "WEEKLY" else []
    n, i = 0, 0
    while i < 3000:
        if freq == "DAILY":
            cands = [sd + timedelta(days=i * step)]
        elif freq == "WEEKLY":
            wk = sd - timedelta(days=sd.weekday()) + timedelta(weeks=i * step)
            cands = sorted(wk + timedelta(days=d) for d in (byday or [sd.weekday()]))
        elif freq == "MONTHLY":
            m = sd.month - 1 + i * step
            y, mo = sd.year + m // 12, m % 12 + 1
            try:
                cands = [date(y, mo, sd.day)]
            except ValueError:
                cands = []
        elif freq == "YEARLY":
            try:
                cands = [sd.replace(year=sd.year + i * step)]
            except ValueError:
                cands = []
        else:
            yield start
            return
        i += 1
        for d in cands:
            if d < sd:
                continue
            if (until and d > until) or (count is not None and n >= count) or d > hi:
                return
            n += 1
            occ = datetime.combine(d, start.time()) if isinstance(start, datetime) else d
            key = d
            if key in exdates or d < lo:
                continue
            yield occ


def events(ics, cal_name, lo, hi):
    name = re.search(r"^X-WR-CALNAME:(.*)$", ics, re.M)
    cal = text(name.group(1)) if name else cal_name
    out = []
    for block in ics.split("BEGIN:VEVENT")[1:]:
        block = block.split("END:VEVENT")[0]
        P = props(block)
        if "DTSTART" not in P or "RECURRENCE-ID" in P:
            continue
        if (P.get("STATUS", [({}, "")])[0][1] or "").upper() == "CANCELLED":
            continue
        start, allday = when(*P["DTSTART"][0])
        if "DTEND" in P:
            end, _ = when(*P["DTEND"][0])
        else:
            end = start + timedelta(days=1) if allday else start
        length = end - start
        ex = set()
        for p, v in P.get("EXDATE", []):
            for part in v.split(","):
                d, _ = when(p, part)
                ex.add(d.date() if isinstance(d, datetime) else d)
        rule = P.get("RRULE", [({}, "")])[0][1]
        title = text(P.get("SUMMARY", [({}, "Busy")])[0][1]) or "Busy"
        for occ in occurrences(start, rule, lo, hi, ex):
            fin = occ + length
            if allday:
                last = fin - timedelta(days=1) if fin > occ else occ
                if last < lo:
                    continue
                out.append({"date": occ.isoformat(), "end": last.isoformat(), "start": None, "endTime": None, "title": title, "cal": cal})
            else:
                if fin.date() < lo:
                    continue
                out.append({"date": occ.date().isoformat(), "end": fin.date().isoformat(), "start": occ.strftime("%H:%M"),
                            "endTime": fin.strftime("%H:%M") if fin.date() == occ.date() else None, "title": title, "cal": cal})
    return out


def main():
    urls = os.environ.get("ICAL_URLS", "").split()
    today = datetime.now(LOCAL).date()
    lo, hi = today - timedelta(days=PAST), today + timedelta(days=AHEAD)
    items, failed = [], 0
    for k, u in enumerate(urls):
        try:
            items += events(fetch(u), f"Calendar {k + 1}", lo, hi)
        except Exception as e:
            failed += 1
            print(f"calendar {k + 1} could not be read: {type(e).__name__}", file=sys.stderr)
    items.sort(key=lambda e: (e["date"], e["start"] or ""))
    json.dump({"items": items[:600], "failed": failed}, sys.stdout)
    print(f"{len(items)} events from {len(urls) - failed} of {len(urls)} calendars", file=sys.stderr)


if __name__ == "__main__":
    main()
