KLAXON — MANUAL FACEBOOK ACCESS TEST

What this does
--------------
This Phase 1 tester opens a temporary, anonymous Chrome browser, retrieves the
public BOHECO Facebook page, and saves recent posts as latest_post.json. The
alert check starts with the newest post. If its ID is new, the check continues
through older posts until it reaches an already processed ID, with a five-post
safety cap.

It extracts:
- A stable post ID and post URL
- The published timestamp
- The complete Facebook caption
- Attached photo IDs and Facebook's image accessibility text
- Whether the post contains outage wording and affects Dauis or Mayacabac

What this does not do
---------------------
- It does not log into Facebook.
- It does not use or store Facebook credentials.
- It does not schedule itself.
- It sends no Pushover notification unless a Pushover test or send option is
  deliberately selected.
- It does not modify Facebook or any other external account.

How to run it
-------------
Double-click "Run Klaxon Facebook Test.command".

The result will be saved as "latest_post.json" in this folder. Keep the
Terminal window open if the test reports an error so the message can be
inspected.

Location settings
-----------------
The editable settings are in "location_config.json". They currently record:
- Province: Bohol
- Municipality: Dauis
- Barangay: Mayacabac

The broad province name "Bohol" is not a matching keyword. It appears in the
BOHECO page name and would incorrectly make nearly every post location-relevant.

Filter proof
------------
Double-click "Run Location Filter Tests.command" to run safe sample tests:
- A Dauis-wide outage must produce ALERT even if Mayacabac is not named.
- An outage naming Mayacabac must also produce ALERT.
- An outage in unrelated municipalities must produce NO ALERT.

The detailed result is saved as "filter_test_results.json". These tests do not
contact Facebook and do not send a Pushover notification.

Pushover alert levels
---------------------
- A scheduled outage affecting Dauis or Mayacabac uses priority 0.
- An emergency outage affecting Dauis or Mayacabac uses priority 1.
- Both levels use the same Klaxon application and API Token.

Pushover setup
--------------
The User Key and Klaxon API Token are kept in the local file
"pushover_credentials.json". Codex created this directly from the Pushover
dashboard. The file is excluded by .gitignore and should not be shared.

After setup, double-click "Test Klaxon Pushover.command". It sends one normal
priority 0 connection test through the Klaxon application. It does not contact
Facebook.

The ordinary "Run Klaxon Facebook Test.command" remains read-only and does not
send a notification. The Python tester only sends when explicitly run with the
--send-pushover option, and then only for a qualifying outage.

Manual alert check
------------------
Double-click "Run Klaxon Alert Check.command" to run the complete manual path
over the latest five posts:

Facebook post -> outage/location rules -> duplicate check -> Pushover

The check records every examined post ID in the local SQLite database
"klaxon_state.sqlite3". Running the check again for the same post will not send
a second alert. The database also records whether the post was ignored or sent,
its classification, priority, and delivery time. The ordinary read-only
Facebook test does not change this duplicate state.

The database retains only the 20 newest post IDs. Whenever a new ID is added,
older records beyond that limit are deleted oldest-first.

The first database run automatically imports IDs from the earlier
"processed_posts.json" prototype so previous alerts remain protected.

Automated deployment
--------------------
The project is prepared for a free GitHub Actions deployment. Every 15 minutes,
it adaptively checks up to five BOHECO posts and stops when it reaches an ID in
the duplicate history. New posts are processed oldest-to-newest, and duplicate
state is kept in the Actions cache. See "DEPLOYMENT.md" for setup and free-tier
tradeoffs.

Project roadmap
---------------
The accepted feature set, current work, and later phases are recorded in
"PROJECT_ROADMAP.md".

PAGASA cyclone checker
----------------------
The daily detector runs at 4:40 AM Bohol time and writes
"pagasa_daily_detector.json". It checks the official PAGASA tropical-cyclone
bulletin and records the storm name, PAR status, issue time, forecast
positions, and official Bohol wind-signal status. It does not send a routine
Pushover message. A separate elevated monitor
uses the persisted state for three-hour checks while a cyclone is in PAR and
hourly checks when Bohol is under an official wind signal. Its GitHub schedule
is fixed hourly, but it does not fetch PAGASA while elevated monitoring is off
or while its next check is not due. Elevated three-hour checks send priority 0
updates, and hourly checks caused by an official Bohol wind signal send priority
1. The daily detector writes its result for the morning brief without sending
its own routine update. Standalone PAGASA pushes are suppressed unless an actual
monitor check reports an active cyclone inside PAR; quiet-day and out-of-PAR
results are state-only.

When PAGASA provides timestamped forecast positions, the alert also reports
the earliest forecast center within 250 km of the Dauis, Bohol reference point
as an approximate relative time and distance. If none qualifies, it says so;
it does not infer landfall or arrival.

For a one-time end-to-end sample notification, run
`python3 pagasa_cyclone_check.py --test-pushover`. It sends one priority-0
message labeled "TEST ONLY" with representative active-in-PAR content and
does not fetch PAGASA or modify monitor state.

Morning brief
-------------
The `Klaxon morning brief` workflow sends one readable Pushover priority-0
message at 5:15 AM Bohol time. It runs after the 4:40 AM PAGASA detector and
the 5:07 AM Facebook sweep, using their cached structured results and the
latest successfully completed watcher health audit. The brief contains Power
today, Cyclone status, Weather, and System health sections in that order.
Pushover HTML is enabled only to bold the date and those four headings; the
values remain normal readable text. If no valid health-audit result is
available, the brief says so rather than claiming the system is healthy. The
Cyclone status section remains in the daily brief even when PAGASA reports no
active cyclone in PAR; no separate health-warning push is sent either.

Recognized scheduled-outage notices are also stored in durable SQLite state.
The brief evaluates all retained notices against today's Philippine calendar
date, filters to Dauis/Mayacabac, excludes past and future-only notices, and
deduplicates reposts or wording updates for the same date/time window. Missing
dates are shown as uncertain rather than treated as proof that there is no
outage.

Weather comes from PAGASA's official Selected Tourist Areas page for Bohol.
The first forecast day's official Celsius range is retained and converted to
Fahrenheit in the compact form `conditions; 73–82°F (23–28°C).` If PAGASA's
weather page is unavailable or incomplete, the brief says so without guessing.
