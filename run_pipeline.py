"""One pass through the Lympik -> Teamworks AMS pipeline (steps 1-7).

Scheduling/interval is left to whatever runs this script (cron, a scheduled
task, etc.) -- this module is a single run.

STOPGAP, READ BEFORE RUNNING ON A SCHEDULE: this needs to know which
(Lympik event, Teamworks athlete) pairs already have a "Lympik Event" entry
in Teamworks, so we never upload the same athlete's session twice. There's
no confirmed Teamworks endpoint for listing existing form entries (see
PIPELINE.md) -- until there is, this uses a local JSON ledger (see
_load_ledger/_add_to_ledger below) as the source of truth instead. That's
real duplicate protection for repeated runs of *this script*, but it is not
the same as querying Teamworks directly, and it won't know about "Lympik
Event" entries created any other way.

The ledger is keyed per (event id, athlete), not per whole event: an early
version keyed it per whole event and failed a real test -- when one athlete
in an event failed to upload (while others in the same event succeeded), the
whole event was left off the ledger so it would retry, which would have
re-uploaded the athletes who *had* already succeeded as brand new duplicate
entries (there's no existingEventId tracking to turn a retry into an update
instead). Per-athlete ledgering means a retry only re-attempts the athlete(s)
that actually failed.
"""

import json
import logging
import math
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from athlete_matching import match_athletes, teamworks_user_id
from lympik_activity import get_recent_event_ids
from lympik_client import LympikClient
from teamworks_client import TeamworksClient

FORM_NAME = "Lympik Event"
LEDGER_PATH = Path("uploaded_events.json")

RUNS_DF_COLUMNS = [
    "firstName",
    "lastName",
    "Run ID",
    "Run Start unix Time",
    "Split 1",
    "Split 2",
    "Split 3",
    "Run Time",
    "DNF",
]

logger = logging.getLogger("lympik_pipeline")


def _stringify(value):
    """Every eventsimport value must be a string regardless of its real type
    (docs/teamworks-api-reference.md) -- and a missing split/run-time should
    become "" rather than the literal text "None"/"nan"."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value)


def _unix_to_ams_date_time(unix_ts, tz):
    dt = datetime.fromtimestamp(unix_ts, tz=tz)
    return dt.strftime("%d/%m/%Y"), dt.strftime("%I:%M %p").lstrip("0")


def _load_ledger():
    """Returns the set of (event_id, teamworks_user_id) pairs already
    successfully uploaded."""
    if not LEDGER_PATH.exists():
        return set()
    return {tuple(pair) for pair in json.loads(LEDGER_PATH.read_text())}


def _add_to_ledger(pairs):
    ledger = _load_ledger()
    ledger.update(pairs)
    LEDGER_PATH.write_text(json.dumps(sorted(list(pair) for pair in ledger), indent=2))


def build_runs_dataframe(lympik_client, event_id):
    """/event/{eId}/alpine-skiing/group -> one row per run. Runs with no
    assigned athlete (profile null/missing -- e.g. an unassigned DNF pulse)
    are dropped and logged, since there's no athlete to upload them against."""
    groups = list(lympik_client.get_all_pages(f"/event/{event_id}/alpine-skiing/group"))

    rows = []
    for group in groups:
        profile = group.get("profile")
        if not profile:
            logger.warning("event %s: run %s has no assigned athlete, skipping", event_id, group.get("id"))
            continue

        splits = {edge.get("sequence"): edge.get("duration") for edge in (group.get("edges") or [])}

        rows.append(
            {
                "firstName": profile.get("firstName"),
                "lastName": profile.get("lastName"),
                "Run ID": group.get("id"),
                "Run Start unix Time": group.get("startedAt"),
                "Split 1": splits.get(0),
                "Split 2": splits.get(1),
                "Split 3": splits.get(2),
                "Run Time": group.get("totalDuration"),
                "DNF": group.get("invalid") == "user_dnf",
            }
        )

    return pd.DataFrame(rows, columns=RUNS_DF_COLUMNS)


def _build_rows_payload(event_fields, athlete_runs_df):
    rows = [{"row": 0, "pairs": [{"key": k, "value": _stringify(v)} for k, v in event_fields.items()]}]

    for i, run in enumerate(athlete_runs_df.to_dict("records"), start=1):
        rows.append(
            {
                "row": i,
                "pairs": [
                    {"key": "Run #", "value": str(i)},
                    {"key": "Run ID", "value": _stringify(run["Run ID"])},
                    {"key": "Split 1", "value": _stringify(run["Split 1"])},
                    {"key": "Split 2", "value": _stringify(run["Split 2"])},
                    {"key": "Split 3", "value": _stringify(run["Split 3"])},
                    {"key": "Run Time", "value": _stringify(run["Run Time"])},
                    {"key": "DNF", "value": _stringify(run["DNF"])},
                ],
            }
        )
    return rows


