import json
import uuid


def register_identity(request, svc):
    svc.logger.info(
        "api.register.start payload=%s",
        svc._log_json(
            svc._redact_fields(
                request.model_dump(),
                redacted_keys={"webhook_secret"},
            )
        ),
    )

    daemon_name = svc._resolve_daemon_by_native_coin(request.native_coin)
    if daemon_name is None:
        svc.logger.warning(
            "api.register.daemon_unresolved native_coin=%s name=%s parent=%s",
            request.native_coin,
            request.name,
            request.parent,
        )
        raise svc.HTTPException(
            status_code=503,
            detail={
                "status": "degraded",
                "native_coin": request.native_coin,
                "error": "No enabled daemon configured for requested native coin.",
            },
        )

    svc.logger.info(
        "api.register.daemon_resolved native_coin=%s daemon=%s",
        request.native_coin,
        daemon_name,
    )

    try:
        full_identity_name = svc._build_identity_fqn(request.name, request.parent)
    except ValueError as exc:
        raise svc.HTTPException(status_code=400, detail=str(exc))

    try:
        rpc_connection = svc._get_rpc_connection(daemon_name)
        existing_identity = rpc_connection.get_identity(full_identity_name)
        if isinstance(existing_identity, dict) and bool(existing_identity):
            svc.logger.warning(
                "api.register.identity_exists name=%s parent=%s full_identity_name=%s",
                request.name,
                request.parent,
                full_identity_name,
            )
            raise svc.HTTPException(status_code=409, detail=f"Identity already exists: {full_identity_name}")
    except svc.HTTPException:
        raise
    except Exception as exc:
        if not svc._classify_getidentity_not_found(exc):
            svc.logger.exception(
                "api.register.identity_precheck_error name=%s parent=%s full_identity_name=%s",
                request.name,
                request.parent,
                full_identity_name,
            )
            raise svc.HTTPException(status_code=503, detail="Identity node unreachable or degraded")

        # For non-existent IDs, get_identity usually raises; proceed with registration flow.
        svc.logger.info(
            "api.register.identity_precheck_not_found name=%s parent=%s full_identity_name=%s error=%s",
            request.name,
            request.parent,
            full_identity_name,
            exc,
        )

    source_of_funds = svc.os.getenv("SOURCE_OF_FUNDS", "").strip()
    if not source_of_funds:
        svc.logger.error("api.register.source_of_funds_missing daemon=%s", daemon_name)
        raise svc.HTTPException(status_code=503, detail="SOURCE_OF_FUNDS is not configured")

    svc.logger.debug(
        "api.register.source_of_funds_resolved daemon=%s source_of_funds=%s",
        daemon_name,
        svc._mask_value(source_of_funds),
    )

    allowed_parents = svc._allowed_parent_namespaces()
    parent_normalized = svc._normalize_parent_namespace(request.parent)
    svc.logger.debug(
        "api.register.parent_validation requested_parent=%s normalized_parent=%s allowed_parents=%s",
        request.parent,
        parent_normalized,
        svc._log_json(sorted(allowed_parents)),
    )

    if allowed_parents and parent_normalized not in allowed_parents:
        svc.logger.warning(
            "api.register.parent_denied requested_parent=%s allowed_parents=%s",
            request.parent,
            svc._log_json(sorted(allowed_parents)),
        )
        raise svc.HTTPException(
            status_code=403,
            detail={
                "error": "Requested parent namespace is not permitted.",
                "requested_parent": request.parent,
                "allowed_parents": sorted(allowed_parents),
            },
        )

    try:
        svc.logger.debug(
            "api.register.rpc_name_commitment.request daemon=%s request=%s",
            daemon_name,
            svc._log_json(
                {
                    "name": request.name,
                    "control_address": source_of_funds,
                    "referral_id": request.referral_id or "",
                    "parent": request.parent,
                    "source_of_funds": svc._mask_value(source_of_funds),
                }
            ),
        )
        rnc_response = rpc_connection.register_name_commitment(
            request.name,
            source_of_funds,
            request.referral_id or "",
            request.parent,
            source_of_funds,
        )
        svc.logger.info(
            "api.register.rpc_name_commitment.success daemon=%s txid=%s",
            daemon_name,
            rnc_response.get("txid") if isinstance(rnc_response, dict) else None,
        )
        svc.logger.debug(
            "api.register.rpc_name_commitment.response daemon=%s response=%s",
            daemon_name,
            svc._log_json(rnc_response),
        )
    except Exception as e:
        svc.logger.exception(
            "api.register.rpc_name_commitment.error daemon=%s name=%s parent=%s",
            daemon_name,
            request.name,
            request.parent,
        )
        raise svc.HTTPException(status_code=503, detail=f"RPC error during name commitment: {e}")

    request_id = str(uuid.uuid4())
    svc.logger.debug(
        "api.register.db_insert.prepared request_id=%s values=%s",
        request_id,
        svc._log_json(
            {
                "id": request_id,
                "requested_name": request.name,
                "parent_namespace": request.parent,
                "native_coin": request.native_coin,
                "daemon_name": daemon_name,
                "primary_raddress": request.primary_raddress,
                "referral_id": request.referral_id,
                "control_address": source_of_funds,
                "source_of_funds": svc._mask_value(source_of_funds),
                "status": "pending_rnc_confirm",
                "rnc_txid": rnc_response.get("txid") if isinstance(rnc_response, dict) else None,
                "has_webhook_url": bool(request.webhook_url),
                "has_webhook_secret": bool(request.webhook_secret),
            }
        ),
    )

    conn = svc._get_db_connection()
    conn.execute(
        """
        INSERT INTO registrations (
            id,
            requested_name,
            parent_namespace,
            native_coin,
            daemon_name,
            primary_raddress,
            referral_id,
            control_address,
            source_of_funds,
            status,
            rnc_txid,
            rnc_payload_json,
            webhook_url,
            webhook_secret
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_id,
            request.name,
            request.parent,
            request.native_coin,
            daemon_name,
            request.primary_raddress,
            request.referral_id,
            source_of_funds,
            source_of_funds,
            "pending_rnc_confirm",
            rnc_response.get("txid"),
            json.dumps(rnc_response),
            request.webhook_url,
            request.webhook_secret,
        ),
    )
    conn.commit()
    conn.close()

    svc.logger.info(
        "api.register.success request_id=%s status=%s daemon=%s native_coin=%s txid_rnc=%s",
        request_id,
        "pending_rnc_confirm",
        daemon_name,
        request.native_coin,
        rnc_response.get("txid") if isinstance(rnc_response, dict) else None,
    )

    return {
        "request_id": request_id,
        "status": "pending_rnc_confirm",
        "daemon": daemon_name,
        "native_coin": request.native_coin,
        "txid_rnc": rnc_response.get("txid"),
    }
