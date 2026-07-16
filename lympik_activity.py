"""Step 1-2: discover recent Lympik event IDs via /profile/{pId}/activity/search.

This was the previously-missing discovery endpoint -- absent from the
published OpenAPI spec, but reachable via the `activity.search` key scope
granted on the API key.
"""


def get_recent_event_ids(client, since_unix, data_type="timing"):
    """Returns the sorted, de-duplicated set of event ids with activity of
    `data_type` at or after `since_unix` (a unix timestamp). Each item in the
    activity search response is an event summary with its id at the top
    level (alongside `name`/`startedAt`/`module`), not nested under a
    `records` key."""
    activity = client.search_activity(since_unix, data_type)
    return sorted({item["id"] for item in activity if item.get("id")})
