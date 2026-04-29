"""
provisioning/engine.py

Core provisioning engine.
Python orchestrates; provisioning serialization/parsing is delegated via adapter.

Architecture:
  Python (orchestration, ECDSA signing via ecdsa module)
    <--JSON stdin/stdout-->
  Node.js (primitives: LoginConsentProvisioningChallenge/Request/Response,
           binary serialization via verus-typescript-primitives)
"""

from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import time
from typing import Optional

from provisioning.adapters import ProvisioningAdapter


# ─── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_SYSTEM_ID = os.getenv("DEFAULT_SYSTEM_ID", "")
SIGNING_WIF = os.getenv("PROVISIONING_SIGNING_WIF", "")
SIGNING_IDENTITY = os.getenv("SIGNING_IDENTITY", "")
CHALLENGE_MAX_AGE_SECONDS = int(os.getenv("PROVISIONING_CHALLENGE_MAX_AGE_SECONDS", "600"))
I_ADDRESS_VERSION = int(os.getenv("I_ADDRESS_VERSION", "102"))
logger = logging.getLogger(__name__)


# ─── Exception ─────────────────────────────────────────────────────────────────

class ProvisioningError(Exception):
    """Raised when any provisioning operation fails."""
    pass


# ─── ProvisioningEngine ─────────────────────────────────────────────────────────

