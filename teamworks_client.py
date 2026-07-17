"""Thin client for the Teamworks AMS v1 API (HTTP Basic Auth).

Behavior here follows docs/teamworks-api-reference.md plus the confirmed
gotchas in docs/teamworks-ams-notes.md from a prior AMS integration -- AMS
forms are no-code/user-configurable, so the real behavior of the import
endpoints has gaps versus their published schema.
"""

import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BATCH_SIZE = 25  # per Teamworks' own sample: start small, raise only after measuring.
DEFAULT_BASE_URL = "https://usopc.smartabase.com/athlete360-usss"

logger = logging.getLogger("teamworks_client")


class TeamworksAmsError(Exception):
    """Raised when an import endpoint returns HTTP 200 with a non-success body.
    These endpoints return 200 even on failure -- raise_for_status() alone
    will not catch it, so the response body must always be checked."""


class TeamworksClient:
    def __init__(self, base_url=None, username=None, password=None, app_id=None):
        self.base_url = (base_url or os.environ.get("TEAMWORKS_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.username = username or os.environ["TEAMWORKS_USERNAME"]
        self.password = password or os.environ["TEAMWORKS_PASSWORD"]
        self.app_id = app_id or os.environ.get("TEAMWORKS_APP_ID", "usss.lympik-integration")

        self.session = requests.Session()
        self.session.auth = (self.username, self.password)
        self.session.headers["X-APP-ID"] = self.app_id

    def list_athletes(self):
        """Walks /api/v1/usersynchronise to completion and returns every user
        the API account can see (group membership doesn't matter, unlike
        /api/v1/groupmembers). Always starts a full sync from
        lastSynchronisationTimeOnServer=0 -- a caller wanting Teamworks'
        incremental delta-sync should track and pass that value itself."""
        users = []
        cursor = ""
        while True:
            body = {
                "lastSynchronisationTimeOnServer": 0,
                "userIds": [],
                "paginate": "True",
                "cursor": cursor,
            }
            response = self.session.post(
                f"{self.base_url}/api/v1/usersynchronise",
                params={"informat": "json", "format": "json"},
                json=body,
                timeout=30,
            )
            response.raise_for_status()
            page = response.json()

            users.extend(_find_user_list(page))

            cursor = page.get("cursor")
            if not cursor:
                return users

    def find_existing_event_ids(self, form_name, start_date, user_ids, event_id_field, candidate_event_ids):
        """Which (event id, Teamworks user id) pairs already have a
        `form_name` entry in Teamworks on/after start_date, for the given
        user_ids -- queried fresh from Teamworks itself every run instead of
        trusting a separately maintained ledger file.

        Confirmed working shape via a sibling Teamworks AMS integration
        (different org, same v1 API family): POST /api/v1/synchronise with
        {"formName", "startDate", "userIds"}, paginated via
        {"pagination": {"paginate": True, "cursor": ...}} on every page
        after the first, response events under body["export"]["events"].
        Not yet confirmed against this specific AMS instance -- every call
        here dumps its raw response to debug_payloads/synchronise_response.json
        (see run_pipeline.py) so a shape mismatch can be caught and fixed
        instead of silently returning nothing.

        userIds is mandatory on this endpoint: omitting it returns no
        events for anyone, not "all events" -- so empty user_ids or
        candidate_event_ids always short-circuits rather than risking a
        call that would silently mean something different than intended.

        event_id_field is the row-0 field name holding our event id (e.g.
        "Event ID") -- checked first on each returned event.
        candidate_event_ids is the specific set of ids being asked about
        this run; if the row-0 lookup doesn't land on one of them, every
        string leaf in the event is checked against the same set as a
        fallback, since these are distinctive UUIDs unlikely to appear by
        accident.

        Returns (found, raw_responses): found is a set of
        (str(event_id), str(teamworks_user_id)) pairs -- both sides
        stringified since this endpoint's id types aren't confirmed to
        match usersynchronise's exactly. raw_responses is the list of raw
        response bodies (one per page), for the caller to dump for
        inspection.
        """
        candidate_ids = {str(cid) for cid in candidate_event_ids}
        if not candidate_ids or not user_ids:
            return set(), []

        found = set()
        raw_responses = []
        cursor = None
        base_body = {
            "formName": form_name,
            "startDate": start_date,
            "userIds": sorted(user_ids),
        }
        while True:
            body = dict(base_body)
            if cursor:
                body["pagination"] = {"paginate": True, "cursor": cursor}

            response = self.session.post(
                f"{self.base_url}/api/v1/synchronise",
                params={"informat": "json", "format": "json"},
                json=body,
                timeout=30,
            )
            response.raise_for_status()
            page = response.json()
            raw_responses.append(page)

            export = page.get("export") or {}
            for event in export.get("events", []):
                event_id = _extract_field_value(event, event_id_field)
                if str(event_id) not in candidate_ids:
                    event_id = next((v for v in _walk_strings(event) if v in candidate_ids), None)
                user_id = _event_user_id(event)
                if event_id is not None and user_id is not None:
                    found.add((str(event_id), str(user_id)))

            cursor = page.get("cursor") or export.get("cursor")
            if not cursor:
                break

        events_seen = sum(len((p.get("export") or {}).get("events", [])) for p in raw_responses)
        if events_seen and not found:
            logger.warning(
                "synchronise returned %d event(s) for these users but none matched a known "
                "event id/user id shape -- check debug_payloads/synchronise_response.json",
                events_seen,
            )

        return found, raw_responses

    def bulk_import_events(self, events, batch_size=DEFAULT_BATCH_SIZE):
        """POSTs /api/v1/eventsimport in batches of `batch_size`.

        Each item in `events` is a single event dict (formName/startDate/
        startTime/userId/rows/... -- same shape as a single-event import).
        All events in one call must target the same form -- this only reads
        eventImportResultForForm[0], the correct index only when every event
        in the batch uses the same form name (true for this pipeline, which
        only ever submits "Lympik Event").

        This endpoint is all-or-nothing per batch: a single malformed event
        fails the *entire* batch, and the API does not say which one is at
        fault. When a batch fails, this method automatically retries every
        event in that batch individually (batch size 1) so one bad payload
        doesn't block its batch-mates and the caller learns exactly which
        event failed and why.

        Teamworks can also report a batch as SUCCESSFULLY_IMPORTED while
        silently returning fewer ids than events submitted (seen in practice
        for a userId Teamworks can't actually deliver a "Lympik Event" entry
        to) -- there's no per-event error for this, just a shorter list. That
        id-count mismatch is treated the same as a batch failure: retried
        individually so the caller learns exactly which event didn't
        actually go through, instead of silently misaligning results by
        position.

        Returns a list of (event, event_id, error) tuples, one per input
        event, in the same order as `events` -- event_id is the resulting
        Teamworks event id on success and None on failure, error is the
        exception on failure and None on success.
        """
        results = [None] * len(events)

        for start in range(0, len(events), batch_size):
            batch = events[start : start + batch_size]
            indices = range(start, start + len(batch))

            batch_ids = None
            try:
                batch_ids = self._post_eventsimport(batch)
            except TeamworksAmsError:
                logger.warning(
                    "batch of %d event(s) failed as a whole, retrying individually to isolate the cause", len(batch)
                )

            if batch_ids is not None and len(batch_ids) != len(batch):
                logger.warning(
                    "batch of %d event(s) reported success but returned %d id(s) -- "
                    "at least one event was silently dropped, retrying individually to isolate which",
                    len(batch),
                    len(batch_ids),
                )
                batch_ids = None

            if batch_ids is not None:
                for idx, event_id in zip(indices, batch_ids):
                    results[idx] = (events[idx], event_id, None)
                continue

            for idx in indices:
                try:
                    single_result_ids = self._post_eventsimport([events[idx]])
                    if len(single_result_ids) != 1:
                        raise TeamworksAmsError(
                            f"expected exactly 1 id for a single-event import, got {single_result_ids!r}"
                        )
                    results[idx] = (events[idx], single_result_ids[0], None)
                except TeamworksAmsError as exc:
                    results[idx] = (events[idx], None, exc)

        return results

    def _post_eventsimport(self, events):
        response = self.session.post(
            f"{self.base_url}/api/v1/eventsimport",
            params={"informat": "json", "format": "json"},
            json={"events": events},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()

        state = (body.get("result") or {}).get("state")
        if state != "SUCCESSFULLY_IMPORTED":
            raise TeamworksAmsError(f"{state}: {(body.get('result') or {}).get('message')} (raw body: {body})")

        return body["eventImportResultForForm"][0]["eventImportResults"]["ids"]


def _find_user_list(response_json):
    """The user list is wrapped under an implementation-specific key that
    varies by AMS instance -- find it by shape (the first list-of-dicts
    value in the response) rather than hardcoding a key name."""
    for value in response_json.values():
        if isinstance(value, list) and (not value or isinstance(value[0], dict)):
            return value
    return []


def _extract_field_value(event, field_name):
    """Row 0 holds event-level fields (Event ID, Session Name, ...) --
    confirmed shape for eventimport/eventsimport requests; assumed
    symmetric for synchronise responses until confirmed otherwise."""
    for row in event.get("rows", []):
        if row.get("row") == 0:
            for pair in row.get("pairs", []):
                if pair.get("key") == field_name:
                    return pair.get("value")
    return None


def _walk_strings(node):
    """Yield every string leaf value in an arbitrarily nested dict/list --
    fallback for locating a known id when the row-0 shape doesn't apply."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _walk_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_strings(item)


def _event_user_id(event):
    """userId comes back as {"userId": N} in every request shape this
    client sends -- try that first, then a bare value as a fallback."""
    raw = event.get("userId")
    if isinstance(raw, dict):
        return raw.get("userId")
    return raw
