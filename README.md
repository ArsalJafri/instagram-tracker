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
| `STORY_PROVIDER` | `igexport` | Story source adapter(s), comma-separated |
| `BIO_POLL_INTERVAL_SECONDS` | `600` | Minimum gap between direct Instagram profile fetches |
| `POLL_INTERVAL_SECONDS` | `60` | Seconds between polls |
| `PROCESS_EXISTING_STORIES_ON_STARTUP` | `false` | If false, Stories already live on first run are recorded but never notified |
| `DATABASE_PATH` | `./data/job_monitor.db` | SQLite file |
| `DISCORD_WEBHOOK_URL` | — | Discord webhook; required to notify |
| `HEARTBEAT_URL` | — | Optional healthchecks.io-style ping URL; see below |

## Story sources

`STORY_PROVIDER` accepts several providers, comma-separated. They run together on every
poll and a partial failure degrades rather than stopping detection — only a total failure
raises. Links are deduplicated on `canonical_url`, so overlap never double-notifies.

| Provider | Reads | Notes |
| --- | --- | --- |
| `igexport` | igexport.com Story feed | Complete, but a third-party dependency |
| `instagram_bio` | Instagram's public profile endpoint | No auth, no third party; **partial** |

`instagram_bio` needs no session because profile metadata is public — Story *content* is
not, which is why it cannot replace `igexport`. The account's `bio_links` carry the job
link it is currently promoting, which is a curated subset of what goes out on Stories.
Its value is redundancy.

It throttles itself to `BIO_POLL_INTERVAL_SECONDS` regardless of the poll interval, since
polling Instagram directly from a residential IP at Story cadence invites a soft block.

Bio links have no publish timestamp, so their synthetic Stories use discovery time and a
`bio:` prefixed id. Exclude that prefix when measuring detection latency:

```sql
SELECT posted_at, seen_at FROM processed_stories WHERE story_id NOT LIKE 'bio:%';
```

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

## Deploying to a Linux host

`deploy/setup-gcp.sh` provisions a fresh Debian/Ubuntu box — venv, dependencies, and a
systemd service that restarts on failure and starts at boot. Written for a GCP
`e2-micro` (free tier, `us-west1`/`us-central1`/`us-east1`), but it makes no
GCP-specific assumptions and works on any Debian-family machine, including a Pi.

```bash
git clone git@github.com:ArsalJafri/instagram-tracker.git
cd instagram-tracker
cp .env.example .env      # then fill in DISCORD_WEBHOOK_URL
bash deploy/setup-gcp.sh
```

The script refuses to run without a `.env`, and never writes one — secrets stay off the
repo and out of provisioning.

**Carry the database across, or don't — but know which.** Copying `data/job_monitor.db`
preserves notification history and deduplication. Starting fresh is also safe: with
`PROCESS_EXISTING_STORIES_ON_STARTUP=false` the currently live Stories are recorded
without notifying, so a new host does not replay the last 24 hours.

```bash
gcloud compute scp data/job_monitor.db <instance>:~/instagram-tracker/data/ --zone <zone>
```

**Stop the old host first.** Two machines with separate databases both notify, so you
get everything twice. On the Mac:

```bash
launchctl unload ~/Library/LaunchAgents/com.arsaljafri.instagram-tracker.plist
```

**Watch the bio source on first run.** Instagram restricts datacenter IP ranges far more
aggressively than residential ones, so `instagram_bio` may return nothing from a cloud
VM even though it works from home. The setup script runs one verbose cycle and warns if
that happens. IGExport is unaffected, so the tracker still works — but if the bio source
is dead on that host, either drop it from `STORY_PROVIDER` or run on home hardware.

```bash
journalctl -u instagram-tracker -f
```

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Tests run entirely against fixtures in `fixtures/`; no live network requests.
