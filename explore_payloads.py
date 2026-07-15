"""Step 1: sign in to Lympik and dump sample payloads to ./samples/ for inspection.

Usage:
    cp .env.example .env   # then fill in LYMPIK_PROFILE_ID / LYMPIK_API_KEY / LYMPIK_EVENT_ID
    pip install -r requirements.txt
    python explore_payloads.py

This only reads data (no writes to Lympik). Point LYMPIK_EVENT_ID at one event
you host or participate in -- the API has no "list my events" endpoint, so you
need to already know which event to inspect.
"""

import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

from lympik_client import LympikClient

load_dotenv()

SAMPLES_DIR = Path("samples")

# Endpoints available regardless of which module the event uses.
COMMON_ENDPOINTS = {
    "event_detail": "/event/{eId}",
    "event_profiles": "/event/{eId}/profile",
    "event_datasets": "/event/{eId}/dataset",
    "event_dataset_fields": "/event/{eId}/dataset/field",
    "profile_event_timing": "/profile/{pId}/event/{eId}/timing",
    # "event_list": "/event" -- confirmed 403; GET /event (list) isn't a
    # documented operation, only POST /event (create) and GET /event/{eId}.
    # "export_event_timing_excel": "/event/{eId}/timing/export" -- POST-only
    # and returns a binary .xlsx, not JSON, so it doesn't fit this client.
}

# Endpoints specific to each module type, keyed by the `module` value
# returned on the event detail payload.
MODULE_ENDPOINTS = {
    "event:timing": {
        "timing_devices": "/event/{eId}/timing/device",
        "timing_groups": "/event/{eId}/timing/group",
        # Add one with eId and pId
    },
    "event:alpine-skiing": {
        "alpine_devices": "/event/{eId}/alpine-skiing/device",
        "alpine_groups": "/event/{eId}/alpine-skiing/group",
        "alpine_event_profile" : "/profile/{pId}/event/{eId}/alpine-skiing",
        "alpine_event_profile_stats" : "/profile/{pId}/module/event/alpine-skiing/statistic",
    },
    "event:competition": {
        "competition_categories": "/event/{eId}/competition/category",
        "competition_registrations": "/event/{eId}/competition/registration",
        "competition_groups": "/event/{eId}/competition/group",
    },
    "event:motion": {
        "motion_indicators": "/event/{eId}/motion/performance-indicator",
        "profile_event_motion" : "/profile/{pId}/event/{eId}/motion",
    },
    "event:video": {
        "videos": "/event/{eId}/video/video",
        "profile_video": "/profile/{pId}/event/{eId}/video",
        # Add download video once I get a vId /event/{eId}/video/video/{vId}/resolution/original
    },
    "event:weather": {
        "weather_measurements": "/event/{eId}/weather/measurement",
    },
}

# For each module, the "group" list endpoint whose items we drill into for
# per-run/per-session edge (split time) detail.
GROUP_ENDPOINT_BY_MODULE = {
    "event:timing": "timing_groups",
    "event:alpine-skiing": "alpine_groups",
    "event:competition": "competition_groups",
}

EDGE_PATH_BY_MODULE = {
    "event:timing": "/event/{eId}/timing/group/{gId}/edge",
    "event:alpine-skiing": "/event/{eId}/alpine-skiing/group/{gId}/edge",
    "event:competition": "/event/{eId}/competition/group/{gId}/edge",
}


def dump(name, payload):
    SAMPLES_DIR.mkdir(exist_ok=True)
    path = SAMPLES_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))

    if isinstance(payload, dict) and "records" in payload:
        count = len(payload["records"])
        print(f"  {name}: {count} record(s) (of {payload.get('totalCount', count)} total) -> {path}")
    elif isinstance(payload, list):
        print(f"  {name}: {len(payload)} item(s) -> {path}")
    else:
        print(f"  {name}: saved -> {path}")


