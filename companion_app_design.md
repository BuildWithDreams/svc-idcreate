# VerusID Companion App — Technical Design

## Overview

A lightweight, cross-platform native app that:
- Holds a signing key (SSS-derived) in OS-level secure storage
- Signs VerusID operations (2-of-3 multisig: companion key + VM key + guardian)
- Communicates with browser-based games via local HTTP
- Can trigger Verus Mobile for approval via deep links / QR codes
- Runs on: Windows, macOS, Linux, Android, iOS

**Single codebase.** No separate Electron + mobile branches.

---

## Technology Choice: Tauri 2.x

| Criteria | Electron | Tauri 2.x | Flutter | React Native |
|----------|----------|------------|---------|--------------|
| Binary size | ~150MB | ~5MB | ~20MB | ~30MB |
| Web tech reuse | High | High | None | Medium |
| Native crypto | Via Node | Via Rust | Built-in | Plugin |
| Desktop | Yes | Yes | Yes | Yes (Expo) |
| Mobile | Electron-packaged (bad) | Yes | Yes | Yes |
| Local HTTP server | Node http | Rust axum/h3 | None | None |
| Secure storage | Keychain (manual) | Keychain (automatic) | Keychain | Keychain |
| Install friction | Medium | Low | Low | Low |

**Why Tauri:**
- Rust backend = tiny binary, fast, native crypto, local HTTP server
- WebView frontend = reuse your existing web UI skills
- Capacitor can wrap Tauri on mobile for app store distribution
- OS-level secure storage without extra work
- Local HTTP server means no CORS problems

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER DEVICE                               │
│                                                                 │
│   ┌──────────────────────────────────────────────────────────┐  │
│   │  Browser (Game App — any origin)                         │  │
│   │                                                           │  │
│   │  GET /games/play/123                                     │  │
│   │  POST /api/sign  ──────────────────────────────────────►│  │
│   │       { challenge: "...", ops: [...] }                   │  │
│   │                                                           │  │
│   │  ◄────────────────────────────────────────── { signed }  │  │
│   │       { challenge_id, signatures: [comp_sig, vm_sig] } │  │
│   └──────────────────────────────────────────────────────────┘  │
│                              ▲                                  │
│                              │ localhost HTTP                   │
│                              │ (no CORS, no network)           │
│   ┌──────────────────────────┴──────────────────────────┐    │
│   │  Tauri Companion App (always-on, system tray)         │    │
│   │                                                           │  │
│   │  Rust Backend:                                           │    │
│   │    ├─ Local HTTP server (h3, ~127.0.0.1:3847)          │    │
│   │    ├─ OS Keychain (secure storage)                    │    │
│   │    ├─ Signing engine (SSS key operations)               │    │
│   │    └─ VM Communicator (QR code generator)              │    │
│   │                                                           │    │
│   │  WebView Frontend (Svelte/SvelteKit):                   │    │
│   │    ├─ First-run setup wizard                            │    │
│   │    ├─ Active sessions dashboard                         │    │
│   │    ├─ Signing approval dialogs                          │    │
│   │    └─ Settings / key management                         │    │
│   └──────────────────────────────────────────────────────────┘    │
│                              ▲                                  │
│                              │ QR scan / deeplink               │
│   ┌──────────────────────────┴──────────────────────────┐    │
│   │  Verus Mobile (installed on same device or other)   │    │
│   │                                                           │    │
│   │  - Receives signing challenge via QR                  │    │
│   │  - User approves / denies                            │    │
│   │  - Returns signed response to Companion App          │    │
│   └──────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Directory Structure

