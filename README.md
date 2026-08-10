# Instagram Tracker

Monitors an Instagram account's Stories and sends a Discord alert whenever a Story links
to a relevant full-time, entry-level software engineering position.

No Instagram login, no OCR, no browser automation — Stories are read from structured
metadata and job links come from `story_link_stickers`.

## Pipeline

```
Poller -> StorySource -> story processing -> link extraction -> URL normalization
       -> job fetch -> classification -> deduplication -> Discord notification
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in DISCORD_WEBHOOK_URL
```

## Run

```bash
python -m instagram_tracker
```

Run a single poll cycle without looping:

```bash
python -m instagram_tracker --once
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `INSTAGRAM_USERNAME` | `zero2sudo` | Account whose Stories are polled |
| `STORY_PROVIDER` | `igexport` | Story source adapter |
| `POLL_INTERVAL_SECONDS` | `60` | Seconds between polls |
| `PROCESS_EXISTING_STORIES_ON_STARTUP` | `false` | If false, Stories already live on first run are recorded but never notified |
| `DATABASE_PATH` | `./data/job_monitor.db` | SQLite file |
| `DISCORD_WEBHOOK_URL` | — | Discord webhook; required to notify |
| `HEARTBEAT_URL` | — | Optional healthchecks.io-style ping URL; see below |

## Staying alive

A tracker that dies quietly looks exactly like an account that stopped posting — both
are an empty Discord channel. Two separate mechanisms cover that.

**Heartbeat.** Set `HEARTBEAT_URL` to a healthchecks.io ping URL. Every successful poll
pings it; every failed poll pings `<url>/fail`. Configure the check's period to a few
minutes above `POLL_INTERVAL_SECONDS` and the external service alerts you when the pings
stop. Ping failures are logged and swallowed, so the monitor can never stop the tracker.

**Supervision.** `deploy/com.arsaljafri.instagram-tracker.plist` runs the poller under
launchd: started at login, restarted if it crashes, logging to `logs/tracker.log`.

```bash
cp deploy/com.arsaljafri.instagram-tracker.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.arsaljafri.instagram-tracker.plist
```

Stop any copy already running in a terminal first — two instances share one SQLite file
and will race each other. To check on it, or to stop it:

```bash
launchctl list | grep instagram-tracker
launchctl unload ~/Library/LaunchAgents/com.arsaljafri.instagram-tracker.plist
```

This fixes reboots and crashes. It does **not** fix sleep: nothing polls while the lid
is shut, and launchd simply resumes the agent on wake. Only an always-on host solves
that; `caffeinate -s` is a stopgap when the laptop is plugged in.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Tests run entirely against fixtures in `fixtures/`; no live network requests.
