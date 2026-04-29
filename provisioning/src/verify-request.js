/**
 * verify-request.js
 *
 * Parse a LoginConsentProvisioningRequest from the wallet.
 *
 * The wallet sends the request as a base64-encoded binary VDXF object.
 * We base64-decode it, deserialize using LoginConsentProvisioningRequest.fromBuffer,
 * extract the challenge_hash (via getChallengeHash()) and key fields.
 *
 * stdin JSON: { "request_json": { ... } }   -- for structured submission
 * OR:          { "request_b64": "..." }     -- for binary submission (future)
 */
"use strict";

const { LoginConsentProvisioningRequest } = require("verus-typescript-primitives");

function main() {
  const input = JSON.parse(process.argv[2] || process.stdin.read());
  const { request_json } = input;

  if (!request_json) {
    throw new Error("No request_json provided");
  }

  // Build the LoginConsentProvisioningRequest from the JSON representation
  // The request_json contains the challenge data that the wallet returned
  const req = new LoginConsentProvisioningRequest(request_json);

  // Extract the challenge hash (synchronous)
  const challengeHashBuf = req.getChallengeHash();
  const challengeHashHex = challengeHashBuf.toString("hex");

  // Extract key fields for Python to use
  const challengeData = req.challenge || {};
  const decisionId = request_json.decision_id || req.signing_id || "";

  const result = {
    challenge_id: challengeData.challenge_id || "",
    challenge_hash_hex: challengeHashHex,
    signing_address: req.signing_address || "",
    signing_id: req.signing_id || "",
    system_id: req.system_id || challengeData.system_id || "",
    name: challengeData.name || "",
    parent: challengeData.parent || "",
    request_json: req.toJson(),
  };

  console.log(JSON.stringify(result));
}

try {
  main();
} catch (err) {
  console.error(JSON.stringify({ error: err.message, stack: err.stack }));
  process.exit(1);
}
