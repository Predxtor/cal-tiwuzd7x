#!/usr/bin/env python3
"""Rebuild cal.ics from Sussex Canvas. Needs CANVAS_TOKEN in the environment.

Writes cal.ics (deadlines, bills, weekday study blocks) and canvas.json
(open assignments, for the hub). Standard library only.

Study blocks come from week-plan.json when it exists (written by the Sunday
planning routine). Days that file covers use its blocks; every other weekday
falls back to the automatic 11:30 block.
"""
import json, os, sys, urllib.request
from datetime import date, datetime, timedelta, timezone

BASE = "https://canvas.sussex.ac.uk/api/v1"
BILLS = ["2026-11-10"]            # dates only, no amounts (this repo is public)
STUDY_START, STUDY_END = "113000", "130000"   # after gym (9) and meds (11)
LOOKAHEAD_DAYS = 21               # study blocks only for deadlines this close


def get(path):
    out, url = [], BASE + path
    while url:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + os.environ["CANVAS_TOKEN"]})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
            link = r.headers.get("Link", "")
        out.extend(data if isinstance(data, list) else [data])
        url = next((p.split(";")[0].strip("<> ") for p in link.split(",") if 'rel="next"' in p), None)
    return out


def load_plan(path="week-plan.json"):
    """Return (set of covered dates, list of blocks) from the weekly plan, or empty."""
    try:
        with open(path) as f:
            plan = json.load(f)
    except (OSError, ValueError):
        return set(), []
    start, end = date.fromisoformat(plan["week_start"]), date.fromisoformat(plan["week_end"])
    covered = {start + timedelta(i) for i in range((end - start).days + 1)}
    return covered, plan.get("blocks", [])


def ics_text(s):
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def short(name):
    return name.split("[")[0].strip()


def main():
    today = datetime.now(timezone.utc).date()
    courses = get("/courses?enrollment_state=active&per_page=100&include[]=term")
    items = []
    for c in courses:
        if "Teaching" not in ((c.get("term") or {}).get("name") or ""):
            continue
        for a in get(f"/courses/{c['id']}/assignments?per_page=100&include[]=submission"):
            if not a.get("due_at"):
                continue
            due = datetime.fromisoformat(a["due_at"].replace("Z", "+00:00"))
            state = (a.get("submission") or {}).get("workflow_state")
            items.append({"id": a["id"], "module": short(c["name"]), "title": a["name"].strip(),
                          "due": due.isoformat(), "submitted": state in ("submitted", "graded", "pending_review")})
    items.sort(key=lambda x: x["due"])

    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Hub//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
         "X-WR-CALNAME:Uni", "REFRESH-INTERVAL;VALUE=DURATION:PT12H", "X-PUBLISHED-TTL:PT12H"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def ev(uid, summary, start, end, allday=False, alarm=None, desc=""):
        e = ["BEGIN:VEVENT", f"UID:{uid}@cal", f"DTSTAMP:{stamp}", f"SUMMARY:{summary}"]
        e += ([f"DTSTART;VALUE=DATE:{start}", f"DTEND;VALUE=DATE:{end}", "TRANSP:TRANSPARENT"]
              if allday else [f"DTSTART:{start}", f"DTEND:{end}"])
        if desc:
            e.append("DESCRIPTION:" + desc)
        if alarm:
            e += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{summary}", f"TRIGGER:{alarm}", "END:VALARM"]
        L.extend(e + ["END:VEVENT"])

    for it in items:
        due = datetime.fromisoformat(it["due"]).astimezone(timezone.utc)
        if due.date() < today - timedelta(days=7):
            continue
        tag = "DONE" if it["submitted"] else "DUE"
        ev(f"canvas-{it['id']}", f"{tag}: {it['module']} - {it['title']}",
           (due - timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ"), due.strftime("%Y%m%dT%H%M%SZ"),
           alarm=None if it["submitted"] else "-P1D", desc="Submit on Canvas.")

    for b in BILLS:
        d = date.fromisoformat(b)
        if d >= today - timedelta(days=7):
            ev(f"bills-{b}", "Bills due", d.strftime("%Y%m%d"), (d + timedelta(1)).strftime("%Y%m%d"), allday=True, alarm="PT9H")

    covered, plan_blocks = load_plan()
    for b in plan_blocks:
        d = date.fromisoformat(b["date"])
        if d < today or d not in covered:
            continue
        ds = d.strftime("%Y%m%d")
        st, en = b["start"].replace(":", "") + "00", b["end"].replace(":", "") + "00"
        ev(f"plan-{ds}-{st}", ics_text("Study: " + b["title"]), ds + "T" + st, ds + "T" + en,
           alarm="-PT10M", desc=ics_text(b.get("note") or "Do these steps in the hub. Move this block if it clashes."))

    open_items = [it for it in items if not it["submitted"] and datetime.fromisoformat(it["due"]).date() >= today]
    d, k = today, 0
    while open_items and d <= max(datetime.fromisoformat(i["due"]).date() for i in open_items):
        if d.weekday() < 5 and d not in covered:
            cands = [i for i in open_items if d < datetime.fromisoformat(i["due"]).date() <= d + timedelta(LOOKAHEAD_DAYS)]
            if cands:
                pick = cands[0] if k % 3 != 2 or len(cands) == 1 else cands[1]   # nearest deadline gets 2 of every 3 blocks
                ds = d.strftime("%Y%m%d")
                ev(f"study-{ds}", f"Study: {pick['module']} - {pick['title']}", ds + "T" + STUDY_START, ds + "T" + STUDY_END,
                   desc="Do the next step in the hub. Move this block if it clashes.")
                k += 1
        d += timedelta(1)

    L.append("END:VCALENDAR")
    with open("cal.ics", "w", newline="") as f:
        f.write("\r\n".join(L) + "\r\n")
    with open(os.environ.get("CANVAS_JSON", "/tmp/canvas.json"), "w") as f:
        json.dump(open_items, f, indent=1)
    print(f"{len(items)} assignments, {len(open_items)} open")


if __name__ == "__main__":
    if not os.environ.get("CANVAS_TOKEN"):
        sys.exit("CANVAS_TOKEN is not set")
    main()