```
companion-app/
├── src/                          # Web frontend (Svelte/SvelteKit)
│   ├── lib/
│   │   ├── components/           # UI components
│   │   │   ├── SetupWizard.svelte
│   │   │   ├── SigningPrompt.svelte
│   │   │   ├── SessionList.svelte
│   │   │   └── KeyManager.svelte
│   │   ├── stores/              # Svelte stores
│   │   │   ├── sessions.ts
│   │   │   ├── keys.ts
│   │   │   └── settings.ts
│   │   ├── routes/              # SvelteKit pages
│   │   │   ├── +layout.svelte
│   │   │   ├── +page.svelte      # Dashboard
│   │   │   ├── setup/            # First-run wizard
│   │   │   ├── approve/[id]/      # Signing approval page
│   │   │   └── settings/          # Key management
│   │   └── utils/
│   │       ├── api.ts             # Tauri command wrappers
│   │       ├── qr.ts              # QR code generation
│   │       └── sss.ts             # SSS utilities
│   ├── app.html
│   └── hooks.server.ts
│
├── src-tauri/                    # Rust backend
│   ├── src/
│   │   ├── main.rs               # Entry point
│   │   ├── lib.rs
│   │   ├── http_server.rs         # Local HTTP server (h3)
│   │   ├── signing.rs             # Signing operations
│   │   ├── keyring.rs             # OS keychain operations
│   │   ├── crypto.rs              # SSS, key derivation
│   │   ├── vm_communicator.rs    # QR code, deeplinks
│   │   ├── session.rs            # Session management
│   │   ├── protocol.rs            # Request/response types
│   │   └── error.rs              # Error types
│   ├── Cargo.toml
│   ├── tauri.conf.json
│   ├── icons/
│   └── capabilities/             # Tauri permissions
│
├── capacitor.config.ts            # Capacitor config (mobile)
├── src-mobile/                    # Mobile-specific (optional overrides)
│   └── ...
│
├── SPEC.md                       # Full specification
├── README.md
└── package.json
```

---

## Protocol: Browser ↔ Companion App

The browser communicates with the companion app via **localhost HTTP** on a random available port. Port is written to a well-known file so the browser can find it.

### Port Discovery

```rust
// On companion app startup:
// 1. Find available port
// 2. Write to ~/.verus-companion/port (or AppData/Local/verus-companion/port)
let port_file = dirs::data_local_dir()
    .join("verus-companion")
    .join("port");

// File contains: { "port": 38471, "version": 1 }

// Browser reads this file, then POSTs to http://127.0.0.1:<port>
```

### Signing Request (Browser → Companion)

```
POST /v1/sign/login
Content-Type: application/json

{
  "request_id": "uuid-v4",
  "service_id": "my-game-service",
  "service_name": "CryptoQuest",
  "service_icon": "https://my-game.com/icon.png",
  "challenge": {
    "id": "challenge_uuid",
    "type": "login",
    "system_id": "i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV",
    "redirect_uri": "https://my-game.com/auth/callback",
    "permissions": ["identity:read"],
    "created_at": 1712345678,
    "expires_at": 1712346278
  },
  "callback_url": "http://127.0.0.1:38471/v1/callback/{request_id}",
  "idempotency_key": "uuid-v4"
}
```

### Signing Response (Companion → Browser)

```
HTTP/1.1 200 OK
Content-Type: application/json

{
  "request_id": "uuid-v4",
  "status": "approved",
  "signatures": [
    {
      "signer": "companion",
      "key_id": "key_uuid",
      "algorithm": "ecdsa secp256k1",
      "signature": "base64_der_signature"
    }
  ],
  "signed_challenge": {
    "id": "challenge_uuid",
    "system_id": "i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV",
    "signatures": ["..."]
  }
}
```

### Pending / Needs VM (Companion → Browser)

If companion key alone isn't enough (2-of-3):

```
HTTP/1.1 202 Accepted
Content-Type: application/json

{
  "request_id": "uuid-v4",
  "status": "pending",
  "companion_signature": {
    "signer": "companion",
    "signature": "base64..."
  },
  "required_signers": ["verus_mobile"],
  "qr_code_data_uri": "data:image/png;base64,...",
  "poll_url": "http://127.0.0.1:38471/v1/status/uuid-v4",
  "expires_at": 1712346278
}
```

### Full Protocol Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/sign/login` | Login challenge signing |
| `POST` | `/v1/sign/provision` | Identity provisioning signing |
| `POST` | `/v1/sign/content` | Contentmultimap update signing |
| `POST` | `/v1/sign/revoke` | Key revocation signing |
| `GET` | `/v1/status/{id}` | Poll signing status |
| `POST` | `/v1/callback/{id}` | VM callback after QR scan |
| `GET` | `/v1/sessions` | List active signing sessions |
| `DELETE` | `/v1/session/{id}` | Cancel a pending signing request |
| `GET` | `/v1/identity` | Get this device's public identity |
| `POST` | `/v1/identity/register` | Register identity on-chain |
| `GET` | `/v1/health` | Health check |

