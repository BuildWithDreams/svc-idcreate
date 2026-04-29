/**
 * build-challenge.js
 * Reads JSON from stdin: { challenge_id, name, system_id, parent, created_at }
 * Returns JSON: { challenge, challenge_id, deeplink_uri, flags, vdxfkey }
 */
"use strict";

const {
  LoginConsentProvisioningChallenge,
  toBase58Check,
  fromBase58Check,
  LOGIN_CONSENT_PROVISIONING_CHALLENGE_VDXF_KEY,
  PROVISION_IDENTITY_DETAILS_VDXF_KEY,
} = require("verus-typescript-primitives");

function validateBase58Field(fieldName, value) {
  if (!value || typeof value !== "string") {
    throw new Error(`Invalid ${fieldName}: empty`);
  }
  try {
    const decoded = fromBase58Check(value);
    return {
      field: fieldName,
      version: decoded.version,
      hashLen: decoded.hash.length,
      preview: `${value.slice(0, 8)}...${value.slice(-4)}`,
    };
  } catch (err) {
    throw new Error(`Invalid ${fieldName} base58check: ${err.message}`);
  }
}

function main() {
  const input = JSON.parse(process.argv[2] || process.stdin.read());
  const { name, system_id, parent, callback_url } = input;

  // challenge_id and created_at come from python side via env or stdin
  const challengeId = input.challenge_id;
  const createdAt = input.created_at;

  if (!challengeId || !name || !system_id || !parent) {
    throw new Error("Missing required fields: challenge_id, name, system_id, parent");
  }

  const diagnostics = {
    challenge: validateBase58Field("challenge_id", challengeId),
    system: validateBase58Field("system_id", system_id),
    parent: validateBase58Field("parent", parent),
  };

  const challenge = new LoginConsentProvisioningChallenge({
    challenge_id: challengeId,
    name,
    system_id,
    parent,
    created_at: createdAt,
    salt: input.salt || null,
    context: null,
  });

  // Build deeplink URI (base64url-encoded challenge buffer)
  const challengeBuffer = challenge.toBuffer();
  const encoded = challengeBuffer.toString("base64url");
  const deeplinkUri = `verus://provision/${LOGIN_CONSENT_PROVISIONING_CHALLENGE_VDXF_KEY.vdxfid}?${encodeURIComponent(encoded)}`;

  const result = {
    challenge_id: challengeId,
    name: challenge.name,
    system_id: challenge.system_id,
    parent: challenge.parent,
    vdxfkey: challenge.vdxfkey,
    created_at: challenge.created_at,
    flags: challenge.flags ? challenge.flags.toString() : "0",
    challenge_hex: challengeBuffer.toString("hex"),
    deeplink_uri: deeplinkUri,
    challenge_json: challenge.toJson(),
    diagnostics,
  };

  console.log(JSON.stringify(result));
}

try {
  main();
} catch (err) {
  console.error(JSON.stringify({ error: err.message }));
  process.exit(1);
}
