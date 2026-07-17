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
| 5. Filter to (event, athlete) pairs not yet uploaded | Done, but only as safe as the stopgap above |
| 6. Per event: pull data, build per-athlete runs, match, upload | Done — `run_pipeline.py` |

Everything under "Done" was exercised against mock HTTP servers seeded with the
real sample payloads you provided (`event_detail.json`, `event_results.json`,
`profile_activity_timing.json`) — not against live credentials. Testing this way
caught two real bugs before they shipped: see "Bugs found during testing" below.

## Step 1: recent event IDs — solved

`/profile/{pId}/activity/search?dateFrom=...&dataType=timing` was the missing
discovery endpoint from earlier investigation — not in the published OpenAPI spec,
reachable via the `activity.search` key scope. `lympik_client.py`'s
`search_activity()` calls it; `lympik_activity.py`'s `get_recent_event_ids()`
returns the deduplicated event ids from the response. Only `dateFrom` is used
(no upper bound) — confirmed this always looks forward from that point to now.

## Steps 2-3: which events are already in Teamworks — stopgap, not solved

There's still no confirmed Teamworks endpoint for "list existing entries for form
X in a date range." Every doc seen so far only covers `usersynchronise` (users)
and the `eventimport`/`eventsimport` family (create/update, write-only).

**Current behavior**: `run_pipeline.py` keeps a local JSON ledger
(`uploaded_events.json`, gitignored) of `(event_id, teamworks_user_id)` pairs it
has successfully uploaded — **per athlete**, not per whole event (see "Bugs
found during testing" below for why). On each run, it filters out any pair
already in the ledger before uploading. This prevents duplicate uploads across
repeated runs of *this script* — but it is not the same as asking Teamworks
directly, and it has no visibility into a "Lympik Event" entry created any
other way (a manual entry, a different script, a ledger file that gets
lost/reset).

**If/when a real query endpoint is found**, swap `_load_ledger()` /
`_add_to_ledger()` in `run_pipeline.py` for a real call to it — nothing else in
the pipeline needs to change, since `run()` already takes
`already_uploaded_pairs` as an explicit set.

## Step 4: all Teamworks athletes — solved

`TeamworksClient.list_athletes()` — cursor-paginated `/api/v1/usersynchronise`.

## Steps 5-6: per-event processing and upload — solved

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
   full-name → last-name → first-initial → full-first-name cascade (an exact
   first+last match is taken immediately when it's unique; only an
   unresolved/absent full-name match falls through to the last-name-first
   cascade). Unmatched athletes are logged as errors (with event id) and
   skipped, never guessed.
4. Each matched athlete's runs are sorted earliest→latest, numbered into
   `Run #` (1-indexed), and built into one event payload — event-level fields
   in `row: 0`, one table row per run after that.
5. **All** athlete payloads across **every** event in the run are collected
   first, filtered against the ledger, sorted oldest-first, and submitted
   together via `TeamworksClient.bulk_import_events()` (`POST
   /api/v1/eventsimport`), batched (default 25/batch, per Teamworks' own
   sample). This endpoint is all-or-nothing per batch — one malformed event
   fails the whole batch with no indication which one — so a batch failure
   automatically retries every event in it individually to isolate the cause;
   confirmed by test (see below).
6. Only athlete-events that actually succeeded get added to the ledger.

## Bugs found during testing

Both of these were caught by testing against mock servers seeded with your real
sample data, before either shipped:

1. **Grouping runs by Lympik profile id instead of name.** Safer in theory
   against two *different* athletes sharing a name — but your sample data
   showed the same athlete under two slightly different profile-id strings
   across runs within one event, which split that athlete into two separate
   uploads for the same session (exactly the duplicate problem this pipeline
   exists to prevent). Reverted to grouping by name (`firstName`+`lastName`),
   per the original spec. Tradeoff, for awareness: two genuinely different
   athletes sharing an exact name within one event would now merge — flag this
   if that's a real risk for your rosters.
2. **Ledgering by whole event instead of per athlete.** A batch-submission
   test simulated one athlete's upload failing while another in the *same*
   event succeeded. With a whole-event ledger, the failed athlete blocked the
   entire event from being marked done — so the next run would have retried
   the whole event, **re-uploading the athlete who had already succeeded** as
   a second, duplicate entry (there's no `existingEventId` tracking to turn
   that retry into an update instead). Fixed by ledgering per
   `(event_id, teamworks_user_id)` pair, confirmed with a two-run test: only
   the previously-failed athlete gets re-attempted, the already-succeeded one
   is correctly skipped.

## Open questions before running this for real

1. **Step 2/3's real endpoint** (see above) — is there one, and if so, what is
   it? Until answered, duplicate protection is only as strong as the local
   ledger file surviving between runs.
2. ~~Timezone~~ — resolved: always Mountain Time. `PIPELINE_TIMEZONE` env var
   defaults to `America/Denver` (handles MST/MDT automatically).