---

## Signing Request Types

### 1. Login Challenge (`login`)

For logging into browser-based apps:

```typescript
interface LoginSigningRequest {
  type: 'login';
  service_id: string;        // Unique ID of the calling service
  service_name: string;       // Display name
  service_icon?: string;     // URL to service icon
  challenge: {
    id: string;
    system_id: string;        // VerusID system (i-address)
    redirect_uri: string;     // Where to send the final response
    permissions: string[];   // What data is requested
    created_at: number;
    expires_at: number;
  };
}
```

### 2. Content Update (`content`)

For updating game cartridge contentmultimap:

```typescript
interface ContentSigningRequest {
  type: 'content';
  service_id: string;
  cartridge_id: string;
  cartridge_name: string;
  operation: 'save' | 'load' | 'lock' | 'unlock' | 'transfer';
  content: {
    ipfs_cid: string;        // IPFS content identifier
    size_bytes: number;
    hash: string;            // Content hash
    encrypted: boolean;
  };
  metadata?: Record<string, unknown>;
}
```

### 3. Identity Provisioning (`provision`)

For registering a new VerusID:

```typescript
interface ProvisionSigningRequest {
  type: 'provision';
  name: string;              // Identity name (without parent)
  parent: string;            // Parent namespace or system ID
  recovery_delegate?: string; // Optional recovery i-address
  revocation_delegate?: string; // Optional revocation i-address
  guardian_threshold: 2 | 3;  // Multisig threshold
}
```

### 4. Key Revocation (`revoke`)

For revoking a compromised key:

```typescript
interface RevokeSigningRequest {
  type: 'revoke';
  key_to_revoke: string;     // i-address of key being revoked
  reason: 'compromised' | 'lost' | 'rotated' | 'user_request';
  new_key?: string;          // Replacement key i-address (if rotating)
}
```

---

## Companion Key Generation (SSS)

On first run, the app generates a 2-of-3 SSS split:

```
SSS Configuration:
  - Algorithm: Shamir Secret Sharing (2, 3)
  - Prime: secp256k1 curve order
  - Security: equivalent to secp256k1 private key

Three shares generated:
  ┌─────────────────────────────────────────────────────┐
  │  Share 1: Companion App (this device)               │
  │          Stored: OS Keychain                         │
  │          Accessed: biometric / PIN on this device    │
  └─────────────────────────────────────────────────────┘
                          ▲
                          │
  ┌───────────────────────┴───────────────────────────┐
  │  Share 2: Guardian (parent's VM or paper)         │
  │          Option A: QR code → import to Verus Mobile │
  │          Option B: Paper backup (encrypted PDF)     │
  │          Option C: Guardian app on another device   │
  └─────────────────────────────────────────────────────┘
                          ▲
                          │
  ┌───────────────────────┴───────────────────────────┐
  │  Share 3: Recovery seed (user stores)             │
  │          Encrypted PDF printed and stored          │
  │          Password-protected                        │
  └───────────────────────────────────────────────────┘
```

### Key Derivation Path

The signing key is derived from the SSS master key:

```
Master Key (SSS reconstructed)
  │
  └─► BIP-32 derivation path: m/3030'/0'/0'
        │
        ├─► Signing key (m/3030'/0'/0'/0)
        │     Used for: all signing operations
        │
        └─► Identity key (m/3030'/0'/0'/1)
              Used for: VerusID identity registration
```

---

## 2-of-3 Multisig Flow

### Flow: Browser Login with 2-of-3

