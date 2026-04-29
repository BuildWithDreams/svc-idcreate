# Provisioning Update: Verus Mobile Deeplink + Browser SSS Integration

This document describes two integrated flows:

1. **Verus Mobile deeplinks** — making `svc-idcreate` emit Verus Mobile-compatible provisioning deeplinks for users who have Verus Mobile
2. **Browser SSS integration** — using a browser-generated Shamir Secret Sharing key as the VerusID's `primaryaddress`, enabling a fully in-browser identity for games/apps

**Architecture overview:**

```
Browser (SSS)                          Verus Mobile                    svc-idcreate (Server)
    │                                       │                                  │
    │─── generate r-address ────────────────>│                                  │
    │                                       │                                  │
    │    POST /api/provisioning/challenge/browser                               │
    │─── name, browser_r_address ──────────────────────────────────────────────>│
    │                                       │                                  │─── register_name_commitment
    │                                       │                                  │    (browser_r_address as primary)
    │                                       │                                  │
    │                                       │    deeplink approve ─────────────>│
    │                                       │                                       │
    │                                       │    (Verus Mobile signs with its    │
    │                                       │     existing ID — approves the      │
    │                                       │     browser's address as primary)   │
    │                                       │                                  │─── register_identity
    │                                       │                                  │    (browser_r_address = primaryaddress)
    │<─────────────────── identity fqn ─────<─────────────────────────────────────│
```

## Two Modes

| Mode | When to use | Deeplink needed? |
|------|-------------|-----------------|
| **Browser SSS** | User is in-browser, no Verus Mobile needed | No — server registers directly |
| **Verus Mobile** | User wants Verus Mobile to back/revoke the ID, or already has Verus Mobile identities | Yes — Verus Mobile approves via deeplink |

Both modes can be combined: the same identity can have a browser-controlled primary address AND Verus Mobile identities as revocation/recovery delegates.

---

## VDXF Key Constants (for reference)

```javascript
// From verus-typescript-primitives
LOGIN_CONSENT_REQUEST_VDXF_KEY.vdxfid        // "i3dQmgjq8L8XFGQUrs9Gpo8zvPWqs1KMtV"
LOGIN_CONSENT_ID_PROVISIONING_WEBHOOK_VDXF_KEY.vdxfid  // "iMiXw4BuL4iESPqz6fvJ4rHbDg1SvVKLnc"
ID_ADDRESS_VDXF_KEY.vdxfid                  // "i3a3M9n7uVtRYv1vhjmyb4DxY825AVAwic"
ID_SYSTEMID_VDXF_KEY.vdxfid                 // "iMZTNkNBgBXNHkMLipQw9wQb56pxBSEp3k"
ID_PARENT_VDXF_KEY.vdxfid                   // "i6aJSTKfNiDZ4rPxj1pPh4Y8xDmh1GqYm9"
ID_FULLYQUALIFIEDNAME_VDXF_KEY.vdxfid       // "iCQ5gYekWs5DaXiBN7YfoDfNWT3VtpUwVq"
LOGIN_CONSENT_PROVISIONING_CHALLENGE_VDXF_KEY.vdxfid  // "iLvLAJ2YycueCYMDPJA8DwrenULPJkJgKE"
LOGIN_CONSENT_PROVISIONING_RESULT_STATE_COMPLETE.vdxfid // for success response
```

---

## Flow 1: Browser SSS — Server-Registered Identity (No Verus Mobile)

In this flow, the browser generates an r-address via SSS. The server registers the VerusID on-chain with that address as `primaryaddress`. No deeplink needed.

### Browser side

```javascript
// Browser: generate r-address via SSS
const { shards, combinedKey } = await generateSSS(seedPhrase, threshold, numShards);
// The combinedKey contains the private key; the shards are distributed to guardians

// For signing transactions, the browser reconstructs the private key from shards
const privateKey = await combineSSS(shards);

// Get the r-address from the private key
const rAddress = deriveRAddress(privateKey); // e.g., "RExampleAddress123"

const payload = {
  name: "gamertag",
  parent: "@",              // or "i84T3MWcb6zNWcwgNZoU3TXtrUn9EqM84A4" for namespaced
  browser_r_address: rAddress,
  revocation_delegate: "iVerusMobileIdentityFromQrCode",  // scanned from Verus Mobile
  recovery_delegate: "iVerusMobileIdentityFromQrCode",    // same or different
};

const result = await fetch("/api/provisioning/challenge/browser", {
  method: "POST",
  headers: { "X-API-Key": apiKey, "Content-Type": "application/json" },
  body: JSON.stringify(payload),
}).then(r => r.json());

// result.deeplink_uri — only present if vm_delegation_required is true
// result.identity_fqn — identity fully qualified name, set when registration is complete
// result.status — "pending" | "complete" | "failed"
// result.request_id — for polling

if (result.vm_delegation_required) {
  // Show QR code with deeplink for Verus Mobile approval
  showDeeplinkQR(result.deeplink_uri);
}
```

