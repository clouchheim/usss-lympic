# usss-lympic

Pulls alpine-skiing timing data out of Lympik and uploads it into Teamworks AMS,
one "Lympik Event" form entry per athlete per training session.

## The flow, in order

1. **Find recent Lympik events.** Calls Lympik's `/profile/{pId}/activity/search`
   (looking back 24 hours by default) and collects the unique event ids.
2. **Get the Teamworks athlete roster.** Calls Teamworks' `/api/v1/usersynchronise`
   to fetch every athlete the API account can see.
3. **For each Lympik event:**
   - Pull the event's own details (name, location, start time) from `/event/{eId}`.
   - Pull every run in that event from `/event/{eId}/alpine-skiing/group`, and turn
     them into a table: one row per run, with each run's athlete, start time,
     splits, total time, and DNF status.
   - Group that table by athlete name, and match each Lympik athlete name against
     the Teamworks roster (last name → first initial → full first name — see
     `athlete_matching.py`). An athlete with no confident match gets logged as an
     error and skipped, never guessed.
   - For each matched athlete, sort their runs oldest-to-newest, number them
     `Run #`, and build one Teamworks event payload: the session's shared fields
     in row 0, one table row per run after that.
4. **Skip anything already uploaded.** Every (event, athlete) pair that
   previously succeeded is recorded in a local ledger file
   (`uploaded_events.json`) so it's never re-uploaded on a later run. See
   "Known limitations" below — this is a stopgap, not a query against Teamworks
   itself.
5. **Upload.** All the pending athlete-events across every Lympik event in this
   run are batched together and sent to Teamworks' `/api/v1/eventsimport` in
   groups of 25. That endpoint fails an entire batch if even one event in it is
   malformed, with no indication of which one — so a batch failure automatically
   retries its events one at a time to isolate the actual problem, log it, and
   still let the good ones through.
6. **Log the result.** Every success and failure is logged (with the event id
   and athlete name) so a run's outcome is fully visible without a debugger.

## How to run it

Needs Python 3.9+ (for the standard-library `zoneinfo` module).

```bash
git clone <this repo>
cd usss-lympic
pip install -r requirements.txt
cp .env.example .env
# edit .env -- fill in the four real secrets (see below), leave the rest as-is
python run_pipeline.py
```

Each run does **one pass**: it looks back 24 hours from "now" and processes
whatever it finds. Running it again immediately is safe (the ledger prevents
duplicates) but pointless unless new sessions have happened. Actual scheduling
(the "run this every N minutes/hours" part) is intentionally not baked into the
script — wire it up however fits where you're running it:

**Locally / on a VM** — a cron entry:
```cron
*/30 * * * * cd /path/to/usss-lympic && /usr/bin/python3 run_pipeline.py >> pipeline.log 2>&1
```

**GitHub Actions** — a scheduled workflow, with the four secrets set as repo/environment
secrets (Settings → Secrets and variables → Actions):
```yaml
# .github/workflows/pipeline.yml
on:
  schedule:
    - cron: "*/30 * * * *"
  workflow_dispatch: {}   # lets you trigger a run manually too

jobs:
  run-pipeline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - run: python run_pipeline.py
        env:
          LYMPIK_PROFILE_ID: ${{ secrets.LYMPIK_PROFILE_ID }}
          LYMPIK_API_KEY: ${{ secrets.LYMPIK_API_KEY }}
          TEAMWORKS_USERNAME: ${{ secrets.TEAMWORKS_USERNAME }}
          TEAMWORKS_PASSWORD: ${{ secrets.TEAMWORKS_PASSWORD }}
```
One catch with GitHub Actions specifically: the ledger (`uploaded_events.json`) only
lives on the runner's disk, which is thrown away after each job. Without persisting
it (e.g. `actions/cache`, or committing it back, or a small storage step), every
scheduled run would start with an empty ledger and treat the whole 24-hour lookback
window as brand new -- that's real risk of duplicate uploads on GitHub Actions
specifically, worth solving before relying on it there.

I haven't created the workflow file itself since that wasn't asked for -- say the
word and I'll add it for real, including the ledger-persistence piece.

### Secrets you need in `.env`

