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

logger = logging.getLogger("teamworks_client")


class TeamworksAmsError(Exception):
    """Raised when an import endpoint returns HTTP 200 with a non-success body.
    These endpoints return 200 even on failure -- raise_for_status() alone
    will not catch it, so the response body must always be checked."""


class TeamworksClient:
    def __init__(self, base_url=None, username=None, password=None, app_id=None):
        self.base_url = (base_url or os.environ["TEAMWORKS_BASE_URL"]).rstrip("/")
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

        Returns a list of (event, event_id, error) tuples, one per input
        event, in the same order as `events` -- event_id is the resulting
        Teamworks event id on success and None on failure, error is the
        exception on failure and None on success.
        """
        results = [None] * len(events)

        for start in range(0, len(events), batch_size):
            batch = events[start : start + batch_size]
            indices = range(start, start + len(batch))
            try:
                batch_ids = self._post_eventsimport(batch)
                for idx, event_id in zip(indices, batch_ids):
                    results[idx] = (events[idx], event_id, None)
            except TeamworksAmsError:
                logger.warning(
                    "batch of %d event(s) failed as a whole, retrying individually to isolate the cause", len(batch)
                )
                for idx in indices:
                    try:
                        single_result_ids = self._post_eventsimport([events[idx]])
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