### Server endpoint: POST /api/provisioning/challenge/browser

This new endpoint handles the full server-side registration:

```python
class ProvisioningChallengeBrowserRequest(BaseModel):
    name: str = Field(..., description="Identity name without parent.")
    parent: str = Field(..., description="Parent namespace i-address or '@' for system.")
    browser_r_address: str = Field(..., description="R-address controlled by browser via SSS.")
    revocation_delegate: Optional[str] = Field(
        default=None,
        description="i-address of Verus Mobile identity for revocation authority."
    )
    recovery_delegate: Optional[str] = Field(
        default=None,
        description="i-address of Verus Mobile identity for recovery authority."
    )
    system_id: Optional[str] = Field(default=None, description="System i-address.")
    webhook_url: Optional[str] = Field(
        default=None,
        description="Callback URL when registration completes."
    )


class ProvisioningChallengeBrowserResponse(BaseModel):
    challenge_id: str
    name: str
    parent: str
    browser_r_address: str
    identity_fqn: Optional[str] = None   # filled in once registered
    vm_delegation_required: bool         # true if revocation/recovery delegates set
    deeplink_uri: Optional[str] = None   # Verus Mobile approval deeplink (if required)
    vm_approval_timeout: int             # seconds until vm approval expires
    status: str                          # "pending" | "complete" | "failed"
    request_id: Optional[str] = None
    error_message: Optional[str] = None


@router.post(
    "/challenge/browser",
    response_model=ProvisioningChallengeBrowserResponse,
    status_code=status.HTTP_200_OK,
    summary="Create a browser-SSS-based provisioning challenge",
)
def create_browser_provisioning_challenge(
    request: ProvisioningChallengeBrowserRequest,
    _api_key: str = Security(_require_api_key),
):
    """
    For browser-initiated provisioning with SSS-generated r-address.

    The server registers the identity on-chain with the browser's r-address
    as primaryaddress. If revocation/recovery delegates are provided (i-addresses
    from Verus Mobile), Verus Mobile must approve via deeplink before
    registration finalizes.

    If no delegates are provided, registration proceeds immediately and
    the identity is usable right away.
    """
    engine = get_engine()
    engine.clear_expired_challenges()

    system_i = request.system_id or engine.default_system_id

    # Step 1: Register name commitment on-chain with browser's r-address
    # This locks in the name and sets primaryaddress = browser_r_address
    source_of_funds = os.getenv("SOURCE_OF_FUNDS", "").strip()
    if not source_of_funds:
        raise HTTPException(status_code=503, detail="SOURCE_OF_FUNDS not configured")

    try:
        rpc_conn = _get_rpc_connection("verusd_vrsc")
        rnc_response = rpc_conn.register_name_commitment(
            request.name,
            request.browser_r_address,
            "",  # salt - server can generate or use browser-provided
            request.parent,
            source_of_funds,
        )
    except Exception as e:
        logger.exception("name commitment failed")
        raise HTTPException(status_code=500, detail=f"Name commitment failed: {e}")

    challenge_id = str(uuid.uuid4())
    created_at = int(time.time())

    if request.revocation_delegate or request.recovery_delegate:
        # Step 2a: Verus Mobile approval required
        # Build LoginConsentRequest (revocation/recovery go into register_identity, not provisioning_info)
        vm_result = engine.create_login_consent_request(
            name=request.name,
            parent=request.parent,
            primary_raddress=request.browser_r_address,
            webhook_url=os.getenv("INTERNAL_WEBHOOK_URL", ""),
            system_id=system_i,
            challenge_id=challenge_id,
            created_at=created_at,
        )

        # Persist pending registration
        _persist_pending_registration(
            challenge_id=challenge_id,
            name=request.name,
            parent=request.parent,
            browser_r_address=request.browser_r_address,
            revocation_delegate=request.revocation_delegate,
            recovery_delegate=request.recovery_delegate,
            system_id=system_i,
            rnc_txid=rnc_response.get("txid"),
            request_id=None,
            status="pending_vm_approval",
        )

        return ProvisioningChallengeBrowserResponse(
            challenge_id=challenge_id,
            name=request.name,
            parent=request.parent,
            browser_r_address=request.browser_r_address,
            identity_fqn=None,
            vm_delegation_required=True,
            deeplink_uri=vm_result["deeplink_uri"],
            vm_approval_timeout=vm_result["expires_at"] - created_at,
            status="pending",
            request_id=None,
            error_message=None,
        )
    else:
        # Step 2b: No delegates - register immediately
        try:
            identity_result = _register_identity(
                rpc_conn=rpc_conn,
                name=request.name,
                parent=request.parent,
                primary_address=request.browser_r_address,
                revocation_address=request.revocation_delegate,
                recovery_address=request.recovery_delegate,
                system_id=system_i,
                commitment_txid=rnc_response.get("txid"),
            )
            identity_fqn = f"{request.name}@{request.parent}"
            status = "complete"
            error_message = None
        except Exception as e:
            logger.exception("identity registration failed")
            identity_fqn = None
            status = "failed"
            error_message = str(e)

        # Persist for status queries
        _persist_pending_registration(
            challenge_id=challenge_id,
            name=request.name,
            parent=request.parent,
            browser_r_address=request.browser_r_address,
            revocation_delegate=request.revocation_delegate,
            recovery_delegate=request.recovery_delegate,
            system_id=system_i,
            rnc_txid=rnc_response.get("txid"),
            request_id=identity_result.get("request_id") if status == "complete" else None,
            status=status,
        )

        return ProvisioningChallengeBrowserResponse(
            challenge_id=challenge_id,
            name=request.name,
            parent=request.parent,
            browser_r_address=request.browser_r_address,
            identity_fqn=identity_fqn,
            vm_delegation_required=False,
            deeplink_uri=None,
            vm_approval_timeout=0,
            status=status,
            request_id=identity_result.get("request_id") if status == "complete" else None,
            error_message=error_message,
        )
```

