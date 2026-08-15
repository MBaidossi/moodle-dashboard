#!/usr/bin/env python3
"""
Moodle Assignment Dashboard
----------------------------
Fetches your Moodle calendar export feed (.ics) and builds a self-contained
HTML dashboard listing your assignment due dates across all courses.

SETUP (one-time):
1. Put your personal Moodle calendar export URL in config.json (see
   config.example.json for the format). You get this URL from your Moodle
   site under Calendar > Export (or by going directly to
   <your-moodle>/calendar/export.php), choosing "All events", a wide time
   range, and clicking "Get calendar URL".

   IMPORTANT: That URL contains a private access token - anyone who has it
   can see your calendar. Keep config.json out of version control / don't
   share it.

2. Install the one dependency this script needs:
       pip install requests

USAGE:
    python3 moodle_dashboard.py

    This fetches the latest data and writes dashboard.html in the same
    folder. Open that file in your browser (or re-run this script anytime
    to refresh it with new due dates).

    python3 moodle_dashboard.py --done "תרגיל בית 1"
    python3 moodle_dashboard.py --done 189659

    Marks an assignment as done (matches against its id, title, or course
    name) and re-renders the dashboard with it moved to a "Completed"
    section. Use --undone the same way to reverse it.

You can also schedule this to run automatically:
  - macOS/Linux: add a cron entry, e.g. `0 7 * * * cd /path/to/folder && python3 moodle_dashboard.py`
  - Windows: use Task Scheduler to run `python moodle_dashboard.py` daily.
"""

import argparse
import json
import os
import re
import sys
import html as htmlmod
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Jerusalem"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
OUTPUT_PATH = os.path.join(SCRIPT_DIR, "dashboard.html")
COMPLETED_PATH = os.path.join(SCRIPT_DIR, "completed.json")

# Keywords used to classify calendar events. Moodle's Hebrew UI labels an
# assignment/quiz "due" event with a summary that starts with one of these
# phrases. Add more phrases here if your course events use different wording
# (e.g. English-language courses use "is due").
ASSIGNMENT_KEYWORDS = [
    "יש להגיש",      # "you need to submit ..." (assignment/quiz due date)
    "מועד הגשה",      # "submission deadline"
    "בוחן",           # "quiz/test"
    "מבחן",           # "exam"
    "is due",         # English Moodle default phrasing
    "due:",
    "quiz closes",
    "quiz opens",
]
EXCLUDE_KEYWORDS = [
    "נוכחות",         # attendance-taking events, not assignments
]


