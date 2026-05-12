import json
import uuid


def build_currency_request_response(request_id: str, status: str, workflow_type: str, daemon_name: str, native_coin: str):
    return {
        "request_id": request_id,
        "status": status,
        "workflow_type": workflow_type,
        "daemon": daemon_name,
        "native_coin": native_coin,
    }


def enqueue_simple_currency_request(
    *,
    name: str,
    parent: str,
    native_coin: str,
    primary_raddress: str,
    daemon_name: str,
    source_of_funds: str,
    plan,
    create_currency_request_record,
) -> dict:
    request_id = str(uuid.uuid4())
    payload = {
        "name": name,
        "parent": parent,
        "native_coin": native_coin,
        "primary_raddress": primary_raddress,
        "pre_allocation_id": plan.pre_allocation_id,
        "pre_allocation_amount": plan.pre_allocation_amount,
        "id_registration_fees": plan.id_registration_fees,
        "proof_protocol": plan.proof_protocol,
        "define_options": plan.define_options,
        "define_funding_amount": plan.define_funding_amount,
    }
    create_currency_request_record(
        {
            "id": request_id,
            "workflow_type": "simple_token",
            "requested_name": name,
            "parent_namespace": parent,
            "native_coin": native_coin,
            "daemon_name": daemon_name,
            "primary_raddress": primary_raddress,
            "source_of_funds": source_of_funds,
            "status": "pending",
            "payload_json": json.dumps(payload),
            "progress_json": json.dumps({}),
        }
    )

    return build_currency_request_response(request_id, "pending", "simple_token", daemon_name, native_coin)


def enqueue_fractional_currency_request(
    *,
    name: str,
    parent: str,
    native_coin: str,
    primary_raddress: str,
    daemon_name: str,
    source_of_funds: str,
    plan,
    create_currency_request_record,
) -> dict:
    request_id = str(uuid.uuid4())
    payload = {
        "name": name,
        "parent": parent,
        "native_coin": native_coin,
        "primary_raddress": primary_raddress,
        "initial_supply": plan.initial_supply,
        "id_registration_fees": plan.id_registration_fees,
        "id_import_fees": plan.id_import_fees,
        "id_referral_levels": plan.id_referral_levels,
        "start_block": plan.start_block,
        "native": plan.native.model_dump(),
        "reserves": [reserve.model_dump() for reserve in plan.reserves],
        "allocation_id": plan.allocation_id,
        "define_funding_amount": plan.define_funding_amount,
        "create_reserves": plan.create_reserves,
        "identity_exists": plan.identity_exists,
    }

    create_currency_request_record(
        {
            "id": request_id,
            "workflow_type": "fractional_token",
            "requested_name": name,
            "parent_namespace": parent,
            "native_coin": native_coin,
            "daemon_name": daemon_name,
            "primary_raddress": primary_raddress,
            "source_of_funds": source_of_funds,
            "status": "pending",
            "payload_json": json.dumps(payload),
            "progress_json": json.dumps({"reserve_index": 0, "reserve_phase": 0, "fund_index": 0}),
        }
    )

    return build_currency_request_response(request_id, "pending", "fractional_token", daemon_name, native_coin)


def currency_plan_template(mode: str = "auto") -> dict:
    simple_template = {
        "pre_allocation_id": "blockoneminer@",
        "pre_allocation_amount": 80000,
        "id_registration_fees": 25,
        "proof_protocol": 1,
        "define_options": 32,
        "define_funding_amount": 200.001,
    }

    fractional_template = {
        "initial_supply": 100000,
        "id_registration_fees": 50,
        "id_referral_levels": 0,
        "start_block": 28000,
        "native": {
            "name": "VRSCTEST",
            "weight": 0.5,
            "initial_contribution": 20,
        },
        "reserves": [
            {
                "name": "TENNIS",
                "supply": 80000,
                "identity_exists": False,
                "weight": 0.25,
                "initial_contribution": 40000,
            },
            {
                "name": "SAILING",
                "supply": 80000,
                "identity_exists": False,
                "weight": 0.25,
                "initial_contribution": 40000,
            },
        ],
        "allocation_id": "blockoneminer@",
        "define_funding_amount": 200.001,
        "create_reserves": True,
        "identity_exists": False,
    }

    template = {
        "name": "SIXTH",
        "parent": "bitcoins.vrsc",
        "native_coin": "VRSC",
        "primary_raddress": "R...",
        "mode": mode,
    }

    normalized_mode = mode.strip().lower()
    if normalized_mode in {"simple", "simple_token"}:
        template["mode"] = "simple_token"
        template["simple"] = simple_template
    elif normalized_mode in {"fractional", "fractional_token"}:
        template["mode"] = "fractional_token"
        template["fractional"] = fractional_template
    else:
        template["mode"] = "auto"
        template["simple"] = simple_template
        template["fractional"] = fractional_template

    return template


