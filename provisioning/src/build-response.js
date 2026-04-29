/**
 * build-response.js
 * Builds a ProvisioningResponse from result details.
 * stdin JSON: {
 *   system_id, signing_id, signing_address,
 *   decision_id, created_at,
 *   result_state,  -- 'complete', 'failed', 'pendingrequiredinfo', 'pendingapproval'
 *   result_error_key,   -- optional
 *   result_error_desc,  -- optional
 *   result_identity_address,  -- optional (on success)
 *   result_system_id,        -- optional
 *   result_fully_qualified_name,  -- optional
 *   result_parent,           -- optional
 *   result_info_uri,          -- optional
 *   result_txids,            -- array of txid strings, optional
 *   request_json              -- the original ProvisioningRequest JSON
 * }
 */
"use strict";

const {
  LoginConsentProvisioningResponse,
  LoginConsentProvisioningDecision,
  LoginConsentProvisioningRequest,
  LoginConsentProvisioningResult,
  LOGIN_CONSENT_PROVISIONING_RESULT_STATE_COMPLETE,
  LOGIN_CONSENT_PROVISIONING_RESULT_STATE_FAILED,
  LOGIN_CONSENT_PROVISIONING_RESULT_STATE_PENDINGREQUIREDINFO,
  LOGIN_CONSENT_PROVISIONING_RESULT_STATE_PENDINGAPPROVAL,
  fromBase58Check,
} = require("verus-typescript-primitives");

const STATE_MAP = {
  complete: LOGIN_CONSENT_PROVISIONING_RESULT_STATE_COMPLETE,
  failed: LOGIN_CONSENT_PROVISIONING_RESULT_STATE_FAILED,
  pendingrequiredinfo: LOGIN_CONSENT_PROVISIONING_RESULT_STATE_PENDINGREQUIREDINFO,
  pendingapproval: LOGIN_CONSENT_PROVISIONING_RESULT_STATE_PENDINGAPPROVAL,
};

function isValidAddress(value) {
  if (typeof value !== "string" || !value) return false;
  try {
    fromBase58Check(value);
    return true;
  } catch {
    return false;
  }
}

function canonicalizeRequest(inputRequest, signingAddress, systemId, decisionId, createdAt) {
  const request = inputRequest && typeof inputRequest === "object" ? { ...inputRequest } : {};
  const challenge = request.challenge && typeof request.challenge === "object" ? { ...request.challenge } : {};

  challenge.challenge_id = challenge.challenge_id || decisionId;
  challenge.created_at = challenge.created_at || createdAt;
  challenge.salt = challenge.salt || decisionId;
  challenge.name = challenge.name || "";
  challenge.system_id = challenge.system_id || systemId;
  challenge.parent = challenge.parent || systemId;
  challenge.context = challenge.context ?? null;

  return {
    signing_address: request.signing_address || signingAddress,
    signing_id: null,
    system_id: null,
    challenge,
  };
}

function main() {
  const input = JSON.parse(process.argv[2] || process.stdin.read());

  const {
    system_id,
    signing_id,
    signing_address,
    decision_id,
    created_at,
    result_state,
    result_error_key,
    result_error_desc,
    result_identity_address,
    result_system_id,
    result_fully_qualified_name,
    result_parent,
    result_info_uri,
    result_txids,
    request_json,
  } = input;

  if (!system_id || !signing_id || !decision_id || !result_state) {
    throw new Error("Missing required fields");
  }

  // Build ProvisioningResult
  const stateKey = STATE_MAP[result_state];
  if (!stateKey) {
    throw new Error(`Unknown result_state: ${result_state}`);
  }

  const resultInterface = {
    state: stateKey.vdxfid,
  };

  if (isValidAddress(result_error_key)) resultInterface.error_key = result_error_key;
  if (result_error_desc) resultInterface.error_desc = result_error_desc;
  if (result_identity_address) resultInterface.identity_address = result_identity_address;
  if (result_system_id) resultInterface.system_id = result_system_id;
  if (result_fully_qualified_name) resultInterface.fully_qualified_name = result_fully_qualified_name;
  if (isValidAddress(result_parent)) resultInterface.parent = result_parent;
  if (result_info_uri) resultInterface.info_uri = result_info_uri;
  if (result_txids && result_txids.length > 0) {
    resultInterface.provisioning_txids = result_txids.map((txid) => ({
      data: txid,
      vdxfkey: stateKey.vdxfid,
    }));
  }

  const provisioningResult = new LoginConsentProvisioningResult(resultInterface);

  const canonicalRequestJson = canonicalizeRequest(
    request_json,
    signing_address,
    system_id,
    decision_id,
    created_at || Math.floor(Date.now() / 1000)
  );
  const provisioningRequest = new LoginConsentProvisioningRequest(canonicalRequestJson);

  // Build the ProvisioningDecision
  const decision = new LoginConsentProvisioningDecision({
    decision_id: decision_id || system_id,  // use system_id as fallback to ensure valid checksum
    created_at: created_at || Math.floor(Date.now() / 1000),
    request: provisioningRequest,
    result: provisioningResult,
    context: null,
    salt: null,
  });

  // Build the ProvisioningResponse
  const response = new LoginConsentProvisioningResponse({
    system_id,
    signing_id,
    decision,
  });

  const responseHex = response.toBuffer().toString("hex");
  const responseJson = response.toJson();

  const result = {
    response_hex: responseHex,
    response_json: responseJson,
    vdxfkey: response.vdxfkey,
    decision_id: decision.decision_id,
  };

  console.log(JSON.stringify(result));
}

try {
  main();
} catch (err) {
  console.error(JSON.stringify({ error: err.message, stack: err.stack }));
  process.exit(1);
}