### Server helper: _register_identity

```python
def _register_identity(rpc_conn, name, parent, primary_address,
                        revocation_address, recovery_address,
                        system_id, commitment_txid):
    """
    Call register_identity RPC with the committed name.
    primary_address is the browser's SSS-controlled r-address.
    revocation_address and recovery_address are Verus Mobile i-addresses (or None).
    """
    result = rpc_conn.register_identity(
        name=name,
        parent=parent,
        primary_address=primary_address,
        revocation_address=revocation_address,
        recovery_address=recovery_address,
        system_id=system_id,
        commitment_txid=commitment_txid,
    )
    return result


def _persist_pending_registration(challenge_id, name, parent,
                                    browser_r_address, revocation_delegate,
                                    recovery_delegate, system_id,
                                    rnc_txid, request_id, status):
    conn = _get_db_connection()
    conn.execute("""
        INSERT OR REPLACE INTO browser_provisioning (
            challenge_id, name, parent, browser_r_address,
            revocation_delegate, recovery_delegate, system_id,
            rnc_txid, request_id, status, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        challenge_id, name, parent, browser_r_address,
        revocation_delegate, recovery_delegate, system_id,
        rnc_txid, request_id, status, int(time.time()),
    ))
    conn.commit()
    conn.close()
```

### Verus Mobile deeplink approval handler

When Verus Mobile approves via deeplink, it POSTs to your webhook. The existing `/api/provisioning/request` handler can be extended to detect browser-SSS registrations and finalize them:

```python
@router.post("/request")
def submit_provisioning_request(
    payload: ProvisioningRequestPayload,
    _api_key: str = Security(_require_api_key),
):
    """
    Existing handler — extend to finalize browser-SSS registrations
    when Verus Mobile approval is received.
    """
    # ... existing verification code ...

    # Check if this is a browser-SSS pending registration
    stored = engine.get_challenge_status(challenge_id)
    if stored and stored.get("status") == "pending_vm_approval":
        # This is a browser-SSS registration — finalize on-chain
        try:
            identity_result = _register_identity(
                rpc_conn=rpc_conn,
                name=stored["name"],
                parent=stored["parent"],
                primary_address=stored["browser_r_address"],
                revocation_address=stored.get("revocation_delegate"),
                recovery_address=stored.get("recovery_delegate"),
                system_id=stored["system_id"],
                commitment_txid=stored["rnc_txid"],
            )

            engine.update_challenge_status(
                challenge_id, "complete",
                identity_address=identity_result.get("identity_address"),
                fully_qualified_name=f"{stored['name']}@{stored['parent']}",
            )

            # Optionally notify via webhook_url if provided
            # _notify_webhook(stored.get("webhook_url"), identity_result)

        except Exception as e:
            engine.update_challenge_status(
                challenge_id, "failed",
                error_message=str(e),
            )
            raise HTTPException(status_code=500, detail=str(e))

    # ... rest of existing logic ...
```

### Browser polling endpoint

```python
@router.get("/status/{challenge_id}", response_model=ProvisioningStatusResponse)
def get_browser_provisioning_status(challenge_id: str):
    """
    Browser polls this to check if Verus Mobile has approved
    and the identity is registered.
    """
    stored = _get_pending_registration(challenge_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Challenge not found")

    return ProvisioningStatusResponse(
        challenge_id=challenge_id,
        status=stored["status"],
        name=stored["name"],
        parent=stored["parent"],
        identity_address=stored.get("identity_address"),
        fully_qualified_name=stored.get("fully_qualified_name"),
        error_message=stored.get("error_message"),
        error_key=stored.get("error_key"),
        request_id=stored.get("request_id"),
    )
```

### Browser polling flow

