"""Step 4: section one Lympik alpine-skiing event into a per-athlete session
structure, ready to be matched against Teamworks AMS athletes and transformed
into the Teamworks upload payload.

Each athlete gets exactly one record per event (eId + athlete profile id is
the de-dup key downstream), containing the runs table their Teamworks AMS
form entry needs.
"""

import requests


def get_event_sessions(client, event_id):
    """Returns:
        {
            "event": {"id": ..., "name": ..., "started_at": ...},
            "athletes": {
                profile_id: {"profile": {...}, "runs": [{"group_id", "label", "invalid", "edges"}, ...]},
                ...
            },
        }
    """
    event = client.get(f"/event/{event_id}")

    profiles = list(client.get_all_pages(f"/event/{event_id}/profile"))
    profiles_by_id = {profile["id"]: profile for profile in profiles if profile.get("id")}

    groups = list(client.get_all_pages(f"/event/{event_id}/alpine-skiing/group"))

    athletes = {}
    for group in groups:
        profile_id = _extract_profile_id(group)
        if not profile_id:
            continue  # run not yet assigned to an athlete -- nothing to upload for it

        try:
            edges = client.get(f"/event/{event_id}/alpine-skiing/group/{group['id']}/edge")
        except requests.HTTPError:
            edges = None

        run = {
            "group_id": group.get("id"),
            "label": group.get("label"),
            "invalid": group.get("invalid"),
            "edges": edges,
        }

        athlete = athletes.setdefault(profile_id, {"profile": profiles_by_id.get(profile_id), "runs": []})
        athlete["runs"].append(run)

    return {
        "event": {"id": event_id, "name": event.get("name"), "started_at": event.get("startedAt")},
        "athletes": athletes,
    }


def _extract_profile_id(group):
    """The alpine-skiing group schema is left as an untyped `object` in the
    spec, so the exact shape of its athlete association is unconfirmed --
    this handles both a plain uuid string and a nested {"id": uuid} object.
    Verify against a real sample once an eId is available to test against.
    """
    profile = group.get("profile")
    if isinstance(profile, dict):
        return profile.get("id")
    return profile
