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

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Tests run entirely against fixtures in `fixtures/`; no live network requests.
