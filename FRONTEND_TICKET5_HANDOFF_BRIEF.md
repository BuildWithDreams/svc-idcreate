# Frontend Handoff Brief: Ticket 5

## Purpose
Integrate identity availability checks into UI provisioning flow before registration submission.

This brief covers:
- Request and response contract for availability
- Debounced availability check UX
- Race condition handling when register returns 409
- Status polling behavior after successful register submission
- Example client SDK usage

## Backend Readiness
Backend support is available for:
- GET /api/check-availability
- POST /api/register
- GET /api/status/{request_id}

Key backend rules:
- native_coin is required for availability checks
- canonical identity formatting uses trailing @
- availability is advisory, register result is authoritative

## API Contract

### Availability endpoint
Method: GET
Path: /api/check-availability
Auth header: X-API-Key

Query parameters:
- name: string, required
- native_coin: string, required
- parent: string, optional

Successful response, HTTP 200:
- available: boolean
- fully_qualified_name: string
- reason: string or null

Example available response:
~~~json
{
  "available": true,
  "fully_qualified_name": "alice.bitcoins.vrsc@",
  "reason": null
}
~~~

Example unavailable response:
~~~json
{
  "available": false,
  "fully_qualified_name": "alice.bitcoins.vrsc@",
  "reason": "Identity already exists"
}
~~~

Error responses:
- 403 invalid or missing API key
- 400 invalid input
- 503 daemon unresolved or node degraded

### Register endpoint
Method: POST
Path: /api/register
Auth header: X-API-Key

Request body:
~~~json
{
  "name": "alice",
  "parent": "bitcoins.vrsc",
  "native_coin": "VRSC",
  "primary_raddress": "RaliceAddress"
}
~~~

Expected responses:
- 202 accepted, returns request_id for async flow
- 409 conflict if identity already exists, including race window after prior availability true

### Status endpoint
Method: GET
Path: /api/status/{request_id}
Auth: current deployment behavior for status route

Terminal statuses:
- complete
- failed

## Frontend UX Flow

1. User enters name and optional parent.
2. UI runs debounced availability check after 300 to 500 ms idle.
3. UI state mapping:
- checking while request in flight
- available when available is true
- unavailable when available is false
- degraded when endpoint returns 503
4. Submit button behavior:
- enabled when input is valid
- can still submit even if availability was true earlier, because race is possible
5. On submit, call register endpoint.
6. If register returns 409, show clear message that name was just taken and prompt user to choose another.
7. If register returns 202, start status polling by request_id.
8. Stop polling at complete or failed.

## Debounce and Request Management

Recommended behavior:
- Debounce: 300 to 500 ms
- Cancel stale in-flight availability requests when input changes
- Ignore out-of-order responses by request token sequence

Practical UI rules:
- If name or parent changes, reset availability badge to idle or checking
- Do not trust old availability response for new input

## Race Condition Handling

Important:
- Availability check is not a reservation lock.
- Final authority is POST /api/register.

If 409 is returned on register:
- Show: Name was just taken. Please try another name.
- Keep user input editable
- Trigger fresh availability check on next input pause

## Status Polling Behavior

Recommended defaults:
- Poll interval: 3 to 5 seconds
- Timeout budget: 2 to 5 minutes
- Stop conditions: complete or failed

Failure handling:
- If polling times out, show non-terminal timeout message and allow manual refresh
- If status endpoint errors transiently, retry until timeout budget exhausted

## TypeScript SDK Example

Use the TypeScript client methods:
- checkIdentityAvailability
- createIdentity
- getIdentityRequestStatus

~~~ts
import { IdCreateClient } from "../src/client";

const client = new IdCreateClient(baseUrl, apiKey);

const availability = await client.checkIdentityAvailability({
  name,
  parent,
  native_coin,
});

if (!availability.available) {
  // show unavailable UI
  return;
}

const created = await client.createIdentity({
  name,
  parent,
  native_coin,
  primary_raddress,
});

const requestId = created.request_id;
~~~

Polling sketch:
~~~ts
const deadline = Date.now() + 5 * 60 * 1000;
while (Date.now() < deadline) {
  const status = await client.getIdentityRequestStatus(requestId);
  const state = String(status.status ?? "unknown");
  if (state === "complete" || state === "failed") break;
  await new Promise((r) => setTimeout(r, 5000));
}
~~~

## Python SDK Example

Use the Python client methods:
- check_identity_availability
- create_identity
- get_identity_request_status

~~~python
availability = client.check_identity_availability(
    name="alice",
    parent="bitcoins.vrsc",
    native_coin="VRSC",
)

if availability["available"]:
    created = client.create_identity(
        name="alice",
        parent="bitcoins.vrsc",
        native_coin="VRSC",
        primary_raddress="RaliceAddress",
    )
~~~

## Acceptance Checklist for Frontend Ticket 5

- Availability check is debounced and uses current input only
- UI supports checking, available, unavailable, degraded states
- Register submit handles 202 success and 409 race conflict
- Status polling runs after 202 and stops at terminal states
- Error messaging is user-friendly and actionable
- Native coin is always included in availability and register flows
