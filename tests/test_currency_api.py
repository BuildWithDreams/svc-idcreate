import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import id_create_service


def _build_client(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))
    monkeypatch.setenv("REGISTRAR_API_KEYS", "test-key")
    monkeypatch.setenv("SOURCE_OF_FUNDS", "RsourceFundsAddr")
    monkeypatch.setattr(id_create_service, "_resolve_daemon_by_native_coin", lambda _: "verusd_vrsc")

    with TestClient(id_create_service.app) as client:
        yield client


def test_create_simple_currency_request(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    payload = {
        "name": "TENNIS",
        "parent": "bitcoins.vrsc",
        "native_coin": "VRSC",
        "primary_raddress": "RtestAddress",
        "pre_allocation_id": "blockoneminer@",
        "pre_allocation_amount": 80000,
    }
    resp = client.post(
        "/api/currency/simple",
        json=payload,
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert body["workflow_type"] == "simple_token"

    status_resp = client.get(f"/api/currency/status/{body['request_id']}")
    assert status_resp.status_code == 200
    status_body = status_resp.json()
    assert status_body["workflow_type"] == "simple_token"
    assert status_body["payload"]["name"] == "TENNIS"


def test_create_fractional_currency_request(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    payload = {
        "name": "SIXTH",
        "parent": "bitcoins.vrsc",
        "native_coin": "VRSC",
        "primary_raddress": "RtestAddress",
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
                "weight": 0.25,
                "initial_contribution": 40000,
            }
        ],
        "create_reserves": False,
        "prepare_fractional_identity": False,
    }
    resp = client.post(
        "/api/currency/fractional",
        json=payload,
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert body["workflow_type"] == "fractional_token"

    status_resp = client.get(f"/api/currency/status/{body['request_id']}")
    assert status_resp.status_code == 200
    status_body = status_resp.json()
    assert status_body["payload"]["create_reserves"] is False
    assert status_body["payload"]["prepare_fractional_identity"] is False


def test_create_fractional_currency_request_allows_missing_supply_when_not_creating_reserves(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    payload = {
        "name": "DPNK",
        "parent": "VRSCTEST",
        "native_coin": "VRSCTEST",
        "primary_raddress": "RtestAddress",
        "initial_supply": 325000,
        "id_registration_fees": 777,
        "id_referral_levels": 3,
        "start_block": 1057000,
        "native": {
            "name": "VRSCTEST",
            "weight": 0.55,
            "initial_contribution": 20,
        },
        "reserves": [
            {
                "name": "SPORTS",
                "weight": 0.2,
                "initial_contribution": 0.1,
            },
            {
                "name": "SAILING",
                "weight": 0.2,
                "initial_contribution": 0.1,
            },
            {
                "name": "YEN",
                "weight": 0.05,
                "initial_contribution": 0.1,
            },
        ],
        "define_funding_amount": 200.001,
        "create_reserves": False,
        "prepare_fractional_identity": True,
    }
    resp = client.post(
        "/api/currency/fractional",
        json=payload,
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 202


def test_create_fractional_currency_request_requires_supply_when_creating_reserves(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    payload = {
        "name": "DPNK",
        "parent": "VRSCTEST",
        "native_coin": "VRSCTEST",
        "primary_raddress": "RtestAddress",
        "initial_supply": 325000,
        "id_registration_fees": 777,
        "id_referral_levels": 3,
        "start_block": 1057000,
        "native": {
            "name": "VRSCTEST",
            "weight": 0.55,
            "initial_contribution": 20,
        },
        "reserves": [
            {
                "name": "SPORTS",
                "weight": 0.2,
                "initial_contribution": 0.1,
            }
        ],
        "define_funding_amount": 200.001,
        "create_reserves": True,
        "prepare_fractional_identity": True,
    }
    resp = client.post(
        "/api/currency/fractional",
        json=payload,
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 422
    assert "fractional.reserves[].supply is required when create_reserves is true" in str(resp.json())


def test_currency_request_rejects_missing_api_key(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    resp = client.post(
        "/api/currency/simple",
        json={
            "name": "TENNIS",
            "parent": "bitcoins.vrsc",
            "native_coin": "VRSC",
            "primary_raddress": "RtestAddress",
            "pre_allocation_amount": 100,
        },
    )

    assert resp.status_code == 403


def test_plan_endpoint_auto_routes_to_simple(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    resp = client.post(
        "/api/currency/plan",
        json={
            "name": "TENNIS",
            "parent": "bitcoins.vrsc",
            "native_coin": "VRSC",
            "primary_raddress": "RtestAddress",
            "mode": "auto",
            "simple": {
                "pre_allocation_id": "blockoneminer@",
                "pre_allocation_amount": 80000,
            },
        },
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 202
    body = resp.json()
    assert body["workflow_type"] == "simple_token"

    status_resp = client.get(f"/api/currency/status/{body['request_id']}")
    assert status_resp.status_code == 200
    status_body = status_resp.json()
    assert status_body["workflow_type"] == "simple_token"
    assert status_body["payload"]["pre_allocation_amount"] == 80000


def test_plan_endpoint_explicit_fractional(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    resp = client.post(
        "/api/currency/plan",
        json={
            "name": "SIXTH",
            "parent": "bitcoins.vrsc",
            "native_coin": "VRSC",
            "primary_raddress": "RtestAddress",
            "mode": "fractional",
            "fractional": {
                "initial_supply": 100000,
                "id_registration_fees": 50,
                "id_referral_levels": 0,
                "start_block": 28000,
                "native": {
                    "name": "VRSCTEST",
                    "weight": 0.5,
                    "initial_contribution": 20,
                },
                "reserves": [],
                "create_reserves": False,
                "prepare_fractional_identity": False,
            },
        },
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 202
    body = resp.json()
    assert body["workflow_type"] == "fractional_token"

    status_resp = client.get(f"/api/currency/status/{body['request_id']}")
    assert status_resp.status_code == 200
    status_body = status_resp.json()
    assert status_body["payload"]["prepare_fractional_identity"] is False


def test_plan_endpoint_fractional_allows_missing_supply_when_not_creating_reserves(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    resp = client.post(
        "/api/currency/plan",
        json={
            "name": "DPNK",
            "parent": "VRSCTEST",
            "native_coin": "VRSCTEST",
            "primary_raddress": "RtestAddress",
            "mode": "fractional",
            "fractional": {
                "initial_supply": 325000,
                "id_registration_fees": 777,
                "id_referral_levels": 3,
                "start_block": 1057000,
                "native": {
                    "name": "VRSCTEST",
                    "weight": 0.55,
                    "initial_contribution": 20,
                },
                "reserves": [
                    {
                        "name": "SPORTS",
                        "weight": 0.2,
                        "initial_contribution": 0.1,
                    },
                    {
                        "name": "SAILING",
                        "weight": 0.2,
                        "initial_contribution": 0.1,
                    },
                    {
                        "name": "YEN",
                        "weight": 0.05,
                        "initial_contribution": 0.1,
                    },
                ],
                "define_funding_amount": 200.001,
                "create_reserves": False,
                "prepare_fractional_identity": True,
            },
        },
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 202


def test_plan_endpoint_parent_allowlist_is_case_insensitive(monkeypatch, tmp_path):
    monkeypatch.setenv("REGISTRAR_ALLOWED_PARENTS", "Sailing.VRSCTEST,Sports.VRSCTEST, VRSCTEST")
    client = next(_build_client(monkeypatch, tmp_path))

    resp = client.post(
        "/api/currency/plan",
        json={
            "name": "DPNK",
            "parent": "VRSCTEST",
            "native_coin": "VRSCTEST",
            "primary_raddress": "RtestAddress",
            "mode": "fractional",
            "fractional": {
                "initial_supply": 325000,
                "id_registration_fees": 50,
                "id_referral_levels": 0,
                "start_block": 28000,
                "native": {
                    "name": "VRSCTEST",
                    "weight": 0.55,
                    "initial_contribution": 20,
                },
                "reserves": [
                    {
                        "name": "SPORTS",
                        "weight": 0.2,
                        "initial_contribution": 0.1,
                    }
                ],
                "define_funding_amount": 200.001,
                "create_reserves": False,
                "prepare_fractional_identity": True,
                "identity_exists": False,
            },
        },
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 202


def test_plan_endpoint_rejects_missing_mode_section(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    resp = client.post(
        "/api/currency/plan",
        json={
            "name": "TENNIS",
            "parent": "bitcoins.vrsc",
            "native_coin": "VRSC",
            "primary_raddress": "RtestAddress",
            "mode": "simple",
        },
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 400


def test_plan_endpoint_rejects_invalid_mode(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    resp = client.post(
        "/api/currency/plan",
        json={
            "name": "TENNIS",
            "parent": "bitcoins.vrsc",
            "native_coin": "VRSC",
            "primary_raddress": "RtestAddress",
            "mode": "surprise",
            "simple": {
                "pre_allocation_id": "blockoneminer@",
                "pre_allocation_amount": 80000,
            },
        },
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 400


def test_plan_endpoint_accepts_mixed_case_fractional_token_mode(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    resp = client.post(
        "/api/currency/plan",
        json={
            "name": "DPNK",
            "parent": "VRSCTEST",
            "native_coin": "VRSCTEST",
            "primary_raddress": "RtestAddress",
            "mode": "FrAcTiOnAl_ToKeN",
            "fractional": {
                "initial_supply": 325000,
                "id_registration_fees": 50,
                "id_referral_levels": 0,
                "start_block": 28000,
                "native": {
                    "name": "VRSCTEST",
                    "weight": 0.55,
                    "initial_contribution": 20,
                },
                "reserves": [],
                "create_reserves": False,
                "prepare_fractional_identity": True,
                "identity_exists": False,
            },
        },
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 202
    assert resp.json()["workflow_type"] == "fractional_token"


def test_plan_template_endpoint_auto_mode(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    resp = client.get("/api/currency/plan/template?mode=auto")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "auto"
    assert "simple" in body["template"]
    assert "fractional" in body["template"]
    assert "identity_exists" in body["template"]["fractional"]
    assert "identity_exists" in body["template"]["fractional"]["reserves"][0]


def test_plan_template_endpoint_fractional_mode_contains_identity_flags(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    resp = client.get("/api/currency/plan/template?mode=fractional")
    assert resp.status_code == 200
    body = resp.json()
    assert body["template"]["mode"] == "fractional_token"
    fractional = body["template"]["fractional"]
    assert fractional["identity_exists"] is False
    assert fractional["reserves"][0]["identity_exists"] is False


def test_plan_template_endpoint_mode_query_is_case_insensitive(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    resp = client.get("/api/currency/plan/template?mode=FrAcTiOnAl_ToKeN")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "fractional_token"
    assert body["template"]["mode"] == "fractional_token"


def test_plan_template_endpoint_simple_mode(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    resp = client.get("/api/currency/plan/template?mode=simple")
    assert resp.status_code == 200
    body = resp.json()
    assert body["template"]["mode"] == "simple_token"
    assert "simple" in body["template"]
    assert "fractional" not in body["template"]


def test_plan_template_endpoint_rejects_invalid_mode(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    resp = client.get("/api/currency/plan/template?mode=invalid")
    assert resp.status_code == 400


def test_currency_plan_reference_form(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    resp = client.get("/currency/plan")
    assert resp.status_code == 200
    assert "Currency Plan Console" in resp.text
    assert "currency-plan-form" in resp.text
    assert "/api/currency/plan/template" in resp.text
    assert '<option value="auto">auto</option>' in resp.text
    assert '<option value="simple_token">simple_token</option>' in resp.text
    assert '<option value="fractional_token">fractional_token</option>' in resp.text
