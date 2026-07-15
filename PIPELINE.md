# Lympik → Teamworks AMS pipeline

## Status

| Step | Status |
|---|---|
| 1. Get eventIDs for a given time period | **Blocked** — see below |
| 2. Get all athletes from Teamworks AMS | Done — `teamworks_client.py: TeamworksClient.list_athletes()` |
| 3. Match Lympik profiles to Teamworks athletes | Done — `athlete_matching.py: match_athletes()` |
| 4. Section the Lympik payload per athlete | Done — `lympik_extract.py: get_event_sessions()` |
| 5. Build the Teamworks upload payload | Not started — blocked on the AMS form not existing yet |
| 6. Upload to Teamworks, no duplicates | Not started — same blocker, plus a dedupe strategy to implement |

All three "done" pieces were tested against mock HTTP servers standing in for the
real APIs (pagination, field-name variance, ambiguous-name matching, the
Teamworks "200 on failure" body, and a 404 on a Lympik edge lookup) — not against
live credentials. Wire in real `.env` values and a real `eId` to confirm end to end.

## Step 1: get eventIDs for a given time period — still blocked

Recap of what's been ruled out: the Lympik OpenAPI spec has no list/search-events
endpoint. Profile-scoped discovery (`explore_payloads.py`'s `discover()`) — your own
devices, motion templates, alpine-skiing statistics — either came back empty or
turned out to be config/catalog data with no event reference. Your API key was
granted `activity.list` / `activity.search` / `customer.list` scopes that have no
counterpart anywhere in the documented spec, which is the strongest lead: those are
very likely the real discovery mechanism, scoped to an org/"customer" entity your
profile doesn't directly own (consistent with your own device list coming back empty).

Next actions, in order:
1. Ask Lympik for documentation of the `activity.*` / `customer.*` scopes.
2. Ask whether USSS's devices/events are registered under a team/org profile
   distinct from your personal one — if so, get that profile's ID and re-run
   `discover()` against it.
3. Until resolved, unblock steps 2–6 with a manually maintained list of event IDs
   (copied from the Lympik web app URL after each session) — good enough to build
   and test the rest of the pipeline, not a real solution for "by time period."
4. Once a real discovery endpoint exists, add a `list_events(since, until)`-style
   function next to `LympikClient` — nothing downstream needs to change, since
   `get_event_sessions(client, event_id)` already just takes a plain event ID.

## Step 5: build the Teamworks payload — blocked on the form

Blocked on the actual AMS form: its exact name and field names, which must match the
form builder exactly (case-sensitive) per `docs/teamworks-api-reference.md`.

Once the form exists, this step needs to:
- Make one `import_event()` call per (athlete, event) pair — never split one
  athlete's session across multiple calls, since `existingEventId` **replaces**
  the event's contents rather than merging (`docs/teamworks-ams-notes.md`).
- `userId`: the matched Teamworks user ID from step 3.
- `startDate` / `startTime` (and `finishDate` / `finishTime` if the form uses them):
  derived from `sessions["event"]["started_at"]` (from `lympik_extract.get_event_sessions`)
  — needs converting from Lympik's integer timestamp to AMS's `dd/MM/yyyy` / `h:mm AM/PM`
  string formats.
- `rows`:
  - `row: 0` = single-value/event-level fields only (e.g. session name, and
    probably the Lympik `eId` itself as a field — see step 6).
  - `row: 1..N` = one row per run, from `sessions["athletes"][athlete_id]["runs"]`
    (each run has `label`, `invalid`, `edges`) — exact column keys depend on the
    form once it's built.
  - Every `value` must be a string regardless of source type — numbers/timestamps
    need `str()`.

Decisions needed before this can be coded for real:
1. Final AMS form name + field list (step 3 of your original 7-step plan).
2. Which run fields actually matter in the table — all edges/splits, or a subset
   (best time, DNF/DSQ flag, max speed if motion data is ever pulled in too).
3. Whether the form stores the Lympik `eId` as a field (recommended — see step 6).

## Step 6: upload with no duplicates — blocked on the same form, plus a dedupe strategy

Rule: each `eId` produces at most one Teamworks entry per athlete.

The v1 API has no query/list-by-field endpoint — only `eventimport` (create/update)
and user sync — so dedupe can't be "ask Teamworks if this already exists." Recommended
approach:

1. **Local ledger** (do this): a small local record — JSON file or a SQLite
   table — of `(eId, teamworks_user_id) -> existingEventId` for everything already
   uploaded.
   - Not in the ledger → `import_event()` with no `existingEventId` (creates),
     then store the returned `ids[0]`.
   - Already in the ledger → `import_event()` **with** that `existingEventId`
     (full resend — remember it replaces, not merges), refresh the entry.
   This doesn't depend on any Teamworks-side query capability and survives a
   scheduled run re-scanning the same time window.
2. **Store the `eId` on the form itself** too (a "Lympik Event ID" field), even
   though it isn't queryable via v1 — worth it as a manual audit trail so a human
   in AMS can trace an entry back to its Lympik session.

Because `eventimport` returns HTTP 200 even on failure, log every call's full
response body (success or `TeamworksAmsError`) next to the ledger update — a
partial failure mid-run shouldn't leave the ledger and AMS silently disagreeing
about what actually landed.

## What unblocks the rest

Once you have (a) a real Lympik event ID — or step 1 resolved — and (b) the AMS
form built with its exact field names, steps 5 and 6 can be written against real
shapes instead of documented-but-generic ones.
