# Moodle Assignment Dashboard

A small script that reads your Moodle calendar feed and turns it into a
clean HTML dashboard showing every assignment due date across your courses,
sorted soonest-first and color-coded by urgency.

## Files

- `moodle_dashboard.py` — the script. Run this to (re)generate the dashboard.
- `config.json` — **your personal settings**, already filled in with the
  calendar feed URL we pulled from your Moodle account. This URL contains a
  private access token, so don't share this file or post it anywhere public.
- `config.example.json` — a blank template, useful if you ever need to
  regenerate the URL (e.g. if you think the token leaked) and swap it in.
- `dashboard.html` — the generated dashboard. Open this in any browser.
- `completed.json` — tracks which assignments you've marked done (see
  "Marking tasks done" below). Created automatically the first time you use
  `--done`; safe to delete if you want to reset everything to not-done.

## Requirements

Python 3.9+ (has `zoneinfo` built in). No extra packages needed beyond the
standard library.

## Running it

```
python3 moodle_dashboard.py
```

This fetches the latest data from Moodle and rewrites `dashboard.html`.
Open (or refresh) that file in your browser to see the current list.

## Keeping it up to date

Run the script again anytime you want fresh data — before checking what's
due, for example. If you want it to update automatically:

- **macOS/Linux (cron):** run `crontab -e` and add a line like:
  ```
  0 7 * * * cd /path/to/this/folder && /usr/bin/python3 moodle_dashboard.py
  ```
  (updates every day at 7am)
- **Windows (Task Scheduler):** create a daily trigger that runs
  `python moodle_dashboard.py` with the "Start in" folder set to this
  folder.

## How it decides what's an "assignment"

Your Moodle calendar feed includes more than assignment due dates — it also
has attendance-taking sessions and regular class sessions. The script keeps
events whose title matches Moodle's standard "due date" phrasing (e.g.
Hebrew "יש להגיש..." / English "... is due") and puts everything else in a
collapsed "Other course events" section at the bottom, so nothing is
hidden, but assignments are what you see first.

If a course uses different wording and its assignments end up in the
"Other" section instead, open `moodle_dashboard.py` and add the relevant
phrase to the `ASSIGNMENT_KEYWORDS` list near the top of the file.

## Important: it doesn't know if you already submitted

The calendar feed only contains due dates — it says nothing about whether
you've actually turned something in. So an assignment whose deadline has
passed shows up under "Past deadlines" (a neutral, collapsed section), not
as "Overdue" — it may well already be submitted and graded. Check Moodle
itself if you're unsure of your submission status on something.

## Marking tasks done

Every assignment shown has a small `#id` under its due date. To mark one
done:

```
python3 moodle_dashboard.py --done 189659
python3 moodle_dashboard.py --done "תרגיל בית 1"
```

You can match by id, or by any text that appears in the title or course
name. If your text matches more than one assignment, the script lists all
matches with their ids instead of guessing — run it again with the id to
be precise. Done items move to a collapsed "Completed" section with a
strikethrough and don't count toward the stats at the top.

To undo it:

```
python3 moodle_dashboard.py --undone 189659
```

Both flags also refresh the dashboard with the latest due dates in the same
run, so you don't need to run the script twice. Completed state is stored
in `completed.json` and persists across refreshes until you undo it (or
delete the file to reset everything).

## If the feed ever stops working

Moodle calendar export tokens don't normally expire, but if this ever
breaks, regenerate the URL:
1. Go to `https://el1.netanya.ac.il/calendar/export.php`
2. Choose "All events", a wide custom date range, click
   "Get calendar URL", copy it.
3. Paste it into `config.json` as the `ics_url` value.