```
Browser (Game)                          Companion App                    Verus Mobile
     │                                        │                                │
     │  POST /v1/sign/login ────────────────►│                                │
     │                                        │                                │
     │                                        │  1. User approves in app       │
     │                                        │  2. Sign with companion key   │
     │                                        │  3. Companion signature: 1/2 │
     │                                        │                                │
     │  ◄── 202 Pending ─────────────────────│  4. Generate QR code         │
     │       { qr_code: "...", poll_url: "..." }   │                                │
     │                                        │                                │
     │                                        │  5. QR contains challenge      │
     │                                        │     + companion signature      │
     │                                        │     + callback_url             │
     │                                        │                                │
     │  (browser polls poll_url)              │                                │
     │                                        │                                │
     │                                        │◄─── User scans QR ───────────│
     │                                        │    User taps "Approve"        │
     │                                        │                                │
     │                                        │  6. VM signs (2/2 complete)   │
     │                                        │  7. POST callback_url         │
     │                                        │      { vm_signature: "..." }  │
     │                                        │                                │
     │  ◄─── GET /v1/status/{id} ────────────│  (or browser polls)           │
     │       { status: "complete",            │                                │
     │         signatures: [comp_sig, vm_sig] }                                │
     │                                        │                                │
     │  POST /callback-url ──────────────────────────────────────────────────►│
     │       { signatures: [comp_sig, vm_sig] }                              │
     │                                        │                                │
     │  ◄─── { identity: "iAddress", name: "gamer123" }                     │
```

### Companion Key + Guardian (No VM Available)

If VM is offline or user can't access it:

```
Browser                          Companion App                    Guardian (offline backup)
     │                                   │                                   │
     │  POST /v1/sign/login ───────────►│                                   │
     │                                   │                                   │
     │                                   │  Timeout after 24h               │
     │                                   │  OR user manually escalates      │
     │                                   │                                   │
     │  ◄── 202 Pending ─────────────────│  Guardian notified               │
     │       { escalation_notice: "..." } │                                   │
     │                                   │  QR code for guardian backup     │
     │                                   │  (guardian uses paper backup)    │
     │                                   │                                   │
     │                                   │  Guardian scans + approves       │
     │                                   │                                   │
     │  ◄─── Complete (2/2: companion + guardian) ───────────────────────────│
```

### Guardian Timeout Configuration

```typescript
interface GuardianSettings {
  timeout_hours: number;       // Default: 24. Range: 1-168
  auto_approve_if_read_only: boolean; // Skip guardian if operation is read-only
  require_reapproval_after_hours: number; // Re-prompt VM after X hours
  guardian_contacts: Array<{
    type: 'verus_mobile' | 'paper' | 'email';
    identifier: string;
    priority: number;           // Lower = tried first
  }>;
}
```

---

## Verus Mobile Communication

The companion app never makes network requests to Verus Mobile directly. All communication is via QR code / deeplink.

### QR Code Payload

The QR contains a compressed JSON payload (deflate + base64):

```
data:text/html;base64,<compressed_payload>

<!-- Decompresses to: -->
{
  "v": 1,
  "type": "sign_request",
  "request_id": "uuid",
  "challenge": {
    "system_id": "i5w5...",
    "ops": [...],
    "callback_url": "http://127.0.0.1:38471/v1/callback/uuid",
    "expires_at": 1712346278
  },
  "companion_sig": {
    "signer": "iCompanionAddr",
    "signature": "base64...",
    "signed_at": 1712345678
  }
}
```

### Deep Link Format

For desktop with Verus Desktop Wallet:

```
verus://sign?data=<base64url(compressed_payload)>
```

### Callback Flow

After Verus Mobile signs:

```
1. VM opens callback_url (via local HTTP if on same device)
   POST http://127.0.0.1:38471/v1/callback/{request_id}
   { request_id, signature: "...", signer: "iVMAddress" }

2. Companion receives callback
   - Stores VM signature
   - Marks signing request as complete

3. Browser polling receives result
   GET http://127.0.0.1:38471/v1/status/{request_id}
   { status: "complete", signatures: [companion_sig, vm_sig] }
```

---

## OS Keychain Storage

Tauri with `tauri-plugin-store` handles secure storage automatically:

