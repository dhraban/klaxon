# Klaxon deployment

Klaxon is prepared to run on GitHub Actions every 15 minutes at minutes 7, 22,
37, and 52 of each hour.

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
