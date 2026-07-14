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
}

# Endpoints specific to each module type, keyed by the `module` value
# returned on the event detail payload.
MODULE_ENDPOINTS = {
    "event:timing": {
        "timing_devices": "/event/{eId}/timing/device",
        "timing_groups": "/event/{eId}/timing/group",
    },
    "event:alpine-skiing": {
        "alpine_devices": "/event/{eId}/alpine-skiing/device",
        "alpine_groups": "/event/{eId}/alpine-skiing/group",
    },
    "event:competition": {
        "competition_categories": "/event/{eId}/competition/category",
        "competition_registrations": "/event/{eId}/competition/registration",
        "competition_groups": "/event/{eId}/competition/group",
    },
    "event:motion": {
        "motion_indicators": "/event/{eId}/motion/performance-indicator",
    },
    "event:video": {
        "videos": "/event/{eId}/video/video",
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


def main():
    event_id = os.environ["LYMPIK_EVENT_ID"]
    client = LympikClient()

    print(f"Fetching samples for event {event_id}")

    for name, path in COMMON_ENDPOINTS.items():
        fetch(client, name, path, eId=event_id)

    event_detail = json.loads((SAMPLES_DIR / "event_detail.json").read_text())
    module = event_detail.get("module")
    print(f"Event module: {module}")

    module_endpoints = MODULE_ENDPOINTS.get(module, {})
    for name, path in module_endpoints.items():
        fetch(client, name, path, eId=event_id)

    group_endpoint_name = GROUP_ENDPOINT_BY_MODULE.get(module)
    edge_path = EDGE_PATH_BY_MODULE.get(module)
    if group_endpoint_name and edge_path:
        groups_payload = json.loads((SAMPLES_DIR / f"{group_endpoint_name}.json").read_text())
        sample_groups = groups_payload.get("records", [])[:3]
        for group in sample_groups:
            gid = group.get("id")
            if gid:
                fetch(client, f"{group_endpoint_name}_{gid}_edges", edge_path, eId=event_id, gId=gid)

    print(f"\nDone. Inspect the payloads under {SAMPLES_DIR}/")


if __name__ == "__main__":
    main()