def build_athlete_payloads(lympik_client, teamworks_athletes, event_id, tz):
    """Returns a list of {"event_id", "teamworks_user_id", "lympik_profile",
    "ams_event"} dicts, one per matched athlete in this Lympik event -- not
    filtered against the ledger or uploaded yet, since run() collects these
    across every event in the run, filters, and submits them together in as
    few eventsimport batches as possible. Unmatched athletes are logged as
    errors (with the event id) and skipped, never guessed."""
    event = lympik_client.get(f"/event/{event_id}")
    event_fields = {
        "Event ID": event["id"],
        "Session Name": event.get("name"),
        "Location": event.get("locationName"),
        "startedAt unix": event.get("startedAt"),
    }
    start_date, start_time = _unix_to_ams_date_time(event["startedAt"], tz)

    runs_df = build_runs_dataframe(lympik_client, event_id)
    if runs_df.empty:
        logger.info("event %s: no assigned runs, nothing to upload", event_id)
        return []

    # Grouped by name, not Lympik profile id: sample data showed the same
    # athlete can appear under two slightly different profile-id strings
    # across runs within one event, which would otherwise split one athlete
    # into multiple Teamworks uploads for the same session.
    lympik_profiles = [
        {"firstName": fn, "lastName": ln}
        for fn, ln in runs_df[["firstName", "lastName"]].drop_duplicates().itertuples(index=False)
    ]

    matched, unmatched, _ = match_athletes(
        lympik_profiles,
        teamworks_athletes,
        lympik_first_name_fn=lambda p: p["firstName"],
        lympik_last_name_fn=lambda p: p["lastName"],
    )

    for profile in unmatched:
        logger.error("event %s: no Teamworks match for %s %s", event_id, profile["firstName"], profile["lastName"])

    payloads = []
    for lympik_profile, teamworks_athlete in matched:
        athlete_runs_df = (
            runs_df[
                (runs_df["firstName"] == lympik_profile["firstName"])
                & (runs_df["lastName"] == lympik_profile["lastName"])
            ]
            .sort_values("Run Start unix Time")
            .reset_index(drop=True)
        )
        ams_event = {
            "formName": FORM_NAME,
            "startDate": start_date,
            "finishDate": start_date,
            "startTime": start_time,
            "userId": {"userId": teamworks_user_id(teamworks_athlete)},
            "rows": _build_rows_payload(event_fields, athlete_runs_df),
        }
        payloads.append(
            {
                "event_id": event_id,
                "teamworks_user_id": teamworks_user_id(teamworks_athlete),
                "sort_key": event.get("startedAt"),
                "lympik_profile": lympik_profile,
                "ams_event": ams_event,
            }
        )
    return payloads


def run(lympik_client, teamworks_client, since_unix, already_uploaded_pairs, tz):
    """already_uploaded_pairs has no default: callers must pass it explicitly
    (an empty set is fine for a first manual test) so a real gap in
    duplicate protection is never silent. Each item is an (event_id,
    teamworks_user_id) pair already successfully uploaded -- see module
    docstring re: the local-ledger stopgap in main()."""
    event_ids = get_recent_event_ids(lympik_client, since_unix)
    logger.info("%d recent event(s) in window", len(event_ids))

    teamworks_athletes = teamworks_client.list_athletes()

    all_payloads = []
    for event_id in event_ids:
        try:
            all_payloads.extend(build_athlete_payloads(lympik_client, teamworks_athletes, event_id, tz))
        except Exception:
            logger.exception("event %s: failed to prepare, will retry next run", event_id)

    pending = [p for p in all_payloads if (p["event_id"], p["teamworks_user_id"]) not in already_uploaded_pairs]
    logger.info("%d athlete-session(s) to upload (%d already in the ledger)", len(pending), len(all_payloads) - len(pending))

    # Oldest-first, per Teamworks' own eventsimport sample ("minimise
    # re-running historical calcs").
    pending.sort(key=lambda p: p["sort_key"])

    results = teamworks_client.bulk_import_events([p["ams_event"] for p in pending])

    newly_uploaded = []
    for payload, (_, teamworks_event_id, error) in zip(pending, results):
        profile = payload["lympik_profile"]
        athlete_label = f"{profile['firstName']} {profile['lastName']}"
        if error is not None:
            logger.error("event %s: upload failed for %s: %s", payload["event_id"], athlete_label, error)
        else:
            logger.info("event %s: uploaded %s -> Teamworks event %s", payload["event_id"], athlete_label, teamworks_event_id)
            newly_uploaded.append((payload["event_id"], payload["teamworks_user_id"]))

    _add_to_ledger(newly_uploaded)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    since_unix = time.time() - 86400
    tz = ZoneInfo(os.environ.get("PIPELINE_TIMEZONE", "America/Denver"))

    run(
        lympik_client=LympikClient(),
        teamworks_client=TeamworksClient(),
        since_unix=since_unix,
        already_uploaded_pairs=_load_ledger(),
        tz=tz,
    )


if __name__ == "__main__":
    main()
