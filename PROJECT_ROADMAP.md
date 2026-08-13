# Klaxon project roadmap

Last updated: 2026-08-13

## Accepted and complete

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

## Planned reliability feature

### Watcher run-count warning

Once per day or every other day, compare the number of completed Klaxon runs
with the expected number. Send a normal warning if the actual count is more
than 10% below the expected count.

Frequency still to decide: daily or every other day.

## Phase 3

- Read and classify email contents
- Add Messages/iMessage or other approved input sources

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
