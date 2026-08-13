# Klaxon deployment

Klaxon runs on GitHub Actions every 15 minutes at minutes 7, 22, 37, and 52 of
each hour. Each run checks the newest BOHECO post first. If that ID is new, it
continues through older posts until it reaches an already processed ID. A
five-post safety cap bounds the work, and new posts are handled oldest-to-newest.

At 7:15 AM Philippine time, a separate health-check run audits the previous
day's sweep counter. It expects 96 scheduled sweeps and sends a normal Pushover
warning when the count is more than 10% below that target. The counter is then
reset for the next audit period. Manual workflow runs do not affect the counter.

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

Pushover credentials are encrypted repository secrets and are not committed to
the repository. The Klaxon source, location keywords, and seed post IDs are
public in this free configuration.

PAGASA cyclone monitoring
-------------------------
The PAGASA detector runs separately every day at 6:55 AM Bohol time (22:55
UTC). It checks the official tropical-cyclone bulletin and records structured
JSON state for the later morning brief. When a cyclone is inside PAR, it turns
on the separate elevated monitor. That monitor wakes on a fixed hourly GitHub
schedule, but it makes no PAGASA request unless the persisted state says
elevated monitoring is enabled and the next three-hour or hourly check is due.

The daily detector disables elevated monitoring when no active cyclone remains
inside PAR. GitHub Actions schedules are static, so the hourly monitor job can
still appear in the Actions history as a no-op; only its PAGASA fetch is
suppressed outside active monitoring. Each actual detector or monitor fetch
sends through the existing Pushover secrets. Daily and three-hour checks use
priority 0; only an hourly check due to an official Bohol wind signal uses
priority 1.

The one-shot `--test-pushover` mode is reserved for manual testing. It sends
one clearly labeled priority-0 sample and does not change monitor state.
