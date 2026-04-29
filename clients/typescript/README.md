# TypeScript Client (Phase 0)

Thin client for the identity creation service.

## Features

- health check
- create identity request
- status lookup
- list recent failures
- requeue webhook delivery

## Quick example

```ts
import { IdCreateClient } from "./src/client";

const client = new IdCreateClient("http://localhost:5003", "key1");

async function run() {
  const created = await client.createIdentity({
    name: "alice",
    parent: "bitcoins.vrsc",
    native_coin: "VRSC",
    primary_raddress: "RaliceAddress",
  });

  const status = await client.getIdentityRequestStatus(created.request_id);
  console.log(status);
}

run().catch(console.error);
```

## Example script

`examples/createAndPoll.ts` creates a registration request and polls until terminal status.

Run from repo root (Node 18+):

```bash
cd clients/typescript
npm install
IDCREATE_BASE_URL="http://localhost:5003" IDCREATE_API_KEY="key1" npm run example
```
