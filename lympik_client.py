"""Thin client for the Lympik Cloud API (https://api.lympik.com/v1).

Auth is HTTP Basic where username = profile UUID and password = personal API key,
per the `personalToken` security scheme in Lympik's OpenAPI spec.
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BASE_URL = "https://api.lympik.com/v1"


class LympikClient:
    def __init__(self, profile_id=None, api_key=None, base_url=None):
        self.profile_id = profile_id or os.environ["LYMPIK_PROFILE_ID"]
        self.api_key = api_key or os.environ["LYMPIK_API_KEY"]
        self.base_url = (base_url or os.environ.get("LYMPIK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")

        self.session = requests.Session()
        self.session.auth = (self.profile_id, self.api_key)

    def get(self, path, params=None):
        response = self.session.get(f"{self.base_url}{path}", params=params, timeout=30)
        response.raise_for_status()
        return response.json() if response.content else None

    def search_activity(self, date_from, data_type="timing"):
        """GET /profile/{pId}/activity/search -- not in the published OpenAPI
        spec, reachable via the `activity.search` key scope. Returns a plain
        list (no offset/size pagination wrapper), unlike most other list
        endpoints in the documented spec."""
        return self.get(f"/profile/{self.profile_id}/activity/search", params={"dateFrom": date_from, "dataType": data_type})

    def get_all_pages(self, path, params=None, size=100):
        """Follows the offset/size pagination used by list endpoints,
        yielding every record across all pages."""
        offset = 0
        params = dict(params or {})
        while True:
            params.update({"offset": offset, "size": size})
            page = self.get(path, params=params)
            records = page.get("records", [])
            yield from records
            offset += len(records)
            if offset >= page.get("totalCount", 0) or not records:
                return