def load_completed():
    """Returns the set of assignment UIDs the user has marked done."""
    if not os.path.exists(COMPLETED_PATH):
        return set()
    with open(COMPLETED_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return set(data.get("done", []))


def save_completed(completed_uids):
    with open(COMPLETED_PATH, "w", encoding="utf-8") as f:
        json.dump({"done": sorted(completed_uids)}, f, ensure_ascii=False, indent=2)


def find_matches(assignments, query):
    """Match an assignment by exact uid, or case-insensitive substring in
    its title or course name."""
    query_lower = query.strip().lower()
    exact_uid = [a for a in assignments if a["uid"] == query.strip()]
    if exact_uid:
        return exact_uid
    return [
        a for a in assignments
        if query_lower in a["title"].lower() or query_lower in a["course"].lower()
    ]


def load_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"Missing {CONFIG_PATH}.")
        print("Create it (see config.example.json) with your Moodle calendar export URL.")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_ics(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (moodle-dashboard script)"})
    with urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def unfold_lines(ics_text):
    """iCalendar lines can be 'folded' across multiple physical lines, where
    a continuation line starts with a space or tab. Undo that."""
    lines = ics_text.replace("\r\n", "\n").split("\n")
    unfolded = []
    for line in lines:
        if line.startswith(" ") or line.startswith("\t"):
            if unfolded:
                unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def unescape_ics_text(value):
    return (
        value.replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\n", " ")
        .replace("\\N", " ")
        .replace("\\\\", "\\")
    )


def parse_events(ics_text):
    lines = unfold_lines(ics_text)
    events = []
    current = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            key_part, _, value = line.partition(":")
            key = key_part.split(";")[0]  # strip params like ;VALUE=DATE
            current[key] = unescape_ics_text(value)
    return events


def parse_ics_datetime(value):
    """Parse a DTSTART/DTEND value like '20260810T205900Z' into an aware
    UTC datetime. Falls back to naive parsing for date-only values."""
    value = value.strip()
    try:
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        if "T" in value:
            return datetime.strptime(value, "%Y%m%dT%H%M%S")
        return datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return None


def classify(summary):
    text = summary.lower()
    if any(kw.lower() in text for kw in EXCLUDE_KEYWORDS):
        return "other"
    if any(kw in summary for kw in ASSIGNMENT_KEYWORDS) or any(
        kw.lower() in text for kw in ASSIGNMENT_KEYWORDS
    ):
        return "assignment"
    return "other"


def extract_title(summary):
    """Pull the assignment name out of a summary like:
    "יש להגיש את 'תרגיל בית 1'" -> "תרגיל בית 1" """
    m = re.search(r"['\"‘’](.+?)['\"‘’]", summary)
    if m:
        return m.group(1)
    return summary


def build_items(events):
    assignments = []
    other = []
    for ev in events:
        summary = ev.get("SUMMARY", "").strip()
        dtstart = ev.get("DTSTART", "")
        dt = parse_ics_datetime(dtstart)
        course = ev.get("CATEGORIES", "").strip() or "Uncategorized"
        item = {
            "title": extract_title(summary) if summary else "(untitled)",
            "raw_summary": summary,
            "course": course,
            "due": dt,
            "uid": ev.get("UID", ""),
        }
        if classify(summary) == "assignment":
            assignments.append(item)
        else:
            other.append(item)
    assignments.sort(key=lambda i: i["due"] or datetime.max.replace(tzinfo=timezone.utc))
    other.sort(key=lambda i: i["due"] or datetime.max.replace(tzinfo=timezone.utc))
    return assignments, other


def fmt_dt(dt, tzinfo):
    if dt is None:
        return "Unknown date"
    local = dt.astimezone(tzinfo)
    return local.strftime("%a, %d %b %Y • %H:%M")


def status_bucket(dt, now):
    if dt is None:
        return "unknown"
    delta = dt - now
    if delta.total_seconds() < 0:
        return "overdue"
    if delta.total_seconds() < 2 * 24 * 3600:
        return "soon"
    if delta.total_seconds() < 7 * 24 * 3600:
        return "week"
    return "later"


STATUS_LABEL = {
    "overdue": "Past deadline",
    "soon": "Due very soon",
    "week": "Due this week",
    "later": "Upcoming",
    "unknown": "Date unknown",
}

STATUS_COLOR = {
    "overdue": "#6b7280",  # neutral gray, not red — a past deadline does NOT
                            # mean you missed it, see note in render_html()
    "soon": "#ea580c",
    "week": "#ca8a04",
    "later": "#2563a0",
    "unknown": "#6b7280",
}


def render_html(assignments, other, generated_at, tzinfo, completed_uids=None):
    now = datetime.now(timezone.utc)
    completed_uids = completed_uids or set()

    def row(item, done=False):
        bucket = status_bucket(item["due"], now)
        color = "#16a34a" if done else STATUS_COLOR[bucket]
        label = "Done" if done else STATUS_LABEL[bucket]
        title = htmlmod.escape(item["title"])
        course = htmlmod.escape(item["course"])
        due_str = fmt_dt(item["due"], tzinfo)
        title_style = "text-decoration:line-through;color:var(--muted)" if done else ""
        return f"""
        <div class="item" data-bucket="{bucket}">
          <div class="item-bar" style="background:{color}"></div>
          <div class="item-body">
            <div class="item-title" style="{title_style}">{title}</div>
            <div class="item-course">{course}</div>
          </div>
          <div class="item-meta">
            <span class="badge" style="background:{color}22;color:{color}">{label}</span>
            <span class="due-date">{due_str}</span>
            <span class="item-id">#{htmlmod.escape(item['uid'])}</span>
          </div>
        </div>"""

    active = [a for a in assignments if a["uid"] not in completed_uids]
    done_items = [a for a in assignments if a["uid"] in completed_uids]

    upcoming = [a for a in active if status_bucket(a["due"], now) != "overdue"]
    past = [a for a in active if status_bucket(a["due"], now) == "overdue"]

    upcoming_rows = "\n".join(row(a) for a in upcoming) or '<p class="empty">No upcoming assignment deadlines in the feed.</p>'
    past_rows = "\n".join(row(a) for a in past) or '<p class="empty">None.</p>'
    other_rows = "\n".join(row(o) for o in other) or '<p class="empty">Nothing else in range.</p>'
    done_rows = "\n".join(row(a, done=True) for a in done_items) or '<p class="empty">Nothing marked done yet.</p>'

    counts = {"overdue": 0, "soon": 0, "week": 0, "later": 0, "unknown": 0}
    for a in active:
        counts[status_bucket(a["due"], now)] += 1

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Assignment Dashboard</title>
<style>
  :root {{
    --bg: #f7f8fa; --card: #ffffff; --border: #e5e7eb;
    --text: #1f2430; --muted: #6b7280;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#14161a; --card:#1d2026; --border:#2a2e37; --text:#e8eaee; --muted:#9aa0ab; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px 16px; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
  }}
  .wrap {{ max-width: 780px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .subtitle {{ color: var(--muted); font-size: 13px; margin-bottom: 24px; }}
  .summary {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 28px; }}
  .stat {{
    flex: 1; min-width: 110px; background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 12px 14px;
  }}
  .stat .n {{ font-size: 22px; font-weight: 700; }}
  .stat .l {{ font-size: 12px; color: var(--muted); }}
  h2 {{ font-size: 15px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em;
       margin: 28px 0 10px; }}
  .item {{
    display: flex; align-items: stretch; background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; margin-bottom: 8px; overflow: hidden;
  }}
  .item-bar {{ width: 4px; flex-shrink: 0; }}
  .item-body {{ padding: 12px 14px; flex: 1; min-width: 0; }}
  .item-title {{ font-weight: 600; font-size: 14px; }}
  .item-course {{ font-size: 12px; color: var(--muted); margin-top: 2px; }}
  .item-meta {{ padding: 12px 14px; text-align: right; display: flex; flex-direction: column;
                justify-content: center; gap: 4px; white-space: nowrap; }}
  .badge {{ font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: 999px; }}
  .due-date {{ font-size: 12px; color: var(--muted); }}
  .item-id {{ font-size: 10px; color: var(--muted); font-family: ui-monospace, Menlo, Consolas, monospace; }}
  .empty {{ color: var(--muted); font-size: 13px; }}
  details summary {{ cursor: pointer; color: var(--muted); font-size: 13px; margin-top: 8px; }}
  .note {{
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 10px 14px; font-size: 12px; color: var(--muted); margin: 6px 0 20px;
  }}
  footer {{ margin-top: 32px; font-size: 12px; color: var(--muted); text-align: center; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Assignment Dashboard</h1>
  <div class="subtitle">Generated {generated_at}</div>

  <div class="summary">
    <div class="stat"><div class="n" style="color:{STATUS_COLOR['soon']}">{counts['soon']}</div><div class="l">Due &lt; 48h</div></div>
    <div class="stat"><div class="n" style="color:{STATUS_COLOR['week']}">{counts['week']}</div><div class="l">This week</div></div>
    <div class="stat"><div class="n" style="color:{STATUS_COLOR['later']}">{counts['later']}</div><div class="l">Later</div></div>
    <div class="stat"><div class="n" style="color:{STATUS_COLOR['overdue']}">{counts['overdue']}</div><div class="l">Past deadline</div></div>
  </div>

  <div class="note">
    This only reflects <strong>due dates</strong> from your Moodle calendar feed &mdash;
    it has no way to know whether you already submitted something. A "Past deadline"
    item may well be done already; check Moodle directly if you're not sure.
    To mark something done yourself, run
    <code>python3 moodle_dashboard.py --done &lt;id or title&gt;</code> using the
    <code>#id</code> shown on each item below.
  </div>

  <h2>Upcoming</h2>
  {upcoming_rows}

  <details>
    <summary>Past deadlines ({len(past)}) &mdash; already passed, submission status unknown</summary>
    {past_rows}
  </details>

  <details>
    <summary>Completed ({len(done_items)})</summary>
    {done_rows}
  </details>

  <details>
    <summary>Other course events ({len(other)}) &mdash; class sessions, attendance, etc.</summary>
    {other_rows}
  </details>

  <footer>Re-run moodle_dashboard.py to refresh assignment due dates from Moodle.</footer>
</div>
</body>
</html>"""


def apply_done_flag(assignments, query, completed_uids):
    matches = find_matches(assignments, query)
    if not matches:
        print(f'No assignment matches "{query}". Nothing changed.')
        return completed_uids
    if len(matches) > 1:
        print(f'"{query}" matches {len(matches)} assignments — be more specific, or use the id:')
        for m in matches:
            print(f"  #{m['uid']}  {m['title']}  ({m['course']})")
        return completed_uids
    match = matches[0]
    completed_uids = set(completed_uids)
    completed_uids.add(match["uid"])
    save_completed(completed_uids)
    print(f"Marked done: {match['title']} ({match['course']})")
    return completed_uids


def apply_undone_flag(assignments, query, completed_uids):
    matches = find_matches(assignments, query)
    matches = [m for m in matches if m["uid"] in completed_uids]
    if not matches:
        print(f'No completed assignment matches "{query}". Nothing changed.')
        return completed_uids
    if len(matches) > 1:
        print(f'"{query}" matches {len(matches)} completed assignments — be more specific, or use the id:')
        for m in matches:
            print(f"  #{m['uid']}  {m['title']}  ({m['course']})")
        return completed_uids
    match = matches[0]
    completed_uids = set(completed_uids)
    completed_uids.discard(match["uid"])
    save_completed(completed_uids)
    print(f"Marked not done: {match['title']} ({match['course']})")
    return completed_uids


def main():
    parser = argparse.ArgumentParser(description="Moodle assignment dashboard")
    parser.add_argument("--done", metavar="TEXT", help="Mark an assignment done (match by id, title, or course)")
    parser.add_argument("--undone", metavar="TEXT", help="Unmark an assignment as done")
    args = parser.parse_args()

    config = load_config()
    url = config.get("ics_url")
    if not url:
        print("config.json is missing the 'ics_url' field.")
        sys.exit(1)

    print("Fetching calendar feed...")
    ics_text = fetch_ics(url)
    events = parse_events(ics_text)
    print(f"Parsed {len(events)} calendar events.")

    assignments, other = build_items(events)
    print(f"  -> {len(assignments)} classified as assignments, {len(other)} other events.")

    completed_uids = load_completed()
    if args.done:
        completed_uids = apply_done_flag(assignments, args.done, completed_uids)
    if args.undone:
        completed_uids = apply_undone_flag(assignments, args.undone, completed_uids)

    tz_name = config.get("timezone", DEFAULT_TIMEZONE)
    tzinfo = ZoneInfo(tz_name)
    generated_at = datetime.now(tzinfo).strftime("%a, %d %b %Y %H:%M")
    html_out = render_html(assignments, other, generated_at, tzinfo, completed_uids)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
