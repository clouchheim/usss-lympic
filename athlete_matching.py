"""Step 3: match Lympik event profiles against the full Teamworks AMS athlete
roster.

Matching cascade (confirmed workable by a prior AMS integration, see
docs/teamworks-ams-notes.md): exact last name (case-insensitive) -> narrow by
first-initial -> narrow by full first name. Known limits, inherited from that
same integration: no fuzzy/accent normalization, no handling of hyphenated
names/middle names/suffixes, and a genuine duplicate name still produces an
ambiguous non-match. If either system can supply a stable id (athlete id,
DOB), prefer matching on that over name.

Lympik's profile schema is left as an untyped `object` in its spec, so the
Lympik-side name getters are passed in by the caller rather than hardcoded.
"""

import re

_TEAMWORKS_FIRST_NAME_KEYS = ["firstName", "first_name", "forename"]
_TEAMWORKS_LAST_NAME_KEYS = ["lastName", "last_name", "surname"]


def normalize_name(name):
    return re.sub(r"\s+", " ", name or "").strip().casefold()


def _first_present(record, keys):
    for key in keys:
        if record.get(key):
            return record[key]
    return None


def teamworks_first_name(athlete):
    return _first_present(athlete, _TEAMWORKS_FIRST_NAME_KEYS)


def teamworks_last_name(athlete):
    return _first_present(athlete, _TEAMWORKS_LAST_NAME_KEYS)


def match_athletes(
    lympik_profiles,
    teamworks_athletes,
    lympik_first_name_fn,
    lympik_last_name_fn,
    teamworks_first_name_fn=teamworks_first_name,
    teamworks_last_name_fn=teamworks_last_name,
):
    """Returns (matched, unmatched_lympik, unmatched_teamworks).

    matched: list of (lympik_profile, teamworks_athlete) pairs.
    unmatched_*: leftovers for manual review -- never guess past what the
    cascade resolves to a single candidate.
    """
    by_last_name = {}
    for athlete in teamworks_athletes:
        key = normalize_name(teamworks_last_name_fn(athlete))
        by_last_name.setdefault(key, []).append(athlete)

    matched = []
    unmatched_lympik = []
    claimed = set()

    for profile in lympik_profiles:
        first = normalize_name(lympik_first_name_fn(profile))
        last = normalize_name(lympik_last_name_fn(profile))

        candidates = by_last_name.get(last, [])
        if len(candidates) > 1:
            by_initial = [c for c in candidates if normalize_name(teamworks_first_name_fn(c))[:1] == first[:1]]
            if len(by_initial) > 1:
                by_full_first = [c for c in by_initial if normalize_name(teamworks_first_name_fn(c)) == first]
                candidates = by_full_first
            else:
                candidates = by_initial

        if len(candidates) == 1:
            matched.append((profile, candidates[0]))
            claimed.add(id(candidates[0]))
        else:
            unmatched_lympik.append(profile)

    unmatched_teamworks = [athlete for athlete in teamworks_athletes if id(athlete) not in claimed]

    return matched, unmatched_lympik, unmatched_teamworks