```javascript
// In browser, after creating a browser-SSS challenge with vm_delegation_required=true
async function waitForRegistration(challengeId) {
  while (true) {
    const status = await fetch(`/api/provisioning/status/${challengeId}`)
      .then(r => r.json());

    if (status.status === "complete") {
      console.log("Identity registered:", status.identity_fqn);
      // Store identity_fqn in browser localStorage for later use
      return status;
    }

    if (status.status === "failed") {
      throw new Error("Registration failed: " + status.error_message);
    }

    // Still pending — wait and retry
    await new Promise(r => setTimeout(r, 5000));
  }
}
```

---

## Flow 2: Verus Mobile Deeplink — Full LoginConsentRequest

For users who come through Verus Mobile directly (no browser SSS), the existing deeplink approach builds a signed `LoginConsentRequest` with `provisioning_info`.

This is the same flow described in Changes 1-6 below — Verus Mobile signs a `LoginConsentRequest` approving the server to register an identity on its behalf.

**Key difference from Flow 1:** In Flow 1, the browser already controls the primary key via SSS. In Flow 2, Verus Mobile is the signing authority and the server generates the primary address on the user's behalf (or the user imports a key later).

---

## VDXF Key Constants (for reference)

```javascript
// From verus-typescript-primitives
LOGIN_CONSENT_REQUEST_VDXF_KEY.vdxfid        // "i3dQmgjq8L8XFGQUrs9Gpo8zvPWqs1KMtV"
LOGIN_CONSENT_ID_PROVISIONING_WEBHOOK_VDXF_KEY.vdxfid  // "iMiXw4BuL4iESPqz6fvJ4rHbDg1SvVKLnc"
ID_ADDRESS_VDXF_KEY.vdxfid                  // "i3a3M9n7uVtRYv1vhjmyb4DxY825AVAwic"
ID_SYSTEMID_VDXF_KEY.vdxfid                 // "iMZTNkNBgBXNHkMLipQw9wQb56pxBSEp3k"
ID_PARENT_VDXF_KEY.vdxfid                   // "i6aJSTKfNiDZ4rPxj1pPh4Y8xDmh1GqYm9"
ID_FULLYQUALIFIEDNAME_VDXF_KEY.vdxfid       // "iCQ5gYekWs5DaXiBN7YfoDfNWT3VtpUwVq"
```

---

## Change 1: New Node.js Script

**File:** `provisioning/src/build-login-consent-request.js`

Builds a signed `LoginConsentRequest` with `provisioning_info` for Verus Mobile, including optional revocation and recovery delegates (for browser-SSS flow).

