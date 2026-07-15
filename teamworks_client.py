"""Thin client for the Teamworks AMS v1 API (HTTP Basic Auth).

Behavior here follows docs/teamworks-api-reference.md plus the confirmed
gotchas in docs/teamworks-ams-notes.md from a prior AMS integration -- AMS
forms are no-code/user-configurable, so the real behavior of eventimport has
gaps versus its published schema.
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv()


class TeamworksAmsError(Exception):
    """Raised when /api/v1/eventimport returns HTTP 200 with a non-success body.
    This endpoint returns 200 even on failure -- raise_for_status() alone
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

    def import_event(
        self,
        form_name,
        start_date,
        start_time,
        user_id,
        rows,
        finish_date=None,
        finish_time=None,
        existing_event_id=None,
    ):
        """POSTs /api/v1/eventimport.

        rows must already be split per the confirmed working shape for a
        single-value-fields-plus-table form: row 0's pairs = only the
        single-value/event-level fields (never a table column), rows 1..N =
        one table row each. Passing existing_event_id replaces that event's
        entire contents -- it does not merge -- so always send the full
        desired state, including everything from a prior call.
        """
        payload = {
            "formName": form_name,
            "startDate": start_date,
            "finishDate": finish_date or start_date,
            "startTime": start_time,
            "userId": {"userId": user_id},
            "existingEventId": existing_event_id or "",
            "rows": rows,
        }
        if finish_time:
            payload["finishTime"] = finish_time

        response = self.session.post(
            f"{self.base_url}/api/v1/eventimport",
            params={"informat": "json", "format": "json"},
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()

        if body.get("state") != "SUCCESSFULLY_IMPORTED":
            raise TeamworksAmsError(f"{body.get('state')}: {body.get('message')} (raw body: {body})")

        return body["ids"]


def _find_user_list(response_json):
    """The user list is wrapped under an implementation-specific key that
    varies by AMS instance -- find it by shape (the first list-of-dicts
    value in the response) rather than hardcoding a key name."""
    for value in response_json.values():
        if isinstance(value, list) and (not value or isinstance(value[0], dict)):
            return value
    return []
