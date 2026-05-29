import test from "node:test";
import assert from "node:assert/strict";

import { IdCreateClient } from "../src/client";

test("checkIdentityAvailability sends expected query and headers", async () => {
  const originalFetch = globalThis.fetch;
  const captured: { url?: string; method?: string; apiKey?: string } = {};

  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    captured.url = String(input);
    captured.method = init?.method;
    captured.apiKey = (init?.headers as Record<string, string>)["X-API-Key"];

    return new Response(
      JSON.stringify({
        available: true,
        fully_qualified_name: "alice.bitcoins.vrsc@",
        reason: null,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  }) as typeof fetch;

  try {
    const client = new IdCreateClient("http://localhost:5003", "key1", 5000);
    const result = await client.checkIdentityAvailability({
      name: "alice",
      parent: "bitcoins.vrsc",
      native_coin: "VRSC",
    });

    assert.equal(result.available, true);
    assert.equal(
      captured.url,
      "http://localhost:5003/api/check-availability?name=alice&native_coin=VRSC&parent=bitcoins.vrsc"
    );
    assert.equal(captured.method, "GET");
    assert.equal(captured.apiKey, "key1");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("checkIdentityAvailability omits parent when absent", async () => {
  const originalFetch = globalThis.fetch;
  const captured: { url?: string } = {};

  globalThis.fetch = (async (input: RequestInfo | URL) => {
    captured.url = String(input);
    return new Response(
      JSON.stringify({
        available: true,
        fully_qualified_name: "alice@",
        reason: null,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  }) as typeof fetch;

  try {
    const client = new IdCreateClient("http://localhost:5003", "key1", 5000);
    const result = await client.checkIdentityAvailability({
      name: "alice",
      native_coin: "VRSC",
    });

    assert.equal(result.fully_qualified_name, "alice@");
    assert.equal(captured.url, "http://localhost:5003/api/check-availability?name=alice&native_coin=VRSC");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