```rust
// src-tauri/src/keyring.rs

use tauri::Manager;
use tauri_plugin_store::StoreExt;

pub async fn store_key(app: &tauri::AppHandle, share: &[u8]) -> Result<(), Error> {
    // Uses OS Keychain: Keychain on macOS/iOS,
    // DPAPI on Windows, libsecret on Linux
    let store = app.store("keys.dat").unwrap();
    store.set("companion_share", share.to_vec());
    store.save().unwrap();
    Ok(())
}

pub async fn get_key(app: &tauri::AppHandle) -> Result<Vec<u8>, Error> {
    let store = app.store("keys.dat").unwrap();
    let share = store.get("companion_share").unwrap();
    Ok(share)
}
```

The OS keychain is:
- **macOS:** Keychain (biometric or password)
- **iOS:** Keychain (biometric via Face ID/Touch ID)
- **Windows:** Credential Manager / DPAPI
- **Linux:** libsecret / GNOME Keyring
- **Android:** Keystore (hardware-backed if available)

---

## Tauri Configuration

```json
// src-tauri/tauri.conf.json
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "Verus Companion",
  "identifier": "com.verus.comp anion",
  "version": "1.0.0",
  "build": {
    "devtools": true
  },
  "app": {
    "windows": [
      {
        "title": "Verus Companion",
        "width": 400,
        "height": 600,
        "resizable": true,
        "decorations": true,
        "alwaysOnTop": false,
        "visible": true
      }
    ],
    "trayIcon": {
      "iconPath": "icons/tray.png",
      "iconAsTemplate": true
    },
    "security": {
      "dangerousDisableAssetCspModification": false
    }
  },
  "bundle": {
    "active": true,
    "targets": "all",
    "icon": [
      "icons/32x32.png",
      "icons/128x128.png",
      "icons/128x128@2x.png",
      "icons/icon.icns",
      "icons/icon.ico"
    ]
  },
  "plugins": {
    "store": {}
  }
}
```

```toml
# src-tauri/Cargo.toml (key dependencies)

[dependencies]
tauri = { version = "2", features = ["tray-icon", "devtools"] }
tauri-plugin-store = "2"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tokio = { version = "1", features = ["full"] }
h3 = "0.0.0-alpha.4"       # QUIC HTTP server
h3-server = "0.1.0-alpha.3"
bytes = "1"
uuid = { version = "1", features = ["v4", "serde"] }
base64 = "0.22"
flate2 = "1"
chrono = { version = "0.4", features = ["serde"] }
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
aes-gcm = "0.10"
rand = "0.8"
sha2 = "0.10"
thiserror = "1"

[target.'cfg(mobile)'.dependencies]
tauri-plugin-deep-link = "2"
```

---

## Mobile: Capacitor Wrapper

For iOS/Android app store distribution, wrap the Tauri app with Capacitor:

```typescript
// capacitor.config.ts
import { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.verus.companion',
  appName: 'Verus Companion',
  webDir: 'dist',
  server: {
    // Tauri serves the web app itself
    // Capacitor just provides native hooks
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 2000,
      backgroundColor: '#1a1a2e',
    },
    DeepLink: {
      customProtocol: 'veruscompanion',
    },
    BiometricAuth: {
      biometricTitle: 'Unlock Verus Companion',
    },
  },
};

export default config;
```

---

## Browser SDK: `@verus/companion-sdk`

A tiny JS library that browser-based games include:

```typescript
// package: @verus/companion-sdk

import { VerusCompanion } from '@verus/companion-sdk';

// Auto-discover companion app
const companion = await VerusCompanion.discover();

// Connect to companion
await companion.connect();

// Check if user has companion installed
if (!companion.isAvailable()) {
  // Show install prompt
  showInstallPrompt();
  return;
}

// Check if user has a Verus identity
const identity = await companion.getIdentity();
if (!identity) {
  // Guide user through setup
  await companion.setup();
}

// Initiate login
const result = await companion.signLogin({
  service_id: 'my-game',
  service_name: 'CryptoQuest',
  challenge: {
    system_id: 'i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV',
    redirect_uri: 'https://my-game.com/auth/callback',
    permissions: ['identity:read', 'content:write'],
    expires_in: 300, // seconds
  },
});

// result contains signatures from companion + VM
await fetch('https://my-game.com/auth/callback', {
  method: 'POST',
  body: JSON.stringify(result),
});
```

### Auto-Discovery

