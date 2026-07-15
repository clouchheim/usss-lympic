# Teamworks AMS API notes (v1)

Source: internal Teamworks AMS API reference doc. See `docs/teamworks-ams-notes.md`
for the confirmed-by-testing gotchas layered on top of this.

## Overview

| Version | Auth method | Notes |
|---|---|---|
| v1 | HTTP Basic Auth | Simplest to get started |
| v2 | Session-based authentication | Required for v2 endpoints |
| v3 | Session-based authentication | Uses the same session approach as v2 |

## Before you begin

- The AMS API does not support MFA, SSO, or Terms Documents. If your AMS instance
  enforces any of these, the API account must be exempted in the Admin portal.
- When saving a new object, set its `id` field to `-1` (don't use a specific ID for a
  new object — it may override an existing one).
- API permissions match the permissions in the AMS web and mobile apps. If the account
  can't access a form in the AMS UI, it can't access it through the API either.
- Test carefully before production use.

## v1: Synchronise Users

**Purpose**: returns every user the authenticated account has access to. Preferred way
to fetch users — cache results on first call, then pass the previous response's
`lastSynchronisationTimeOnServer` back in to get only changed/new users on later calls.

- **Endpoint**: `POST /api/v1/usersynchronise`
- **Auth**: HTTP Basic (AMS username/password)
- **Required query params**: `informat=json`, `format=json`
- **Optional header**: `X-APP-ID: <your integration's identifier>` (e.g.
  `usss.integration.v1`) — helps Teamworks support find your requests.

Request body:
```json
{
  "lastSynchronisationTimeOnServer": 0,
  "userIds": [],
  "paginate": "True",
  "cursor": ""
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `lastSynchronisationTimeOnServer` | number | yes | Server timestamp; use `0` to return all users, or a saved value to get only changes since then. |
| `userIds` | array | no | Optional list of user IDs to check for deletions/merges. |
| `paginate` | string | no | `"True"` to enable cursor pagination. |
| `cursor` | string | no | Cursor from the previous response; empty string on the first page. |

Pagination: 100 users per page. Pass the returned `cursor` into the next request; a
final page returns `cursor: null`.

Response notes:
- `lastSynchronisationTimeOnServer`: save and reuse on the next sync.
- `cursor`: present when more pages are available, `null` on the final page.
- `mergedUsers`: users merged since the previous sync.
- `idsOfDeletedUsers`: deleted user IDs, only for IDs you supplied in `userIds`.

**Coach account note**: for Coach/non-Admin accounts, this endpoint may not return
users who still exist on the server but were removed from a group the account can
access. Fallback for that case: `/api/v1/listgroups` + `/api/v1/groupmembers` (see
`docs/teamworks-ams-notes.md` for why we avoid `groupmembers` as the primary path).

## v1: Import Event Data

**Purpose**: creates a new event, or updates an existing one, against an AMS form.
Omit `existingEventId` (or leave it empty) to create; provide it to update.

- **Endpoint**: `POST /api/v1/eventimport`
- **Auth**: HTTP Basic (AMS username/password)
- **Required query params**: `informat=json`, `format=json`
- **Optional header**: `X-APP-ID: <your integration's identifier>`

Request body example:
```json
{
  "formName": "My Event Form",
  "startDate": "30/04/2026",
  "finishDate": "30/04/2026",
  "startTime": "9:00 AM",
  "finishTime": "10:00 AM",
  "userId": { "userId": 1009 },
  "existingEventId": "",
  "rows": [
    { "row": 0, "pairs": [ { "key": "Field Name", "value": "Field Value" } ] }
  ]
}
```

Required fields: `formName` (exact form name as in AMS), `startDate` (`dd/MM/yyyy`),
`finishDate` (`dd/MM/yyyy`), `startTime` (`h:mm AM/PM`), `userId` (`{"userId": <id>}`),
`rows`.

Optional fields: `finishTime` (defaults to one hour after `startTime`),
`enteredByUserId` (defaults to the authenticated account), `existingEventId` (omit/empty
to create new).

Row format — `rows` supports multiple rows for forms that allow it:
```json
{ "row": 0, "pairs": [ { "key": "Field Name", "value": "Value" } ] }
```
`row` is a zero-based index; `pairs` are field/value pairs for that row; `key` must
exactly match the field name from the AMS form builder (case-sensitive). To retrieve
exact field names: `GET /api/v3/forms/{form_type}/{form_id}`.

## Practical notes

- **Creating new objects**: use `{"id": -1}`. Don't use a specific ID for a new object
  — it may override an existing one.
- **Permissions**: the API respects the same permissions as AMS web/mobile. No UI
  access to a form means no API access to it either.
- **Recommended user-sync pattern**: call `usersynchronise` with
  `lastSynchronisationTimeOnServer: 0` once, cache the users, store the returned
  `lastSynchronisationTimeOnServer`, and pass that value back in on the next sync
  (with `paginate: "True"` and the returned `cursor` each time).
- **Recommended event-import pattern**: confirm the athlete's user ID, confirm the
  exact AMS form name, confirm exact field names from that form, build `rows` with
  those exact keys, then POST to `eventimport` — leaving `existingEventId` empty for a
  new event, or providing it only when updating one.
