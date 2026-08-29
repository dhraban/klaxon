# Klaxon durable state

This branch is the public, durable state store for Klaxon's free GitHub Actions
deployment. It contains only non-secret operational data:

- processed BOHECO post IDs and retained scheduled-outage data;
- latest PAGASA monitor and daily-detector results; and
- latest watcher-health result.

It never contains Pushover credentials, GitHub secrets, or Facebook login
credentials. The workflows update files only when their meaningful contents
change; Actions cache is retained only as a fast fallback and for the
high-frequency watcher-run counter.