```typescript
// discover() reads the port file
async function discover(): Promise<VerusCompanion> {
  // Cross-platform port file location
  const portFile = await getPortFile(); // ~userData/verus-companion/port

  if (!portFile.exists) {
    throw new Error('Companion not installed');
  }

  const { port } = portFile.read();
  const baseUrl = `http://127.0.0.1:${port}`;

  // Verify companion is running
  const health = await fetch(`${baseUrl}/v1/health`);
  if (!health.ok) {
    throw new Error('Companion not responding');
  }

  return new VerusCompanion(baseUrl);
}
```

---

## Error Handling

```typescript
// All errors have structured codes

enum CompanionErrorCode {
  NOT_INSTALLED = 'NOT_INSTALLED',
  NOT_RUNNING = 'NOT_RUNNING',
  KEY_NOT_SETUP = 'KEY_NOT_SETUP',
  USER_DENIED = 'USER_DENIED',
  TIMEOUT = 'TIMEOUT',
  INVALID_REQUEST = 'INVALID_REQUEST',
  SIGNATURE_FAILED = 'SIGNATURE_FAILED',
  VM_UNAVAILABLE = 'VM_UNAVAILABLE',
  GUARDIAN_TIMEOUT = 'GUARDIAN_TIMEOUT',
  NETWORK_ERROR = 'NETWORK_ERROR',
}
```

---

## Security Properties

| Property | Guarantee |
|----------|-----------|
| **Key isolation** | Signing key never leaves companion app process |
| **OS-level storage** | Key stored in Keychain/Keystore, not files |
| **No network from companion** | App never makes outbound connections; browser handles all network |
| **2-of-3 threshold** | Compromised companion alone cannot sign |
| **Biometric gate** | Key requires biometric/PIN to use |
| **Session-scoped** | Browser sessions are temporary; revoke anytime |
| **Guardian timeout** | Guardian can always recover after timeout |
| **No persistent browser state** | No key stored in browser; session-only |
| **VM as always-available signer** | VM is the panic button for all operations |

---

## Threat Model: What Each Protection Guards Against

| Threat | Protection |
|--------|-----------|
| Malware reading localStorage | Key never in localStorage |
| XSS on game site | No key access; only signing interface |
| Malicious browser extension | Cannot read localhost; key in OS keychain |
| Compromised companion app | Need 2-of-3; VM or guardian required |
| Lost phone | Guardian recovers; VM revokes stolen key |
| Social engineering (kid) | Parent VM must approve high-value actions |
| Man-in-the-middle | All traffic is localhost; blockchain is signed |
| Replay attack | Nonces + expiry on all challenges |
| Phishing clone site | Site must present valid challenge; browser SDK validates |

---

## Build Targets

```
┌─────────────────────────────────────────────────────┐
│  Tauri + Cargo                                      │
│                                                     │
│  ┌───────────┐  ┌───────────┐  ┌───────────────┐  │
│  │ Windows   │  │ macOS     │  │ Linux         │  │
│  │ (.exe)    │  │ (.dmg)    │  │ (.AppImage)   │  │
│  │ Windows   │  │ macOS     │  │ Ubuntu        │  │
│  │ Store     │  │ App Store │  │ Fedora        │  │
│  └───────────┘  └───────────┘  └───────────────┘  │
│                                                     │
│  ┌───────────┐  ┌───────────┐  ┌───────────────┐  │
│  │ iOS       │  │ Android   │  │ Web           │  │
│  │ App Store │  │ Play Store│  │ PWA (progressive)│  │
│  │ Capacitor │  │ Capacitor │  │ Same binary   │  │
│  └───────────┘  └───────────┘  └───────────────┘  │
└─────────────────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase 1: Core (Companion App Only)
- Tauri app setup with local HTTP server
- SSS key generation and OS keychain storage
- Signing approval UI
- Login challenge signing
- QR code generation for VM

### Phase 2: Multisig
- 2-of-3 signing flow
- VM callback handling
- Guardian timeout escalation
- Session management

### Phase 3: Content Operations
- Contentmultimap signing requests
- Cartridge lock/unlock
- Transfer operations

### Phase 4: Distribution
- macOS App Store
- Windows Store
- Linux packages
- iOS / Android (Capacitor)
