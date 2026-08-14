# Instagram Tracker

Monitors an Instagram account's Stories and sends a Discord alert whenever a Story links
to a relevant software engineering role — entry-level/new-grad or an internship, each
routed to its own channel.

No Instagram login, no OCR, no browser automation — Stories are read from structured
metadata and job links come from `story_link_stickers`.

Runs on Render's free tier against a hosted Postgres, kept awake and monitored by a
single UptimeRobot check. See [Deploying to Render](#deploying-to-render-free).

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
| `BIO_POLL_INTERVAL_SECONDS` | `3600` | Minimum gap between direct Instagram profile fetches |
| `POLL_INTERVAL_SECONDS` | `60` | Seconds between polls |
| `PROCESS_EXISTING_STORIES_ON_STARTUP` | `false` | If false, Stories already live on first run are recorded but never notified |
| `DATABASE_PATH` | `./data/job_monitor.db` | SQLite file |
| `DATABASE_URL` | — | Postgres URL; overrides `DATABASE_PATH` when set |
| `PORT` | — | Set by the host; when present a health endpoint is served on it |
| `DISCORD_WEBHOOK_URL` | — | Discord webhook for entry-level/new-grad roles |
| `DISCORD_INTERNSHIP_WEBHOOK_URL` | — | Separate webhook for internships; falls back to the main one |
| `RENDER_PROXY_URL` | `https://r.jina.ai/{url}` | Last-resort renderer for pages that block plain fetches |
| `DISCORD_UNKNOWN_WEBHOOK_URL` | — | Review channel for postings that could not be read |
| `DISCORD_MENTIONS` | — | Mention text prepended to new-grad alerts, e.g. `<@&123>` |
| `DISCORD_INTERNSHIP_MENTIONS` | — | Same for internship alerts; independent of the above |
| `HEARTBEAT_URL` | — | Optional healthchecks.io-style ping URL; see below |

## What counts as relevant

A posting qualifies when it is software-related **and** either entry-level/new-grad or an
internship. Each relevant job is tagged with a `role_type` that decides where it goes:

| Role type | Signals (title or `employmentType` only) | Channel |
| --- | --- | --- |
| `internship` | `intern`, `internship`, `co-op`, `coop` | `DISCORD_INTERNSHIP_WEBHOOK_URL` |
| `new_grad` | `new grad`, `entry level`, `early career`, `associate`, `engineer i/ii`, … | `DISCORD_WEBHOOK_URL` |

Internship signals are read from the title and `employmentType` only, never the
description — postings routinely mention unrelated internship programmes, which would
mislabel full-time roles. Seniority negatives still win, so "Senior Software Engineering
Intern" is rejected. `contract`, `part-time` and schema.org's own `employmentType`
spellings (`contractor`, `part_time`, `temporary`) all reject outright.

Numbered junior titles count as entry-level — `Software Engineer I/II` is exactly how
large employers denote it. The signals are scoped to follow a role word (`engineer i`,
not `i`) so a stray numeral cannot match.

**Job boards and careers landing pages also become `unknown`.** `zero2sudo` regularly
links a board rather than a posting — `careers.roblox.com/jobs?search=2027` yields
`View Jobs | Roblox`, `quantbot.com/careers` yields `Careers – Quantbot Technologies`.
The pipeline assumes one link is one job, and a listing breaks that: the title is
plausible enough to pass every other check, so the classifier confidently reports "not
software" about a page that never held a single role. Only the segment *before* a
`|`, `–` or `·` is checked, and the test is compositional rather than a list of phrases:
chrome is a lead segment built *entirely* from board vocabulary (`job`, `jobs`, `detail`,
`details`, `careers`, `search`, `results`, `open`, `positions`, …). Enumerating phrases
was whack-a-mole — `job detail` was listed and `Job Details | Dayforce Jobs` still slipped
through. A real title always contains at least one word outside that vocabulary, so
`Software Engineer | Careers` and `Software Engineer - Early Career` are untouched. These reach the review channel,
where you can open them yourself — nobody can automate "is one of these 2027 roles
relevant?"

**Unreadable pages become `unknown`, never `not_relevant`.** Client-rendered career sites
return page chrome as the `<title>` — `JobDetail`, `search`, a vendor name. A title like
that is worse than none, because it makes the classifier reject confidently when it never
saw the posting. Page titles that are chrome or a single word are discarded, falling
through to the URL slug and then to `unknown`. JSON-LD titles are exempt: those are the
employer's own statement of the role.

## Reading pages that block plain fetches

Job retrieval tries four sources, in order of reliability and cost:

```
JSON-LD JobPosting → OpenGraph/<title> → URL slug → rendering proxy → unknown
```

The fourth exists because some sites cannot be read by any plain client.
`careers.ibm.com` answers with an **AWS WAF challenge** — HTTP 202 and a script that must
execute to mint an `aws-waf-token` cookie. No HTML, no JSON-LD, and a URL that carries
the role only as `?jobId=128497`, so the slug yields nothing either. A real internship
was missed this way on 2026-08-11.

Worth being precise about what was broken: **nothing was wrong with the parsing.** Once
the challenge passes, the page serves an ordinary `og:title` that step 2 already handles.
The only missing piece was a browser to run the challenge. `RENDER_PROXY_URL` borrows one.

It is deliberately last. The proxy costs several seconds and a third-party call against
milliseconds for the others, so the common path never reaches it — only the one or two
links a day nothing else can read. Set `RENDER_PROXY_URL` empty to disable.

Failure is contained in every direction. If the proxy is down, times out, or the site
blocks it too, the result is the `unknown` it would have been anyway. That last case is
real: Tesla refuses the proxy as well, and the proxy reports it in-band with a 200 and an
`Access Denied` title, which is detected rather than mistaken for a job.

**Every link that is not a confirmed match goes to the review channel.** If `zero2sudo`
posted it, it surfaces somewhere. Judging which rejections were "interesting enough"
guessed wrong repeatedly, and the misses were invisible by construction — you only notice
an alert that never arrived if you happen to be watching Instagram yourself.

| Outcome | Channel | Headline |
| --- | --- | --- |
| relevant, new grad | `DISCORD_WEBHOOK_URL` | New entry-level software role |
| relevant, internship | `DISCORD_INTERNSHIP_WEBHOOK_URL` | New software internship |
| near miss | review | Near match — matched one rule but not the other |
| unreadable | review | Could not read this posting — check it manually |
| no match | review | Other link — did not match the rules |

Each has its own embed colour, and every review-channel alert carries a **Why** field
with the classifier's reason. Only confirmed matches ping anyone.

**Near misses are still labelled separately.** A posting that satisfies exactly one of
the two rules — an internship or entry-level signal without a software one, or a software
signal with no level stated — is worth a glance but not an alert. `zero2sudo` posts a lot
of business-analyst and product-management internships, which are genuine early-career
roles that this tracker is not scoped for.

A seniority word settles the question only when nothing else claims the role is junior:
`Senior Software Engineer` is a clean reject and stays silent, while
`Product Manager Intern` trips `manager` yet is plainly an internship, so it still
surfaces. Newsletters and events match neither rule and never appear.

**And `unknown` postings are sent to a review channel** via
`DISCORD_UNKNOWN_WEBHOOK_URL`, in amber and headed "Could not read this posting", with a
field explaining why. Some sites cannot be read at all — `careers.ibm.com` answers a
plain fetch with HTTP 202 and an empty body, which is bot detection rather than
client-side rendering, and no amount of parsing recovers the role. Dropping those links
silently cost a real internship on 2026-08-11.

These alerts never carry a mention: they are triage, not urgency, and a ping on every
unreadable link would train you to ignore the ping that matters. They never fall back to
a real channel either — with no review webhook set they stay silent, as before.

Signal matching allows a bounded set of inflections (`s`, `es`, `ing`, `ed`, `ship`), so
`software engineer` matches "Software Engineering" and `intern` matches "interns" and
"internship". The set is deliberately closed: open-ended suffix matching would make
`intern` match **internal** and **international**, turning an Internal Tools role into an
internship alert.

## Mentioning people

`DISCORD_MENTIONS` and `DISCORD_INTERNSHIP_MENTIONS` hold raw Discord mention text,
prepended to the alert so it actually notifies someone:

```bash
DISCORD_MENTIONS=<@&987654321>          # a role
DISCORD_INTERNSHIP_MENTIONS=<@&111222333>
```

Enable **Settings → Advanced → Developer Mode** in Discord, then right-click a role or
user and *Copy ID*. Prefer a role: membership is then managed in Discord instead of by
redeploying.

**The wrapper is mandatory.** `<@&ID>` is a role, `<@ID>` a user; `@here` and
`@everyone` work as written. A bare id — `@1536859615256645732`, or the number alone —
is rendered as plain text and pings nobody. Since "Copy ID" hands you the bare number,
prefixing `@` is the natural guess and the failure is completely silent: the message
sends, looks right, and notifies no one.

The app therefore checks at startup and says so:

```text
WARNING  DISCORD_MENTIONS contains bare id(s) 1536859615256645732 — Discord renders
         these as plain text and pings nobody. Wrap them: <@&ID> for a role,
         <@ID> for a user.
```

If that warning is absent from the logs, the syntax is right.

Two details this implementation gets right, both easy to get wrong:

**The mention goes in `content`, never the embed.** Mentions inside an embed render as
blue text and notify nobody — it looks correct and silently pings no one.

**`allowed_mentions` is always sent, and starts closed.** Discord pings nothing the field
does not permit. With no mentions configured the message is barred from pinging anything;
with mentions configured it permits users and roles, and unlocks `@everyone`/`@here` only
when the configured text actually asks for them. A stray character cannot mass-notify a
server.

The two settings are independent and do not fall back to each other — pinging the wrong
group is worse than pinging nobody.

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
polling Instagram directly at Story cadence invites a soft block. **This is not
hypothetical.** On 2026-08-11, at 600s and with two tracker instances accidentally
running, Instagram began returning:

```json
{"message":"Please wait a few minutes before you try again.","require_login":true}
```

The 401 was IP-wide, not app-specific — plain `curl` got it too. The interval was raised
to 3600s in response.

**In production this source is disabled**, and should stay that way. Deployed to Render
the same day, it returned **429 Too Many Requests** from a completely fresh datacenter
IP with no request history. Instagram restricts cloud ranges outright, so it cannot work
from a host — `STORY_PROVIDER=igexport` is the correct production value. IGExport is a
proxy and is unaffected.

The composite means either failure is contained: the source logs a warning and IGExport
carries on. Nothing needs doing when this breaks.

Bio links have no publish timestamp, so their synthetic Stories use discovery time and a
`bio:` prefixed id. Exclude that prefix when measuring detection latency:

```sql
SELECT posted_at, seen_at FROM processed_stories WHERE story_id NOT LIKE 'bio:%';
```

## Staying alive (local only)

A tracker that dies quietly looks exactly like an account that stopped posting — both
are an empty Discord channel. Two mechanisms cover that when running on your own machine.

**In production neither is used.** The 503-on-stall health endpoint plus a single
UptimeRobot check does the same job, so `HEARTBEAT_URL` stays unset on Render and the
launchd agent is not installed. This section applies to local development, or to running
on hardware you own.

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

## Deploying to Render (free)

Render's free tier runs this at no cost, with two constraints that shape the setup.

**No background workers.** Free covers web services only, so the tracker binds `PORT`
and serves a JSON status page. Nothing else about the poller changes; when `PORT` is
absent, as it is locally, no socket is opened at all.

```json
{ "status": "ok", "polls": 42, "notifications_sent": 3,
  "seconds_since_last_poll": 17, "stale_after_seconds": 300, "last_error": null }
```

**It reports what the tracker actually decided.** `channels` shows which webhooks are
configured, as booleans — never the URLs. `recent` holds the last fifteen link decisions
with the title, verdict and destination. This exists because "why didn't X send?" was
repeatedly answered by reconstructing events from Instagram's feed and the git log, when
the tracker knew the answer all along:

```json
"channels": { "new_grad": true, "internship": true, "review": false },
"recent": [
  { "title": "Software Developer Spring Co-op 2027", "verdict": "relevant",
    "sent_to": "internship", "url": "..." },
  { "title": null, "verdict": "unknown", "sent_to": "review", "url": "..." },
  { "title": "Senior Leader Speaker Series", "verdict": "not_relevant",
    "sent_to": "none (silent)", "url": "..." }
]
```

`sent_to` distinguishes the three ways nothing arrives: `none (silent)` means the rules
rejected it outright, `none (no channel configured)` means it qualified but the webhook
is missing, and a channel name means it was delivered.

**It answers HEAD as well as GET.** Uptime monitors send HEAD by default, since it
avoids transferring a body, and Python's `BaseHTTPRequestHandler` replies `501` to any
verb without a handler. Without `do_HEAD` the endpoint looks permanently down to the very
thing watching it — which is exactly what happened on first deployment, while `curl`
(a GET) reported everything healthy.

**It returns 503 once polling has stalled** — no successful poll for five poll intervals
(minimum 300s). A process that is alive but no longer polling is the failure worth
catching, and a permanent 200 would hide it. Because the failure shows up in the status
code, an ordinary uptime check becomes a real liveness check, and UptimeRobot doubles as
the heartbeat with nothing else to configure.

Before the first poll the clock runs from startup, so a cold deploy gets a grace period
rather than failing its host health check on the first request.

**No persistent disk.** Free services have an ephemeral filesystem, so SQLite would be
wiped on every redeploy, restart and spin-down. That is not just lost history: an empty
database makes startup seeding mark every live Story as seen-but-not-notified, silently
swallowing any job posted just before the restart. Set `DATABASE_URL` and the same code
runs on Postgres instead.

**Use a database that does not expire.** Render's own free Postgres is deleted 30 days
after creation. Point `DATABASE_URL` at Neon or Supabase, whose free tiers are permanent.

The connection is re-established automatically if it drops. A local SQLite file never
dies, but a hosted Postgres will — on restart, idle timeout or a network blip — and
without reconnection the tracker would keep running while silently persisting and
notifying nothing.

**Build with `pip install .`, not `pip install -r requirements.txt`.** This is a `src/`
layout, so the package itself must be installed for `python -m instagram_tracker` to
resolve. Installing only the requirements leaves every dependency present and the
application missing, and the deploy fails with `No module named instagram_tracker`.
`pyproject.toml` carries the same dependency list, so one command covers both.

Steps:

1. Create a free Postgres on **Neon** or **Supabase** and copy its connection URL.
   Render's own free Postgres is deleted after 30 days, so do not use it.
2. In Render: **New → Blueprint** from the top-level dashboard (not from inside a
   Project, which only offers service types), and `render.yaml` describes everything.
   Creating a **Web Service** by hand works identically — leave **Root Directory**
   blank, since the repository root is the project root.
3. Set `DATABASE_URL`, `DISCORD_WEBHOOK_URL` and `DISCORD_INTERNSHIP_WEBHOOK_URL` in the
   dashboard. They are marked `sync: false` so they never live in the repo. Leave
   `HEARTBEAT_URL` unset — step 4 covers it.
4. Point **UptimeRobot** (free, 50 monitors) at the service URL on a **5-minute**
   interval. Confirm it reports Up — if it says down while `curl` succeeds, check HEAD
   specifically:

   ```bash
   curl -s -I -o /dev/null -w "HEAD -> %{http_code}\n" https://<your-service>.onrender.com/
   ```

**Use 5 minutes, not 15.** Fifteen is exactly Render's spin-down threshold, leaving no
margin: checks drift, and one delayed or failed request lets the service sleep. Five
gives roughly three pings per window, so two can fail harmlessly. It also matches the
300-second stall threshold, so a stalled poller surfaces in 5–10 minutes instead of up
to half an hour. Sleep is not merely a slow first request — polling is stopped the whole
time, and a job posted in that window is never seen.

Verify the deploy by watching the logs for `State: PostgreSQL`. If it says
`State: SQLite`, `DATABASE_URL` was not picked up and everything will vanish on the next
restart. Schema is created on first connect, so there is no migration step. Expect
`First run: recording N existing Stories without notifying` on the first boot.

Two limits worth knowing: the free tier allows **750 instance hours per month** against a
~730-hour month, so this covers exactly one always-on service — a second project will not
fit. And a spin-down that UptimeRobot misses costs about a minute of cold start on top of
the polling that never happened.

## Verifying it is running

The health endpoint answers this on its own; you do not need logs. Take two readings a
minute apart:

```bash
while true; do date +%T; curl -s https://<your-service>.onrender.com/; echo; sleep 60; done
```

Two numbers together are the proof, and neither is sufficient alone:

- **`uptime_seconds` advancing in step with wall-clock time** means the process did not
  restart or sleep between readings.
- **`polls` incrementing** means it is doing work rather than merely answering HTTP.

A low `uptime_seconds` by itself is not a fault — saving any environment variable in
Render triggers a redeploy, which resets it.

| Symptom | Meaning |
| --- | --- |
| `HTTP 503` | Alive but no successful poll in 5 minutes — stall detection firing |
| Timeout or connection refused | Asleep or down; the first request wakes it after ~1 minute |
| `polls` unchanged across readings | Stalled, despite returning 200 |
| `uptime_seconds` repeatedly resetting | Crash looping — read the dashboard logs |

For log lines rather than status, the Render dashboard's **Logs** tab streams live, or
install the CLI with `brew install render` and run `render help logs` for the current
syntax.

In practice UptimeRobot makes this unnecessary: it checks every 5 minutes and alerts on
both a dead service and a 503, which covers "down" and "up but doing nothing".

## Deploying to a Linux host

`deploy/setup-linux.sh` provisions a fresh Debian/Ubuntu box — venv, dependencies, and a
systemd service that restarts on failure and starts at boot. It makes no cloud-specific
assumptions and works on any Debian-family machine, including a Raspberry Pi.

```bash
git clone git@github.com:ArsalJafri/instagram-tracker.git
cd instagram-tracker
cp .env.example .env      # then fill in DISCORD_WEBHOOK_URL
bash deploy/setup-linux.sh
```

The script refuses to run without a `.env`, and never writes one — secrets stay off the
repo and out of provisioning.

### Oracle Cloud Always Free

The target host. Unlike GCP, the Always Free allowance **includes a public IPv4**, which
is what makes it genuinely free rather than free-plus-$3.60/month for the address.

- **Shape:** `VM.Standard.E2.1.Micro` (AMD, 1/8 OCPU, 1GB RAM) is always available and
  ample — the tracker idles around 51MB. `VM.Standard.A1.Flex` (ARM) gives far more
  headroom within the Always Free allowance, halved to 2 OCPU / 12GB in June 2026, but
  ARM capacity is frequently exhausted. Take A1 if you can get it, E2.1.Micro otherwise.
- **Image:** Canonical Ubuntu or Oracle Linux. The script targets Debian-family, so
  choose Ubuntu.
- **Region:** must be your tenancy's home region, or it is not free.
- **Networking:** no inbound rules are needed beyond SSH. Oracle blocks inbound by
  default in both the security list and local iptables, which trips people up — but this
  service only makes outbound connections, so leave all of it alone.

**Idle reclamation is the real risk.** Oracle reclaims Always Free compute when
95th-percentile CPU *and* network stay below 20% over 7 days. A 60-second poller that
sleeps in between fits that description exactly. Two mitigations:

1. Upgrade the account to Pay-As-You-Go. Always Free resources stay $0 and reclamation
   stops applying to the tenancy. Set a $1 budget alert so any real charge surfaces
   immediately.
2. Failing that, treat reclamation as a when-not-if and rely on the heartbeat to tell
   you, since a reclaimed instance dies silently.

**Carry the database across, or don't — but know which.** Copying `data/job_monitor.db`
preserves notification history and deduplication. Starting fresh is also safe: with
`PROCESS_EXISTING_STORIES_ON_STARTUP=false` the currently live Stories are recorded
without notifying, so a new host does not replay the last 24 hours.

```bash
scp data/job_monitor.db ubuntu@<instance-ip>:~/instagram-tracker/data/
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

Look for this line in the first-run output:

```text
DEBUG instagram_tracker.sources.instagram_bio: Instagram bio exposed N distinct link(s)
```

```bash
journalctl -u instagram-tracker -f
```

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Tests run entirely against fixtures in `fixtures/`; no live network requests, and SQLite
is used throughout so the suite needs no database server.