```javascript
/**
 * build-login-consent-request.js
 *
 * Builds a signed LoginConsentRequest with provisioning_info for Verus Mobile.
 *
 * stdin JSON: {
 *   system_id,             // provider's i-address (signer)
 *   signing_id,            // signing identity i-address (usually same as system_id)
 *   signing_wif,           // WIF private key for signing
 *   challenge_id,           // unique challenge ID (i-address format, generated on Python side)
 *   name,                   // identity name being provisioned
 *   parent,                 // parent namespace i-address
 *   primary_raddress,       // r-address that will be the identity's primaryaddress (browser SSS key)
 *   webhook_url,            // provisioning webhook URL (LOGIN_CONSENT_ID_PROVISIONING_WEBHOOK_VDXF_KEY data)
 *   revocation_delegate,    // optional: i-address of Verus Mobile identity for revocation
 *   recovery_delegate,      // optional: i-address of Verus Mobile identity for recovery
 *   created_at,             // unix timestamp
 * }
 *
 * Returns JSON: {
 *   deeplink_uri,
 *   request_hex,
 *   request_json,
 *   challenge_id,
 *   expires_at,
 * }
 */
"use strict";

const {
  LoginConsentRequest,
  LOGIN_CONSENT_REQUEST_VDXF_KEY,
  LOGIN_CONSENT_ID_PROVISIONING_WEBHOOK_VDXF_KEY,
  ID_ADDRESS_VDXF_KEY,
  ID_SYSTEMID_VDXF_KEY,
  ID_PARENT_VDXF_KEY,
  ID_FULLYQUALIFIEDNAME_VDXF_KEY,
  SIGNATURE_VDXF_KEY,
} = require("verus-typescript-primitives");

function main() {
  const input = JSON.parse(process.argv[2] || process.stdin.read());

  const {
    system_id,
    signing_id,
    signing_wif,
    challenge_id,
    name,
    parent,
    primary_raddress,
    webhook_url,
    created_at,
  } = input;

  if (!system_id || !signing_id || !signing_wif || !challenge_id ||
      !name || !parent || !primary_raddress || !webhook_url) {
    throw new Error("Missing required fields");
  }

  // Build provisioning_info array
  // This tells Verus Mobile WHERE to post the signed request, and what identity to create.
  // revocation/recovery authorities are NOT in provisioning_info — they go directly
  // into register_identity on the server side after VM approval.
  const provisioning_info = [];

  // LOGIN_CONSENT_ID_PROVISIONING_WEBHOOK_VDXF_KEY - required (where VM posts the signed request)
  provisioning_info.push({
    vdxfkey: LOGIN_CONSENT_ID_PROVISIONING_WEBHOOK_VDXF_KEY.vdxfid,
    version: "01",
    encoding: "utf-8",
    data: webhook_url,
  });

  // ID_SYSTEMID_VDXF_KEY - required
  provisioning_info.push({
    vdxfkey: ID_SYSTEMID_VDXF_KEY.vdxfid,
    version: "01",
    encoding: "utf-8",
    data: system_id,
  });

  // ID_PARENT_VDXF_KEY - required
  provisioning_info.push({
    vdxfkey: ID_PARENT_VDXF_KEY.vdxfid,
    version: "01",
    encoding: "utf-8",
    data: parent,
  });

  // Build subject array
  const subject = [
    {
      vdxfkey: ID_SYSTEMID_VDXF_KEY.vdxfid,
      version: "01",
      encoding: "utf-8",
      data: system_id,
    },
    {
      vdxfkey: ID_PARENT_VDXF_KEY.vdxfid,
      version: "01",
      encoding: "utf-8",
      data: parent,
    },
  ];

  // Build redirect_uris - empty array signals pure provisioning
  const redirect_uris = [];

  // Build the challenge
  const challenge = {
    challenge_id,
    redirect_uris,
    provisioning_info,
    subject,
    created_at: created_at || Math.floor(Date.now() / 1000),
    salt: challenge_id,
    context: null,
  };

  // Build the LoginConsentRequest
  const req = new LoginConsentRequest({
    system_id,
    signing_id,
    challenge,
  });

  // Sign the request using the signing WIF
  const signedReq = signLoginConsentRequest(req, signing_wif, signing_id);

  // Encode to buffer
  const reqBuffer = signedReq.toBuffer();
  const encoded = reqBuffer.toString("base64url");

  // Build deeplink URI
  // Format: i<provider_iaddress>://x-callback-url/<vdxfid>/?<vdxfid>=<encoded>
  const deeplinkUri = `${system_id}://x-callback-url/${LOGIN_CONSENT_REQUEST_VDXF_KEY.vdxfid}/?${LOGIN_CONSENT_REQUEST_VDXF_KEY.vdxfid}=${encoded}`;

  const result = {
    deeplink_uri: deeplinkUri,
    request_hex: reqBuffer.toString("hex"),
    request_json: signedReq.toJson(),
    challenge_id,
    signature: signedReq.signature ? signedReq.signature.signature : null,
  };

  console.log(JSON.stringify(result));
}

/**
 * Sign a LoginConsentRequest with ECDSA using the provider's signing WIF
 */
function signLoginConsentRequest(request, signingWif, signingId) {
  const { VerusIdInterface } = require("verus-typescript-primitives");

  // Get the challenge hash to sign
  const dataToSign = request.getChallengeHash();

  // Import the private key from WIF
  const { PrivateKey } = require("@dashevo/dashcore-lib");
  const privateKey = PrivateKey.fromWIF(signingWif);

  // Sign the challenge hash
  const signature = privateKey.sign(dataToSign);
  const signatureDer = signature.toDER();

  // Encode signature as base64
  const signatureBase64 = Buffer.from(signatureDer).toString("base64");

  // Attach signature to request
  request.signature = {
    vdxfkey: SIGNATURE_VDXF_KEY.vdxfid,
    signature: signatureBase64,
    serializekey: true,
  };

  return request;
}

try {
  main();
} catch (err) {
  console.error(JSON.stringify({ error: err.message, stack: err.stack }));
  process.exit(1);
}
```

Note: Verus does **not** use separate VDXF keys for revocation/recovery in `provisioning_info`. Instead, these are passed directly in the `register_identity` call's JSON body (see `json_identity` in `_register_identity` below). The LoginConsentRequest `provisioning_info` carries the **webhook URL** for the provisioning request, not the revocation/recovery authorities — those are embedded by the server when it calls `register_identity` after Verus Mobile approval.

The `revocation_delegate` and `recovery_delegate` (i-addresses) are stored in the pending registration record and used when the server calls `register_identity`:

```python
def _register_identity(rpc_conn, name, parent, primary_address,
                        revocation_address, recovery_address,
                        system_id, commitment_txid):
    """
    Call register_identity RPC with the committed name.

    primary_address: browser's SSS-controlled r-address  (becomes primaryaddresses[0])
    revocation_address: Verus Mobile i-address (or None)
    recovery_address: Verus Mobile i-address (or None)
    """
    json_identity = {
        "name": name,
        "primaryaddresses": [primary_address],
        "privateaddresses": os.getenv("Z_ADDRESS", "").strip() or "",
        "minimumsignature": 1,
        # revocationauthority and recoveryauthority accept i-addresses
        **({} if not revocation_address else {"revocationauthority": revocation_address}),
        **({} if not recovery_address else {"recoveryauthority": recovery_address}),
    }
    result = rpc_conn.register_identity(commitment_txid, json_identity)
    return result
