/**
 * base58check.js
 * Provides base58check encode/decode for Python.
 * argv[2]: { action: "encode" | "decode", data_hex: "..." | "address": "..." }
 */
"use strict";

const { toBase58Check, fromBase58Check } = require("verus-typescript-primitives");

function main() {
  const input = JSON.parse(process.argv[2]);

  if (input.action === "encode") {
    const result = toBase58Check(Buffer.from(input.data_hex, "hex"), input.version || 0);
    console.log(JSON.stringify({ result }));
  } else if (input.action === "decode") {
    const decoded = fromBase58Check(input.address);
    console.log(JSON.stringify({
      version: decoded.version,
      hash: decoded.hash.toString("hex"),
    }));
  } else {
    throw new Error(`Unknown action: ${input.action}`);
  }
}

try {
  main();
} catch (err) {
  console.error(JSON.stringify({ error: err.message }));
  process.exit(1);
}