def create_simple_currency(request, svc):
    daemon_name = svc._resolve_currency_daemon_or_503(request.native_coin)
    svc._validate_currency_parent_or_403(request.parent)

    source_of_funds = svc.os.getenv("SOURCE_OF_FUNDS", "").strip()
    if not source_of_funds:
        raise svc.HTTPException(status_code=503, detail="SOURCE_OF_FUNDS is not configured")

    simple_plan = svc.CurrencySimplePlan(
        pre_allocation_id=request.pre_allocation_id,
        pre_allocation_amount=request.pre_allocation_amount,
        id_registration_fees=request.id_registration_fees,
        proof_protocol=request.proof_protocol,
        define_options=request.define_options,
        define_funding_amount=request.define_funding_amount,
    )
    return svc._enqueue_simple_currency_request(
        name=request.name,
        parent=request.parent,
        native_coin=request.native_coin,
        primary_raddress=request.primary_raddress,
        daemon_name=daemon_name,
        source_of_funds=source_of_funds,
        plan=simple_plan,
    )


def create_fractional_currency(request, svc):
    daemon_name = svc._resolve_currency_daemon_or_503(request.native_coin)
    svc._validate_currency_parent_or_403(request.parent)

    source_of_funds = svc.os.getenv("SOURCE_OF_FUNDS", "").strip()
    if not source_of_funds:
        raise svc.HTTPException(status_code=503, detail="SOURCE_OF_FUNDS is not configured")

    fractional_plan = svc.CurrencyFractionalPlan(
        initial_supply=request.initial_supply,
        id_registration_fees=request.id_registration_fees,
        id_import_fees=request.id_import_fees,
        id_referral_levels=request.id_referral_levels,
        start_block=request.start_block,
        native=request.native,
        reserves=request.reserves,
        allocation_id=request.allocation_id,
        define_funding_amount=request.define_funding_amount,
        create_reserves=request.create_reserves,
        identity_exists=request.identity_exists,
    )
    return svc._enqueue_fractional_currency_request(
        name=request.name,
        parent=request.parent,
        native_coin=request.native_coin,
        primary_raddress=request.primary_raddress,
        daemon_name=daemon_name,
        source_of_funds=source_of_funds,
        plan=fractional_plan,
    )


def create_currency_from_plan(request, svc):
    daemon_name = svc._resolve_currency_daemon_or_503(request.native_coin)
    svc._validate_currency_parent_or_403(request.parent)

    source_of_funds = svc.os.getenv("SOURCE_OF_FUNDS", "").strip()
    if not source_of_funds:
        raise svc.HTTPException(status_code=503, detail="SOURCE_OF_FUNDS is not configured")

    mode = (request.mode or "auto").strip().lower()
    if mode in {"auto", ""}:
        if request.fractional is not None:
            mode = "fractional_token"
        elif request.simple is not None:
            mode = "simple_token"
        else:
            raise svc.HTTPException(status_code=400, detail="Plan mode auto requires either 'simple' or 'fractional' section")
    elif mode == "simple":
        mode = "simple_token"
    elif mode == "fractional":
        mode = "fractional_token"

    if mode == "simple_token":
        if request.simple is None:
            raise svc.HTTPException(status_code=400, detail="mode simple_token requires 'simple' section")
        return svc._enqueue_simple_currency_request(
            name=request.name,
            parent=request.parent,
            native_coin=request.native_coin,
            primary_raddress=request.primary_raddress,
            daemon_name=daemon_name,
            source_of_funds=source_of_funds,
            plan=request.simple,
        )

    if mode == "fractional_token":
        if request.fractional is None:
            raise svc.HTTPException(status_code=400, detail="mode fractional_token requires 'fractional' section")
        return svc._enqueue_fractional_currency_request(
            name=request.name,
            parent=request.parent,
            native_coin=request.native_coin,
            primary_raddress=request.primary_raddress,
            daemon_name=daemon_name,
            source_of_funds=source_of_funds,
            plan=request.fractional,
        )

    raise svc.HTTPException(status_code=400, detail="mode must be one of: auto, simple, simple_token, fractional, fractional_token")