```

The `revocationauthority` and `recoveryauthority` fields accept either a friendly name (`alice@`) or an i-address (`i5w5MuNik5NtLcYm...`). Both are identity references, not plain r-addresses.

---

## Change 2: Add Adapter Method

**File:** `provisioning/adapters.py`

Add a new method to `HttpProvisioningAdapter` to call the new Node.js script:

```python
class HttpProvisioningAdapter:
    # ... existing code ...

    def build_login_consent_request(self, input_data: dict) -> dict:
        """
        Build a signed LoginConsentRequest with provisioning_info
        for Verus Mobile deeplinks.
        """
        return self._post_json("/v1/provisioning/login-consent-request/build", input_data)
```

And in `provisioning/engine.py`, add a corresponding method that calls the adapter.

---

## Change 3: New Engine Method

**File:** `provisioning/engine.py`

Add `create_login_consent_request()` to `ProvisioningEngine`:

```python
def create_login_consent_request(
    self,
    name: str,
    parent: str,
    primary_raddress: str,
    webhook_url: str,
    system_id: Optional[str] = None,
    challenge_id: Optional[str] = None,
    created_at: Optional[int] = None,
    salt: Optional[str] = None,
) -> dict:
    """
    Create a signed LoginConsentRequest with provisioning_info
    for Verus Mobile provisioning deeplinks.

    Note: revocation_delegate and recovery_delegate are NOT passed here.
    They are stored in the pending registration record and applied
    during register_identity on the server side after VM approval.

    Returns a dict with deeplink_uri, request_hex, request_json,
    challenge_id, signature, expires_at, created_at.
    """
    if not self.signing_identity:
        raise ProvisioningError("SIGNING_IDENTITY must be set")

    system_i = system_id or self.default_system_id

    # Use provided challenge_id or generate one (base58check via adapter)
    challenge_id = challenge_id or self._generate_challenge_id()
    created_at = created_at or int(time.time())
    salt = salt or challenge_id

    node_input = {
        "system_id": system_i,
        "signing_id": system_i,
        "signing_wif": self.signing_wif,
        "challenge_id": challenge_id,
        "name": name,
        "parent": parent,
        "primary_raddress": primary_raddress,
        "webhook_url": webhook_url,
        "created_at": created_at,
        "salt": salt,
    }

    try:
        result = self.adapter.build_login_consent_request(node_input)
    except Exception as e:
        raise ProvisioningError(f"Failed to build login consent request: {e}") from e

    expires_at = created_at + CHALLENGE_MAX_AGE_SECONDS

    # Persist for later verification
    record = {
        "challenge_id": challenge_id,
        "name": name,
        "parent": parent,
        "system_id": system_i,
        "primary_raddress": primary_raddress,
        "request_hex": result["request_hex"],
        "request_json": result["request_json"],
        "deeplink_uri": result["deeplink_uri"],
        "expires_at": expires_at,
        "created_at": created_at,
        "status": "pending",
        "request_id": None,
        "identity_address": None,
        "fully_qualified_name": None,
        "error_message": None,
        "error_key": None,
    }
    self._challenge_store[challenge_id] = record
    self._save_challenge_record(record)
    logger.info("login consent request created challenge_id=%s name=%s", challenge_id, name)

    return {
        "challenge_id": challenge_id,
        "name": name,
        "parent": parent,
        "system_id": system_i,
        "primary_raddress": primary_raddress,
        "request_hex": result["request_hex"],
        "request_json": result["request_json"],
        "deeplink_uri": result["deeplink_uri"],
        "expires_at": expires_at,
        "created_at": created_at,
        "status": "pending",
    }