class ProvisioningEngine:
    """
    High-level provisioning engine.

    Endpoints:
      create_challenge(...)    → challenge + deeplink
      verify_and_parse_request(...)  → parsed request or raises ProvisioningError
      build_success_response(...)     → ProvisioningResponse JSON+hex
      build_failure_response(...)     → ProvisioningResponse JSON+hex (failed state)
      get_challenge_status(challenge_id) → state dict or None
    """

    def __init__(
        self,
        signing_identity: Optional[str] = None,
        signing_wif: Optional[str] = None,
        default_system_id: Optional[str] = None,
        adapter: Optional[ProvisioningAdapter] = None,
    ):
        if adapter is None:
            raise ProvisioningError("ProvisioningEngine requires an explicit provisioning adapter")
        self.signing_identity = signing_identity or SIGNING_IDENTITY
        self.signing_wif = signing_wif or SIGNING_WIF
        self.default_system_id = default_system_id or DEFAULT_SYSTEM_ID
        self.adapter = adapter
        self._db_path = os.getenv("REGISTRAR_DB_PATH", "registrar.db")
        self._init_challenge_store_db()

        # In-memory store: challenge_id -> {
        #   challenge_id, name, parent, system_id, primary_raddress,
        #   challenge_hex, challenge_json, deeplink_uri,
        #   expires_at, created_at, status, request_id, ...
        # }
        self._challenge_store: dict[str, dict] = {}

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_challenge_store_db(self) -> None:
        conn = self._db()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS provisioning_challenges (
                challenge_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                parent TEXT NOT NULL,
                system_id TEXT NOT NULL,
                primary_raddress TEXT NOT NULL,
                challenge_hex TEXT NOT NULL,
                challenge_json TEXT NOT NULL,
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
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_provisioning_challenges_expires_at ON provisioning_challenges(expires_at)"
        )
        conn.commit()
        conn.close()
        logger.debug("provisioning challenge table ready db_path=%s", self._db_path)

    def _save_challenge_record(self, record: dict) -> None:
        conn = self._db()
        conn.execute(
            """
            INSERT OR REPLACE INTO provisioning_challenges (
                challenge_id,
                name,
                parent,
                system_id,
                primary_raddress,
                challenge_hex,
                challenge_json,
                deeplink_uri,
                expires_at,
                created_at,
                status,
                request_id,
                identity_address,
                fully_qualified_name,
                error_message,
                error_key,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["challenge_id"],
                record["name"],
                record["parent"],
                record["system_id"],
                record["primary_raddress"],
                record["challenge_hex"],
                json.dumps(record["challenge_json"]),
                record["deeplink_uri"],
                int(record["expires_at"]),
                int(record["created_at"]),
                record["status"],
                record.get("request_id"),
                record.get("identity_address"),
                record.get("fully_qualified_name"),
                record.get("error_message"),
                record.get("error_key"),
                int(time.time()),
            ),
        )
        conn.commit()
        conn.close()
        logger.debug(
            "challenge persisted id=%s status=%s request_id=%s",
            record.get("challenge_id"),
            record.get("status"),
            record.get("request_id"),
        )

    def _row_to_record(self, row: sqlite3.Row) -> dict:
        record = dict(row)
        record["challenge_json"] = json.loads(record["challenge_json"])
        return record

    def _load_challenge_record(self, challenge_id: str) -> Optional[dict]:
        conn = self._db()
        row = conn.execute(
            "SELECT * FROM provisioning_challenges WHERE challenge_id = ?",
            (challenge_id,),
        ).fetchone()
        conn.close()
        if row is None:
            return None
        return self._row_to_record(row)

    def _delete_challenge_record(self, challenge_id: str) -> None:
        conn = self._db()
        conn.execute(
            "DELETE FROM provisioning_challenges WHERE challenge_id = ?",
            (challenge_id,),
        )
        conn.commit()
        conn.close()
        logger.debug("challenge deleted id=%s", challenge_id)

    # ─── Challenge creation ───────────────────────────────────────────────────

    def create_challenge(
        self,
        name: str,
        parent: str,
        primary_raddress: str,
        system_id: Optional[str] = None,
        salt: Optional[str] = None,
    ) -> dict:
        """
        Create a LoginConsentProvisioningChallenge via Node.js.

        Returns a dict with challenge_id, challenge_hex, challenge_json,
        deeplink_uri, expires_at, created_at.
        """
        if not self.signing_identity:
            raise ProvisioningError("SIGNING_IDENTITY must be set")

        system_i = system_id or self.default_system_id

        # Generate IDs (base58check via Node.js)
        challenge_id = self._generate_challenge_id()
        created_at = int(time.time())
        salt = salt or self._generate_challenge_id()

        node_input = {
            "name": name,
            "system_id": system_i,
            "parent": parent,
            "challenge_id": challenge_id,
            "created_at": created_at,
            "salt": salt,
        }

        logger.info(
            "creating challenge name=%s parent=%s system_id=%s challenge_id=%s",
            name,
            self._id_preview(parent),
            self._id_preview(system_i),
            self._id_preview(challenge_id),
        )

        try:
            challenge_data = self.adapter.build_challenge(node_input)
        except Exception as e:
            logger.exception(
                "build challenge adapter failure name=%s parent=%s system_id=%s challenge_id=%s",
                name,
                self._id_preview(parent),
                self._id_preview(system_i),
                self._id_preview(challenge_id),
            )
            raise ProvisioningError(f"Failed to build challenge: {e}") from e

        expires_at = created_at + CHALLENGE_MAX_AGE_SECONDS

        # Persist to in-memory store
        record = {
            "challenge_id": challenge_id,
            "name": name,
            "parent": parent,
            "system_id": system_i,
            "primary_raddress": primary_raddress,
            "challenge_hex": challenge_data["challenge_hex"],
            "challenge_json": challenge_data["challenge_json"],
            "deeplink_uri": challenge_data["deeplink_uri"],
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
        logger.info("challenge created id=%s name=%s parent=%s", challenge_id, name, parent)

        return record

    # ─── Request verification ─────────────────────────────────────────────────

    def verify_and_parse_request(self, request_json: dict) -> dict:
        """
        Validate and parse a wallet-returned ProvisioningRequest.

        Checks:
          1. challenge_id is known
          2. challenge has not expired
          3. name matches what was stored

        Returns the parsed request data dict on success.
        Raises ProvisioningError on any validation failure.
        """
        if not request_json:
            raise ProvisioningError("Empty request payload")

        # Use Node.js to parse and extract the challenge fields
        try:
            parsed = self.adapter.verify_request(request_json)
        except Exception as e:
            raise ProvisioningError(f"Failed to parse request: {e}") from e

        challenge_id = parsed.get("challenge_id")
        if not challenge_id:
            raise ProvisioningError("Request has no challenge_id")

        stored = self.get_challenge_status(challenge_id)
        if stored is None:
            logger.warning("verify failed unknown challenge_id=%s", challenge_id)
            raise ProvisioningError(f"Unknown challenge_id: {challenge_id}")

        # Expiry check
        if time.time() > stored["expires_at"]:
            self._challenge_store.pop(challenge_id, None)
            self._delete_challenge_record(challenge_id)
            logger.warning("verify failed expired challenge_id=%s", challenge_id)
            raise ProvisioningError("Challenge expired")

        # Name must match
        embedded = parsed.get("name") or request_json.get("name")
        if embedded and stored.get("name") and embedded != stored["name"]:
            logger.warning(
                "verify failed name mismatch challenge_id=%s embedded=%s stored=%s",
                challenge_id,
                embedded,
                stored.get("name"),
            )
            raise ProvisioningError(
                f"Challenge name mismatch: requested '{embedded}', "
                f"stored '{stored['name']}'"
            )

        logger.info("request verified challenge_id=%s", challenge_id)

        return {
            "challenge_id": challenge_id,
            "name": stored["name"],
            "parent": stored["parent"],
            "system_id": stored["system_id"],
            "primary_raddress": stored["primary_raddress"],
            "signing_address": parsed.get("signing_address"),
            "challenge_hash_hex": parsed.get("challenge_hash_hex"),
            "request_json": parsed.get("request_json"),
        }

    # ─── Response building ────────────────────────────────────────────────────

    def build_success_response(
        self,
        decision_id: str,
        signing_address: str,
        identity_address: str,
        fully_qualified_name: str,
        system_id: str,
        parent: str,
        txids: list[str],
        request_json: dict,
    ) -> dict:
        """
        Build a LoginConsentProvisioningResponse with result_state = 'complete'.
        """
        node_input = {
            "system_id": system_id,
            "signing_id": system_id,
            "signing_address": signing_address,
            "decision_id": decision_id,
            "created_at": int(time.time()),
            "result_state": "complete",
            "result_identity_address": identity_address,
            "result_fully_qualified_name": fully_qualified_name,
            "result_system_id": system_id,
            "result_parent": parent,
            "result_txids": txids,
            "request_json": request_json,
        }

        try:
            return self.adapter.build_response(node_input)
        except Exception as e:
            logger.exception("success response build failed decision_id=%s", decision_id)
            raise ProvisioningError(f"Failed to build success response: {e}") from e

    def build_failure_response(
        self,
        decision_id: str,
        signing_address: str,
        error_key: str,
        error_desc: str,
        system_id: str,
        request_json: dict,
    ) -> dict:
        """
        Build a LoginConsentProvisioningResponse with result_state = 'failed'.
        """
        node_input = {
            "system_id": system_id,
            "signing_id": system_id,
            "signing_address": signing_address,
            "decision_id": decision_id,
            "created_at": int(time.time()),
            "result_state": "failed",
            "result_error_key": error_key,
            "result_error_desc": error_desc,
            "request_json": request_json,
        }

        try:
            return self.adapter.build_response(node_input)
        except Exception as e:
            logger.exception("failure response build failed decision_id=%s", decision_id)
            raise ProvisioningError(f"Failed to build failure response: {e}") from e

    # ─── Status ──────────────────────────────────────────────────────────────

    def get_challenge_status(self, challenge_id: str) -> Optional[dict]:
        """Return the stored challenge record, or None if not found."""
        cached = self._challenge_store.get(challenge_id)
        if cached is not None:
            return cached

        loaded = self._load_challenge_record(challenge_id)
        if loaded is not None:
            self._challenge_store[challenge_id] = loaded
        return loaded

    def clear_expired_challenges(self) -> int:
        """Remove expired challenges from in-memory store. Returns count removed."""
        now = time.time()
        expired = [k for k, v in self._challenge_store.items() if now > v["expires_at"]]
        for k in expired:
            del self._challenge_store[k]

        conn = self._db()
        cur = conn.execute(
            "DELETE FROM provisioning_challenges WHERE expires_at < ?",
            (int(now),),
        )
        conn.commit()
        db_deleted = cur.rowcount if cur.rowcount is not None else 0
        conn.close()

        if db_deleted:
            logger.info("expired challenges removed count=%s", db_deleted)

        return max(len(expired), db_deleted)

    def update_challenge_status(
        self,
        challenge_id: str,
        status: str,
        request_id: Optional[str] = None,
        identity_address: Optional[str] = None,
        fully_qualified_name: Optional[str] = None,
        error_message: Optional[str] = None,
        error_key: Optional[str] = None,
    ) -> None:
        """Update the stored challenge record (used by worker as it progresses)."""
        if challenge_id not in self._challenge_store:
            loaded = self._load_challenge_record(challenge_id)
            if loaded is None:
                return
            self._challenge_store[challenge_id] = loaded

        if challenge_id not in self._challenge_store:
            return
        record = self._challenge_store[challenge_id]
        record["status"] = status
        if request_id is not None:
            record["request_id"] = request_id
        if identity_address is not None:
            record["identity_address"] = identity_address
        if fully_qualified_name is not None:
            record["fully_qualified_name"] = fully_qualified_name
        if error_message is not None:
            record["error_message"] = error_message
        if error_key is not None:
            record["error_key"] = error_key

        self._save_challenge_record(record)
        logger.info(
            "challenge status updated id=%s status=%s request_id=%s",
            challenge_id,
            status,
            record.get("request_id"),
        )

    # ─── Internals ────────────────────────────────────────────────────────────

    def _generate_challenge_id(self) -> str:
        """Generate a base58check challenge ID via Node.js."""
        try:
            return self.adapter.base58check_encode(
                data_hex=random.randbytes(20).hex(),
                version=I_ADDRESS_VERSION,
            )
        except Exception as e:
            raise ProvisioningError(f"Failed to generate challenge id: {e}") from e

    @staticmethod
    def _id_preview(value: Optional[str]) -> str:
        """Return a short preview for log lines while avoiding full-value dumps."""
        if not value:
            return "<empty>"
        if len(value) <= 12:
            return value
        return f"{value[:6]}...{value[-4:]}"
