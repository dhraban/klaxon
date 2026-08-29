# Klaxon deployment

Klaxon runs on GitHub Actions every 15 minutes at minutes 7, 22, 37, and 52 of
each hour. Each run checks the newest BOHECO post first. If that ID is new, it
continues through older posts until it reaches an already processed ID. A
five-post safety cap bounds the work, and new posts are handled oldest-to-newest.

At 6:15 AM USA Central Standard Time (12:15 UTC), a separate health-check run
audits the previous day's sweep counter. It expects 96 scheduled sweeps and records a degraded
result when the count is more than 10% below that target. It does not send a
separate health-warning Pushover message; the counter is then reset for the next
audit period. It also writes a structured
`watcher_health.json` result to the Actions cache for the next morning brief.
Manual workflow runs do not affect the counter.

## One-time GitHub setup

1. Create a public repository named `klaxon`.
2. Upload this project, excluding files covered by `.gitignore`.
3. In the repository, open **Settings → Secrets and variables → Actions**.
4. Create these two repository secrets:
   - `PUSHOVER_USER_KEY`
   - `PUSHOVER_APPLICATION_TOKEN`
5. Open **Actions → Klaxon Facebook monitor → Run workflow** for the first test.

The workflow carries the five post IDs already processed during local testing.
After the first run, its SQLite history is restored and saved using GitHub's
Actions cache. The database continues to retain only the newest 20 IDs.

## Cost and reliability

Standard GitHub-hosted runners are free for public repositories. GitHub's
scheduled jobs are approximate: runs can be delayed or, under unusually high
load, dropped. A public repository's scheduled workflows are automatically
disabled after 60 days without repository activity, so check the Actions page
occasionally and re-enable the workflow if GitHub pauses it.

The daily times currently use fixed UTC-6 (USA Central Standard Time) cron
values because GitHub Actions schedules are UTC-based and do not adjust for
daylight saving time. During Central Daylight Time, each displayed local time
will therefore be one hour later until the cron values are changed.

Pushover credentials are encrypted repository secrets and are not committed to
the repository. The Klaxon source, location keywords, and seed post IDs are
public in this free configuration.

PAGASA cyclone monitoring
-------------------------
The PAGASA detector runs separately every day at 5:55 AM USA Central Standard
Time (11:55 UTC). It checks the official tropical-cyclone bulletin and records
structured
JSON state for the later morning brief without sending a routine Pushover
message. When a cyclone is inside PAR, it turns
on the separate elevated monitor. That monitor wakes on a fixed hourly GitHub
schedule, but it makes no PAGASA request unless the persisted state says
elevated monitoring is enabled and the next three-hour or hourly check is due.

The daily detector disables elevated monitoring when no active cyclone remains
inside PAR. GitHub Actions schedules are static, so the hourly monitor job can
still appear in the Actions history as a no-op; only its PAGASA fetch is
suppressed outside active monitoring. Standalone PAGASA pushes are sent only for
actual monitor checks that still report an active cyclone inside PAR. Elevated
three-hour checks use priority 0; only an hourly check due to an official Bohol
wind signal uses priority 1. Quiet-day and out-of-PAR results are state-only.

Alerts also calculate the earliest future PAGASA forecast position within 250
km of the Dauis, Bohol reference point, using the bulletin's timestamp and
coordinates only. The wording is an approximate time and distance to the
forecast center; it does not claim landfall or arrival.

The one-shot `--test-pushover` mode is reserved for manual testing. It sends
one clearly labeled priority-0 sample and does not change monitor state.

Morning brief
-------------
The `Klaxon morning brief` workflow runs at 6:30 AM USA Central Standard Time
(12:30 UTC), after the daily PAGASA detector at 5:55 AM, the 6:15 AM health
audit, and the 6:22 AM Facebook sweep. The source workflows cache their
structured results. The brief also restores the latest successful watcher-health
result; it does not re-scrape Facebook or PAGASA's cyclone bulletin.

Each Facebook sweep also upserts recognized scheduled-outage notices into the
durable `scheduled_outages` table in `klaxon_state.sqlite3`. The brief checks
all retained notices against the current Philippine calendar date, filters to
the configured Dauis/Mayacabac area, excludes non-overlapping dates, and
deduplicates repeated notices with the same outage window. A notice whose date
cannot be read is retained and reported as date/time uncertain.

It sends one Pushover priority-0 message titled `Morning brief`. HTML is used
only to bold the date and the four section headings; the values remain plain
readable text. The message begins with the Bohol weekday/date, then contains
Power today, Cyclone status, Weather, and System health sections. System health
reports the latest completed watcher audit, including a clear degraded warning
or an unavailable message when the audit result is missing or invalid. No
separate health-warning push is sent. Cyclone Status remains present every day,
including the quiet result from PAGASA. Weather
is read from PAGASA's official Selected Tourist Areas Bohol forecast, with
Celsius retained and Fahrenheit calculated. Missing weather data is reported
as unavailable rather than invented.
