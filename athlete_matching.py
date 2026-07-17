"""Step 3: match Lympik event profiles against the full Teamworks AMS athlete
roster.

Matching cascade: exact full name (first+last, case-insensitive) first, since
there's no reason not to take a complete match when one uniquely exists. If
that doesn't resolve to exactly one athlete, fall back to the cascade
confirmed workable by a prior AMS integration (see
docs/teamworks-ams-notes.md): exact last name -> narrow by first-initial ->
if still ambiguous, keep narrowing by one more letter of the first name at a
time (2 letters, 3 letters, ...) until either one candidate remains or the
Lympik-supplied first name runs out of letters to add, whichever comes
first. Known limits, inherited from that same integration: no fuzzy/accent
normalization, no handling of hyphenated names/middle names/suffixes, and a
genuine duplicate name still produces an ambiguous non-match. If either
system can supply a stable id (athlete id, DOB), prefer matching on that
over name.

Lympik's profile schema is left as an untyped `object` in its spec, so the
Lympik-side name getters are passed in by the caller rather than hardcoded.
"""

import re

_TEAMWORKS_FIRST_NAME_KEYS = ["firstName", "first_name", "forename"]
_TEAMWORKS_LAST_NAME_KEYS = ["lastName", "last_name", "surname"]
_TEAMWORKS_USER_ID_KEYS = ["userId", "user_id", "id"]


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


def teamworks_user_id(athlete):
    return _first_present(athlete, _TEAMWORKS_USER_ID_KEYS)


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
    by_full_name = {}
    for athlete in teamworks_athletes:
        last_key = normalize_name(teamworks_last_name_fn(athlete))
        by_last_name.setdefault(last_key, []).append(athlete)
        full_key = (normalize_name(teamworks_first_name_fn(athlete)), last_key)
        by_full_name.setdefault(full_key, []).append(athlete)

    matched = []
    unmatched_lympik = []
    claimed = set()

    for profile in lympik_profiles:
        first = normalize_name(lympik_first_name_fn(profile))
        last = normalize_name(lympik_last_name_fn(profile))

        candidates = by_full_name.get((first, last), [])
        if len(candidates) != 1:
            candidates = by_last_name.get(last, [])
            # First initial (k=1), then one more letter at a time, only
            # while it actually narrows the pool -- stop as soon as one
            # candidate remains, or once `first` has no more letters to
            # offer (an empty narrowing means the extra letter didn't
            # distinguish anyone further, so keep the wider pool rather
            # than discarding everyone).
            for k in range(1, len(first) + 1):
                if len(candidates) <= 1:
                    break
                narrowed = [c for c in candidates if normalize_name(teamworks_first_name_fn(c))[:k] == first[:k]]
                if narrowed:
                    candidates = narrowed

            # A last-name pool that was only ever one athlete never went
            # through the loop above, so a shared last name alone would
            # otherwise stand in for actually checking the first name at
            # all (e.g. "RTS2 Smith" silently matching the sole "RTS1
            # Smith" in Teamworks). Verify the first name agrees over
            # however many letters both names actually have, rather than
            # accepting a lone last-name candidate unconditionally.
            if len(candidates) == 1:
                candidate_first = normalize_name(teamworks_first_name_fn(candidates[0]))
                common_len = min(len(first), len(candidate_first))
                if first[:common_len] != candidate_first[:common_len]:
                    candidates = []

        if len(candidates) == 1:
            matched.append((profile, candidates[0]))
            claimed.add(id(candidates[0]))
        else:
            unmatched_lympik.append(profile)

    unmatched_teamworks = [athlete for athlete in teamworks_athletes if id(athlete) not in claimed]

    return matched, unmatched_lympik, unmatched_teamworks