| Variable | What it is |
|---|---|
| `LYMPIK_PROFILE_ID` | Your Lympik profile UUID |
| `LYMPIK_API_KEY` | Your Lympik personal API key |
| `TEAMWORKS_USERNAME` | Teamworks AMS API account username |
| `TEAMWORKS_PASSWORD` | Teamworks AMS API account password |

Everything else in `.env.example` (`LYMPIK_BASE_URL`, `TEAMWORKS_BASE_URL`,
`TEAMWORKS_APP_ID`, `PIPELINE_TIMEZONE`) already has a working default and only
needs changing if you have a specific reason to.

## Files

**The actual pipeline:**
- `run_pipeline.py` — the entry point (`python run_pipeline.py`). Everything
  described in "The flow" above lives here: pulling event data, building the
  per-athlete runs table, matching, batching, uploading, and the local ledger.
- `lympik_client.py` — Lympik API client. Handles auth (HTTP Basic, profile
  UUID + API key) and generic GET/pagination, plus `search_activity()` for the
  event-discovery endpoint.
- `lympik_activity.py` — turns an activity-search response into a deduplicated
  list of event ids (step 1 of the flow).
- `teamworks_client.py` — Teamworks AMS API client. `list_athletes()` walks the
  paginated user-sync endpoint; `bulk_import_events()` submits events in
  batches and handles the all-or-nothing-per-batch failure mode by retrying
  individually.
- `athlete_matching.py` — the name-matching cascade (last name → first initial
  → full first name) between a Lympik athlete name and the Teamworks roster,
  plus small helpers for Teamworks' inconsistent field naming
  (`firstName`/`first_name`/`forename`, etc.).

**Reference docs:**
- `PIPELINE.md` — the detailed design doc: what's solved vs. still a stopgap,
  two real bugs that testing caught before they shipped, and open questions
  (most importantly: there's still no confirmed Teamworks endpoint to query
  existing form entries, so duplicate protection currently depends entirely on
  `uploaded_events.json` surviving between runs).
- `docs/teamworks-api-reference.md` — the Teamworks v1 API endpoints this
  project uses (`usersynchronise`, `eventimport`, `eventsimport`), cleaned up
  from Teamworks' own reference material.
- `docs/teamworks-ams-notes.md` — hard-won gotchas from an earlier AMS
  integration (`usss-mocap`) that this project's Teamworks client follows:
  HTTP 200 on failure, `existingEventId` replacing rather than merging, how to
  lay out single-value fields vs. table rows, etc.

**Config:**
- `.env.example` — template for `.env`. Copy it, fill in the four secrets above.
- `requirements.txt` — `requests`, `python-dotenv`, `pandas`.
- `.gitignore` — keeps `.env`, `uploaded_events.json`, and old exploration
  output (`samples/`) out of version control.

**Earlier exploration, not part of the live pipeline:**
- `explore_payloads.py` — the very first script in this project, from before
  a real event-discovery endpoint was known. Authenticates and dumps sample
  Lympik payloads to `samples/*.json` for manual inspection. Still useful if
  you ever need to poke at a new/undocumented Lympik endpoint by hand.
- `lympik_extract.py` — an earlier, more generic single-event extractor,
  written before the actual "Lympik Event" form fields were known. Superseded
  by the more specific `build_runs_dataframe()`/`build_athlete_payloads()` in
  `run_pipeline.py`. Left in place rather than deleted, but nothing in the
  current flow calls it.

## After a run

Check the console output (or wherever you redirect it, e.g. `pipeline.log` in
the cron example above) for:
- `INFO` lines: recent-event counts, successful uploads with the resulting
  Teamworks event id.
- `WARNING` lines: a run with no assigned athlete was skipped (an unassigned
  timing pulse, not a real error).
- `ERROR` lines: an athlete with no confident Teamworks match, or an upload
  that actually failed -- these need a human to look at (fix the roster
  mismatch, or investigate the failure reason in the log message).

`uploaded_events.json` in the working directory is the ledger -- delete it
only if you deliberately want to force a full re-upload of everything in the
current lookback window (24 hours), since Teamworks has no way to tell you
what's already there.