```

**Also update** `_db()` initialization to include the new `request_hex`, `request_json`, and `primary_raddress` columns:

```python
def _init_challenge_store_db(self) -> None:
    conn = self._db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS provisioning_challenges (
            challenge_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            parent TEXT NOT NULL,
            system_id TEXT NOT NULL,
            primary_raddress TEXT NOT NULL,
            challenge_hex TEXT,           -- for provisioning challenge (backward compat)
            challenge_json TEXT,           -- for provisioning challenge (backward compat)
            request_hex TEXT,              -- for login consent request (new)
            request_json TEXT,            -- for login consent request (new)
            deeplink_uri TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            status TEXT NOT NULL,
            request_id TEXT,
            identity_address TEXT,
            fully_qualified_name TEXT,
            error_message TEXT,
            error_key TEXT,
            updated_at INTEGER NOT NULL
        )
    """)
    # ... rest unchanged
```

---

## Change 4: New Router Endpoint

**File:** `provisioning/router.py`

Add a new request model and endpoint:

```python
class ProvisioningChallengeVmRequest(BaseModel):
    """Request body for creating a Verus Mobile provisioning deeplink."""
    name: str = Field(..., description="Identity name without parent namespace.", examples=["alice"])
    parent: str = Field(..., description="Parent namespace (i-address or friendly name).", examples=["i84T3MWcb6zNWcwgNZoU3TXtrUn9EqM84A4"])
    primary_raddress: str = Field(..., description="Primary R-address for identity control.", examples=["RExampleAddress123"])
    webhook_url: str = Field(..., description="Provisioning webhook URL to POST the signed request to.")
    system_id: Optional[str] = Field(default=None, description="System i-address (defaults to DEFAULT_SYSTEM_ID).")
    redirect_uri: Optional[str] = Field(default=None, description="Optional login redirect URI for hybrid flow.")
    preassigned_iaddress: Optional[str] = Field(default=None, description="Optional pre-assigned i-address.")
    prequalified_name: Optional[str] = Field(default=None, description="Optional pre-qualified name.")


class ProvisioningChallengeVmResponse(BaseModel):
    """Response for Verus Mobile provisioning deeplink creation."""
    challenge_id: str
    name: str
    parent: str
    system_id: str
    primary_raddress: str
    deeplink_uri: str
    request_hex: str
    request_json: dict
    expires_at: int
    created_at: int
```

Add the new endpoint:

```python
@router.post(
    "/challenge/vm",
    response_model=ProvisioningChallengeVmResponse,
    status_code=status.HTTP_200_OK,
    summary="Create a Verus Mobile provisioning challenge",
)
def create_provisioning_challenge_vm(
    request: ProvisioningChallengeVmRequest,
    _api_key: str = Security(_require_api_key),
):
    """
    Create a signed LoginConsentRequest with provisioning_info,
    suitable for Verus Mobile deeplinks.

    The returned deeplink_uri can be encoded into a QR code or
    deep-linked to Verus Mobile.
    """
    engine = get_engine()
    engine.clear_expired_challenges()

    try:
        result = engine.create_login_consent_request(
            name=request.name,
            parent=request.parent,
            primary_raddress=request.primary_raddress,
            webhook_url=request.webhook_url,
            system_id=request.system_id,
            redirect_uri=request.redirect_uri,
            preassigned_iaddress=request.preassigned_iaddress,
            prequalified_name=request.prequalified_name,
        )
    except Exception as e:
        logger.exception("VM challenge create failed name=%s", request.name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create challenge: {e}",
        )

    logger.info("VM challenge created challenge_id=%s", result["challenge_id"])

    return ProvisioningChallengeVmResponse(
        challenge_id=result["challenge_id"],
        name=result["name"],
        parent=result["parent"],
        system_id=result["system_id"],
        primary_raddress=result["primary_raddress"],
        deeplink_uri=result["deeplink_uri"],
        request_hex=result["request_hex"],
        request_json=result["request_json"],
        expires_at=result["expires_at"],
        created_at=result["created_at"],
    )
```

---

## Change 5: Node.js Service Route for build-login-consent-request

**File:** `provisioning/src/service.js` (create if doesn't exist)

The HTTP provisioning adapter calls `/v1/provisioning/login-consent-request/build`. You'll need a small Node.js HTTP server (or add to an existing one) that routes to the build script. Example using Express:

```javascript
// service.js
const express = require("express");
const { spawn } = require("child_process");
const path = require("path");

const app = express();
app.use(express.json());

function runScript(scriptName, input) {
  return new Promise((resolve, reject) => {
    const scriptPath = path.join(__dirname, scriptName);
    const child = spawn("node", [scriptPath, JSON.stringify(input)], {
      cwd: __dirname,
    });

    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (data) => { stdout += data.toString(); });
    child.stderr.on("data", (data) => { stderr += data.toString(); });

    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`${scriptName} failed: ${stderr}`));
      } else {
        try {
          resolve(JSON.parse(stdout));
        } catch (e) {
          reject(new Error(`Invalid JSON from ${scriptName}: ${stdout}`));
        }
      }
    });
  });
}

app.post("/v1/provisioning/login-consent-request/build", async (req, res) => {
  try {
    const result = await runScript("build-login-consent-request.js", req.body);
    res.json(result);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post("/v1/provisioning/request/verify", async (req, res) => {
  try {
    const result = await runScript("verify-request.js", req.body);
    res.json(result);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post("/v1/provisioning/response/build", async (req, res) => {
  try {
    const result = await runScript("build-response.js", req.body);
    res.json(result);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post("/v1/base58check/encode", async (req, res) => {
  try {
    const result = await runScript("base58check.js", req.body);
    res.json(result);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

const PORT = process.env.PROVISIONING_NODE_PORT || 3001;
app.listen(PORT, () => {
  console.log(`Provisioning Node service listening on port ${PORT}`);
});
```

---

## Change 6: Package.json for Node.js Scripts

**File:** `provisioning/package.json` (update)

Ensure the `@dashevo/dashcore-lib` dependency is present for WIF signing:

```json
{
  "name": "provisioning-node",
  "version": "1.0.0",
  "dependencies": {
    "verus-typescript-primitives": "*",
    "@dashevo/dashcore-lib": "^1.0.0",
    "express": "^4.18.2"
  }
}
```

---

## Full API Flow

### Create provisioning deeplink for Verus Mobile

```
POST /api/provisioning/challenge/vm
X-API-Key: <your-key>

{
  "name": "alice",
  "parent": "i84T3MWcb6zNWcwgNZoU3TXtrUn9EqM84A4",
  "primary_raddress": "RExampleAddress123",
  "webhook_url": "https://your-service.com/api/provisioning/request",
  "system_id": "i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV"
}
```

**Response:**

```json
{
  "challenge_id": "iRwegWuHfnJXPYt6PzN2itJxBTZTQctmja",
  "name": "alice",
  "parent": "i84T3MWcb6zNWcwgNZoU3TXtrUn9EqM84A4",
  "system_id": "i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV",
  "primary_raddress": "RExampleAddress123",
  "deeplink_uri": "i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV://x-callback-url/i3dQmgjq8L8XFGQUrs9Gpo8zvPWqs1KMtV/?i3dQmgjq8L8XFGQUrs9Gpo8zvPWqs1KMtV=Aa4uvSgsLA...",
  "request_hex": "01ae2ebd...",
  "request_json": { ... },
  "expires_at": 1775247855,
  "created_at": 1775247255
}
```

### Wallet sends signed provisioning request to your webhook

```
POST https://your-service.com/api/provisioning/request
Content-Type: application/json

{
  "signing_address": "RExampleAddress123",
  "challenge": {
    "challenge_id": "iRwegWuHfnJXPYt6PzN2itJxBTZTQctmja",
    "name": "alice",
    "system_id": "i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV",
    "parent": "i84T3MWcb6zNWcwgNZoU3TXtrUn9EqM84A4",
    "created_at": 1775247255
  }
}
```

### Your service returns provisioning response

```
POST https://wallet-webhook/callback  (from provisioning_info.webhook_url)
Content-Type: application/json

{
  "system_id": "i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV",
  "signing_id": "i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV",
  "decision": {
    "decision_id": "iRwegWuHfnJXPYt6PzN2itJxBTZTQctmja",
    "created_at": 1775248000,
    "request": { ... },
    "result": {
      "state": "iLvLAJ2YycueCYMDPJA8DwrenULPJkJgKE",  // COMPLETE vdxfid
      "identity_address": "iNewlyCreatedIdentity...",
      "fully_qualified_name": "alice@i84T3MWcb6zNWcwgNZoU3TXtrUn9EqM84A4@",
      "system_id": "i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV",
      "parent": "i84T3MWcb6zNWcwgNZoU3TXtrUn9EqM84A4",
      "provisioning_txids": [
        { "data": "txid_here", "vdxfkey": "iLvLAJ2YycueCYMDPJA8DwrenULPJkJgKE" }
      ]
    }
  }
}
```

---

## Summary of Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `provisioning/src/build-login-consent-request.js` | **CREATE** | New Node.js script to build signed LoginConsentRequest with provisioning_info |
| `provisioning/src/service.js` | **CREATE** | Express server routing to build scripts |
| `provisioning/package.json` | **UPDATE** | Add `@dashevo/dashcore-lib`, `express` dependencies |
| `provisioning/adapters.py` | **UPDATE** | Add `build_login_consent_request()` method to HttpProvisioningAdapter |
| `provisioning/engine.py` | **UPDATE** | Add `create_login_consent_request()` method; update DB schema with `primary_raddress`, `request_hex`, `request_json` columns |
| `provisioning/router.py` | **UPDATE** | Add Flow 1 browser endpoint (`POST /challenge/browser`) + Flow 2 VM endpoint (`POST /challenge/vm`) |
| `provisioning/__init__.py` | **UPDATE** | Export new engine method if needed |
| `id_create_service.py` | **UPDATE** | Add `browser_provisioning` table init; add `_register_identity()` helper; wire `/api/provisioning/status/{id}` |

## Key Corrections Applied

- **Revocation/recovery delegates are NOT in provisioning_info or the LoginConsentRequest**. They go directly into the `register_identity` call's `json_identity` body as `revocationauthority` and `recoveryauthority` (accepting i-addresses).
- **primary_raddress** goes into `register_identity` as `primaryaddresses: [primary_raddress]` and is stored in the pending registration DB.
- The Verus Mobile LoginConsentRequest's `provisioning_info` carries only: `LOGIN_CONSENT_ID_PROVISIONING_WEBHOOK_VDXF_KEY` (webhook URL), `ID_SYSTEMID_VDXF_KEY`, `ID_PARENT_VDXF_KEY`.
