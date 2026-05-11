import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from verus_node_rpc import NodeRpc


class _FailingDefineRpcConnection:
    def definecurrency(self, params):
        raise Exception("boom")


class _PassingSendCurrencyRpcConnection:
    def sendcurrency(self, from_address, params):
        return "opid-abc"


def test_define_currency_logs_submit_params_on_error(monkeypatch, caplog):
    monkeypatch.setattr(NodeRpc, "rpc_connect", lambda self, *_args: _FailingDefineRpcConnection())

    rpc = NodeRpc("user", "pass", 1234, "127.0.0.1")

    caplog.set_level("INFO")
    with pytest.raises(Exception, match="Error with define currency"):
        rpc.define_currency({"name": "DPNK", "startblock": 1057000})

    assert any("rpc.definecurrency.submit" in rec.message for rec in caplog.records)
    assert any('"name": "DPNK"' in rec.message for rec in caplog.records)


def test_send_currency_logs_submit_params(monkeypatch, caplog):
    monkeypatch.setattr(NodeRpc, "rpc_connect", lambda self, *_args: _PassingSendCurrencyRpcConnection())

    rpc = NodeRpc("user", "pass", 1234, "127.0.0.1")

    caplog.set_level("INFO")
    result = rpc.send_currency("Rfrom", [{"currency": "VRSCTEST", "address": "DPNK@", "amount": 200.001}])

    assert result == "opid-abc"
    assert any("rpc.sendcurrency.submit" in rec.message for rec in caplog.records)
    assert any('"amount": 200.001' in rec.message for rec in caplog.records)
