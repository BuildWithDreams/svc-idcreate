import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from provisioning.adapters import HttpProvisioningAdapter
from provisioning.router import _build_provisioning_adapter


def test_adapter_selection_defaults_to_http(monkeypatch):
    monkeypatch.delenv("PROVISIONING_ADAPTER_MODE", raising=False)
    monkeypatch.setenv("PROVISIONING_SERVICE_URL", "http://localhost:5055")

    adapter = _build_provisioning_adapter()
    assert isinstance(adapter, HttpProvisioningAdapter)


def test_adapter_selection_uses_http_when_enabled(monkeypatch):
    monkeypatch.setenv("PROVISIONING_ADAPTER_MODE", "http")
    monkeypatch.setenv("PROVISIONING_SERVICE_URL", "http://localhost:5055")
    monkeypatch.setenv("PROVISIONING_HTTP_TIMEOUT_SECONDS", "4")

    adapter = _build_provisioning_adapter()
    assert isinstance(adapter, HttpProvisioningAdapter)
    assert adapter.base_url == "http://localhost:5055"
    assert adapter.timeout_seconds == 4


def test_adapter_selection_errors_when_http_missing_url(monkeypatch):
    monkeypatch.setenv("PROVISIONING_ADAPTER_MODE", "http")
    monkeypatch.delenv("PROVISIONING_SERVICE_URL", raising=False)

    with pytest.raises(RuntimeError, match="PROVISIONING_SERVICE_URL"):
        _build_provisioning_adapter()
