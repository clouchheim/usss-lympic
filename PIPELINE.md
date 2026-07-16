# Lympik → Teamworks AMS pipeline

The concrete, current pipeline lives in `run_pipeline.py` (steps 1-7 below). It
supersedes the earlier, more abstract 6-step plan this doc used to describe.

## Status

| Step | Status |
|---|---|
| 1. Get recent event IDs from Lympik | Done — `lympik_activity.py: get_recent_event_ids()` |
| 2. Pull existing "Lympik Event" entries from Teamworks | **Stopgap** — see below |
| 3. Get unique Event IDs already in Teamworks | **Stopgap** — local ledger instead |
| 4. Get all Teamworks athletes | Done — `teamworks_client.py: TeamworksClient.list_athletes()` |
| 5. Filter Lympik event IDs to ones not yet uploaded | Done, but only as safe as the stopgap above |
| 6. Per event: pull data, build per-athlete runs, match, upload | Done — `run_pipeline.py: process_event()` |

Everything under "Done" was exercised against mock HTTP servers seeded with the
real sample payloads you provided (`event_detail.json`, `event_results.json`,
`profile_activity_timing.json`) — not against live credentials. Testing this way
caught a real bug before it shipped: see "Bug found during testing" below.

## Step 1: recent event IDs — solved

`/profile/{pId}/activity/search?dateFrom=...&dataType=timing` was the missing
discovery endpoint from earlier investigation — not in the published OpenAPI spec,
reachable via the `activity.search` key scope. `lympik_client.py`'s
`search_activity()` calls it; `lympik_activity.py`'s `get_recent_event_ids()`
returns the deduplicated event ids from the response.

## Steps 2-3: which events are already in Teamworks — stopgap, not solved

There's still no confirmed Teamworks endpoint for "list existing entries for form
X in a date range." Every doc seen so far (`docs/teamworks-api-reference.md`,
`docs/teamworks-ams-notes.md`) only covers `usersynchronise` (users) and
`eventimport` (create/update, write-only).

**Current behavior**: `run_pipeline.py` keeps a local JSON ledger
(`uploaded_events.json`, gitignored) of Lympik event ids it has successfully
uploaded. On each run, it filters out any event id already in the ledger before
processing. This does prevent duplicate uploads across repeated runs of *this
script* — but it is not the same as asking Teamworks directly, and it has no
visibility into a "Lympik Event" entry created any other way (a manual entry, a
different script, a ledger file that gets lost/reset).

**If/when a real query endpoint is found**, swap `_load_ledger()` /
`_add_to_ledger()` in `run_pipeline.py` for a real call to it — nothing else in
the pipeline needs to change, since `run()` already takes
`already_uploaded_event_ids` as an explicit set.

## Step 4: all Teamworks athletes — solved

`TeamworksClient.list_athletes()`, unchanged from before — cursor-paginated
`/api/v1/usersynchronise`.

## Step 6: per-event processing — solved

`process_event()` in `run_pipeline.py`:
1. `GET /event/{eId}` → `Event ID` / `Session Name` / `Location` / `startedAt unix`
   fields, shared by every athlete's entry for this event.
2. `GET /event/{eId}/alpine-skiing/group` → one row per run, via
   `build_runs_dataframe()`. Splits are read from each run's `edges` list by
   `sequence` (0/1/2 → Split 1/2/3); a run with no `sequence` match is left
   blank, not zero. Runs with no assigned athlete (`profile` null/missing — an
   unassigned DNF pulse) are dropped and logged, since there's no one to upload
   them against.
3. Athletes within the event are grouped **by name** (`firstName`+`lastName`),
   matched against Teamworks via `athlete_matching.match_athletes()`'s
   last-name → first-initial → full-first-name cascade. Unmatched athletes are
   logged as errors (with event id) and skipped, never guessed.
4. Each matched athlete's runs are sorted earliest→latest, numbered into
   `Run #` (1-indexed), and built into one `eventimport` payload — event-level
   fields in `row: 0`, one table row per run after that — then uploaded via
   `TeamworksClient.import_event()`.
5. The event id is only added to the ledger after every athlete in it has been
   attempted without an unhandled exception (business-logic misses like an
   unmatched athlete are logged and still count as "attempted," so they don't
   retry forever without a human fixing the underlying roster mismatch; a
   network/API-level exception does NOT get ledgered, so it retries next run).

## Bug found during testing: group by name, not Lympik profile id

First pass grouped an event's runs by the Lympik profile `id` (safer in theory
against two *different* athletes sharing a name). Testing against your real
sample data caught a worse problem: the same athlete showed up under two
slightly different profile-id strings across runs within one event, which split
one athlete into two separate `eventimport` creates for the same session —
exactly the duplicate-entry problem this pipeline exists to prevent. Reverted to
grouping by name (`firstName`+`lastName`), per the original spec. Tradeoff, for
awareness: two genuinely different athletes sharing an exact name within one
event would now merge incorrectly — call this out if that's a real risk for
your rosters.

## Open questions before running this for real

1. **Step 2/3's real endpoint** (see above) — is there one, and if so, what is
   it? Until answered, duplicate protection is only as strong as the local
   ledger file surviving between runs.
2. **Timezone** for converting Lympik's unix timestamps into AMS's
   `dd/MM/yyyy` / `h:mm AM/PM` strings — currently `PIPELINE_TIMEZONE` env var,
   defaulting to UTC. Getting this wrong could shift an evening session onto
   the wrong calendar day in AMS.
