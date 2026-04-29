import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# Keep tests isolated from optional runtime dependency.
if "slickrpc" not in sys.modules:
    sys.modules["slickrpc"] = types.SimpleNamespace(Proxy=object)

from verus_node_rpc import NodeRpc


class _FakeRpc:
    def __init__(self):
        self.last_update_payload = None

    def updateidentity(self, payload):
        self.last_update_payload = payload
        return {"txid": "txid-1"}


class _FakeRpcWrapper(NodeRpc):
    pass


def _make_node_rpc(fake_rpc):
    rpc = _FakeRpcWrapper.__new__(_FakeRpcWrapper)
    rpc.rpc_connection = fake_rpc
    return rpc


def test_update_identity_forwards_payload_exactly():
    fake_rpc = _FakeRpc()
    rpc = _make_node_rpc(fake_rpc)

    payload = {
        "name": "trial1",
        "contentmultimap": {
            "iKey": [{"data": {"filename": "/tmp/chunk-0.bin", "createmmr": True}}]
        },
    }

    result = rpc.update_identity(payload)

    assert result["txid"] == "txid-1"
    assert fake_rpc.last_update_payload == payload


def test_build_contentmultimap_data_wrapper_nests_data_under_key_value_list():
    fake_rpc = _FakeRpc()
    rpc = _make_node_rpc(fake_rpc)

    result = rpc.build_contentmultimap_data_wrapper(
        vdxf_key="iChunkKey",
        identity_address="trial1.filestorage@",
        filename="/tmp/chunk-0.bin",
        label="chunk-0",
        mimetype="application/octet-stream",
    )

    assert "iChunkKey" in result
    assert isinstance(result["iChunkKey"], list)
    assert "data" in result["iChunkKey"][0]
    assert result["iChunkKey"][0]["data"]["filename"] == "/tmp/chunk-0.bin"
