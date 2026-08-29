# Klaxon project roadmap

Last updated: 2026-08-13

## Phase 1 — complete

The current Klaxon implementation is accepted as complete in its present form.
This includes:

- Pushover delivery to the iPhone
- Scheduled-outage priority 0 notifications
- Emergency-outage priority 1 notifications
- Email auto-forwarding to Pushover
- Anonymous BOHECO Facebook access
- Adaptive recent-post polling every 15 minutes
- Continued sweeping after a new ID until a processed ID is reached
- Five-post safety cap with oldest-to-newest processing
- Caption and Facebook image-accessibility-text inspection
- Dauis and Mayacabac location matching
- Deterministic outage filtering
- Duplicate suppression with persistent SQLite state
- Retention of only the 20 newest processed post IDs
- Free GitHub Actions hosting
- Encrypted Pushover credentials in GitHub Secrets

There are no partially complete features being tracked.

## Phase 2

### Watcher run-count warning

Once per day at 6:15 AM USA Central Standard Time, compare the number of scheduled
Facebook sweeps with the expected number. Record a degraded result if the
actual count is more than 10% below the expected count, then reset the counter.

The health check is kept separate and its latest result now feeds the overall
daily brief. A degraded result is reported in the brief; it does not generate a
separate routine Pushover warning.

### Natural-disaster warnings

- Hurricane/typhoon warning when a storm enters the Philippine Area of
  Responsibility (PAR) — detector and separate elevated monitor implemented;
  routine daily detector push is consolidated into the morning brief; quiet
  days produce no standalone PAGASA push, and urgent elevated alerts remain
  separate
- Earthquake warning

## Phase 3

- Read and classify email contents
- Add Messages/iMessage or other approved input sources
- Send a daily 6:30 AM USA Central Standard Time Pushover summary of upcoming scheduled
  power outages — morning brief implemented with PAGASA Bohol weather,
  cyclone-status, and system-health sections

## Phase 4

- AI-assisted classification
- Escalation chains
- Long-term searchable event history

## Explicitly out of scope

The following features are not needed:

- External heartbeat monitoring
- Dedicated OCR for outage images
- Feeder, circuit, nearby-area, and alternate-spelling rules
- Signal input
- User feedback and classification correction
- Snoozing or acknowledgment tracking
- Backups and provider redundancy