def fetch(client, name, path, **path_params):
    try:
        payload = client.get(path.format(**path_params))
        dump(name, payload)
        return payload
    except requests.HTTPError as exc:
        print(f"  {name}: skipped ({exc.response.status_code} {exc.response.reason})")
        return None


def fetch_all(client, name, path, **path_params):
    """Like fetch(), but for paginated list endpoints -- walks every page up front."""
    try:
        records = list(client.get_all_pages(path.format(**path_params)))
        dump(name, records)
        return records
    except requests.HTTPError as exc:
        print(f"  {name}: skipped ({exc.response.status_code} {exc.response.reason})")
        return []


def scan_for_event_refs(records):
    """Flags field names on the first record that might reference an event,
    since these list schemas are undocumented (`type: object`) in the spec."""
    if not records or not isinstance(records[0], dict):
        return []
    return [key for key in records[0] if "event" in key.lower()]


def discover(client):
    """Endpoints reachable with only your own profile ID -- no eId required.
    Everything here gets dumped to samples/discover_*.json so you can check
    for an embedded event reference before ever setting LYMPIK_EVENT_ID."""
    print("Looking for a way to reach an event without a hardcoded eId...")

    devices = fetch_all(client, "discover_profile_devices", "/profile/{pId}/device", pId=client.profile_id)
    for key in scan_for_event_refs(devices):
        print(f"  -> discover_profile_devices has a '{key}' field, check it")

    for device in devices:
        device_id = device.get("id")
        if not device_id:
            continue

        datasets = fetch_all(client, f"discover_device_{device_id}_datasets", "/device/{id}/dataset", id=device_id)
        for key in scan_for_event_refs(datasets):
            print(f"  -> discover_device_{device_id}_datasets has a '{key}' field, check it")

        messages = fetch_all(
            client, f"discover_device_{device_id}_timing_messages", "/device/{id}/message/timing", id=device_id
        )
        for key in scan_for_event_refs(messages):
            print(f"  -> discover_device_{device_id}_timing_messages has a '{key}' field, check it")

    fetch(
        client,
        "discover_alpine_statistics",
        "/profile/{pId}/module/event/alpine-skiing/statistic",
        pId=client.profile_id,
    )
    fetch_all(
        client, "discover_motion_templates", "/profile/{pId}/module/event/motion/template", pId=client.profile_id
    )

    print(f"Discovery samples saved under {SAMPLES_DIR}/discover_*.json\n")


def main():
    client = LympikClient()
    profile_id = client.profile_id

    discover(client)

    event_id = os.environ.get("LYMPIK_EVENT_ID")
    if not event_id:
        print("LYMPIK_EVENT_ID not set -- skipping the per-event drill-down.")
        print(f"Check {SAMPLES_DIR}/discover_*.json for anything that looks like an event id, then set it and re-run.")
        return

    print(f"Fetching samples for event {event_id}")

    for name, path in COMMON_ENDPOINTS.items():
        fetch(client, name, path, eId=event_id, pId=profile_id)

    event_detail = json.loads((SAMPLES_DIR / "event_detail.json").read_text())
    module = event_detail.get("module")
    print(f"Event module: {module}")

    module_endpoints = MODULE_ENDPOINTS.get(module, {})
    for name, path in module_endpoints.items():
        fetch(client, name, path, eId=event_id, pId=profile_id)

    group_endpoint_name = GROUP_ENDPOINT_BY_MODULE.get(module)
    edge_path = EDGE_PATH_BY_MODULE.get(module)
    if group_endpoint_name and edge_path:
        groups_payload = json.loads((SAMPLES_DIR / f"{group_endpoint_name}.json").read_text())
        sample_groups = groups_payload.get("records", [])[:3]
        for group in sample_groups:
            gid = group.get("id")
            if gid:
                fetch(client, f"{group_endpoint_name}_{gid}_edges", edge_path, eId=event_id, pId=profile_id, gId=gid)

    print(f"\nDone. Inspect the payloads under {SAMPLES_DIR}/")


if __name__ == "__main__":
    main()
