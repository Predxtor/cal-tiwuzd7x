#!/usr/bin/env python3
"""Rebuild cal.ics from Sussex Canvas. Needs CANVAS_TOKEN in the environment.

Writes cal.ics (deadlines, bills, weekday study blocks) and canvas.json
(open assignments, for the hub). Standard library only.
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

    open_items = [it for it in items if not it["submitted"] and datetime.fromisoformat(it["due"]).date() >= today]
    d, k = today, 0
    while open_items and d <= max(datetime.fromisoformat(i["due"]).date() for i in open_items):
        if d.weekday() < 5:
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
