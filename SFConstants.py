import os
import json
import ast
from dotenv import load_dotenv
load_dotenv(verbose=True)

# currencies
TICKER_VRSCTEST="VRSCTEST"
TICKER_VRSC="VRSC"
TICKER_ETH="vETH"
TICKER_VARRR="vARRR"
TICKER_VDEX="vDEX"
TICKER_CHIPS="CHIPS"
TICKER_DAI=f"DAI.{TICKER_ETH}"
TICKER_TBTC=f"tBTC.{TICKER_ETH}"
TICKER_BVETH=f"Bridge.{TICKER_ETH}"
TICKER_BVARRR=f"Bridge.{TICKER_VARRR}"
TICKER_BVDEX=f"Bridge.{TICKER_VDEX}"
TICKER_MKR=f"MKR.{TICKER_ETH}"
TICKER_SWITCH="Switch"
TICKER_USDC=f"vUSDC.{TICKER_ETH}"
TICKER_EURC=f"EURC.{TICKER_ETH}"
TICKER_PURE="Pure"
TICKER_KAIJU="Kaiju"
TICKER_USDT=f"vUSDT.{TICKER_ETH}"
TICKER_VDEX="vDEX"
TICKER_NATIVRSCBASKET="NATI"
TICKER_NATIOWL="iH37kRsdfoHtHK5TottP1Yfq8hBSHz9btw"
TICKER_NATI10K=f"NATI.{TICKER_ETH}"


CURRENCY_DICTIONARY_ID_TO_TICKER = [
    { "i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV": TICKER_VRSC },
    { "iGBs4DWztRNvNEJBt4mqHszLxfKTNHTkhM": TICKER_DAI },
    { "iCkKJuJScy4Z6NSDK7Mt42ZAB2NEnAE1o4": TICKER_MKR },
    { "i9nwxtKuVYX4MSbeULLiK2ttVi6rUEhh4X": TICKER_ETH },
    { "iS8TfRPfVpKo5FVfSUzfHBQxo9KuzpnqLU": TICKER_TBTC },
    { "iExBJfZYK7KREDpuhj6PzZBzqMAKaFg7d2": TICKER_VARRR },
    { "i3f7tSctFkiPpiedY8QR5Tep9p4qDVebDx": TICKER_BVETH },
    { "i4Xr5TAMrDTD99H69EemhjDxJ4ktNskUtc": TICKER_SWITCH },
    { "i61cV2uicKSi1rSMQCBNQeSYC3UAi9GVzd": TICKER_USDC },
    { "iC5TQFrFXSYLQGkiZ8FYmZHFJzaRF5CYgE": TICKER_EURC },
    { "iHax5qYQGbcMGqJKKrPorpzUBX2oFFXGnY": TICKER_PURE },
    { "i9kVWKU2VwARALpbXn4RS9zvrhvNRaUibb": TICKER_KAIJU },
    { "i9oCSqKALwJtcv49xUKS2U2i79h1kX6NEY": TICKER_USDT },
    { "i6j1rzjgrDhSmUYiTtp21J8Msiudv5hgt9": TICKER_BVDEX},
    { "iHog9UCTrn95qpUBFCZ7kKz7qWdMA8MQ6N": TICKER_VDEX },
    { "iRt7tpLewArQnRddBVFARGKJStK6w5pDmC": TICKER_NATIVRSCBASKET },
    { "iH37kRsdfoHtHK5TottP1Yfq8hBSHz9btw": TICKER_NATIOWL },
    { "iL62spNN42Vqdxh8H5nrfNe8d6Amsnfkdx": TICKER_NATI10K},
    { "iD5WRg7jdQM1uuoVHsBCAEKfJCKGs1U3TB": TICKER_BVARRR }
]

# daemons
DAEMON_VERUSD_VRSCTEST="verusd_vrsctest"
DAEMON_VERUSD_VRSC="verusd_vrsc"
DAEMON_VERUSD_VARRR="verusd_varrr"
DAEMON_VERUSD_VDEX="verusd_vdex"
DAEMON_VERUSD_CHIPS="verusd_chips"
VTRC_DAEMONS=[
    DAEMON_VERUSD_VRSC, 
    DAEMON_VERUSD_VARRR, 
    DAEMON_VERUSD_VDEX, 
    DAEMON_VERUSD_CHIPS,
    DAEMON_VERUSD_VRSCTEST ]

VTRC_NATIVE_COINS = {
    DAEMON_VERUSD_VRSCTEST: TICKER_VRSCTEST,
    DAEMON_VERUSD_VRSC: TICKER_VRSC, 
    DAEMON_VERUSD_VARRR: TICKER_VARRR, 
    DAEMON_VERUSD_VDEX: TICKER_VDEX,
    DAEMON_VERUSD_CHIPS: TICKER_CHIPS }

def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _build_daemon_config(daemon_name: str) -> dict:
    return {
        "user": os.getenv(f"{daemon_name}_rpc_user"),
        "password": os.getenv(f"{daemon_name}_rpc_password"),
        "port": os.getenv(f"{daemon_name}_rpc_port"),
        "host": os.getenv(f"{daemon_name}_rpc_host"),
    }


def _validate_daemon_config(daemon_name: str, cfg: dict):
    missing_fields = [k for k, v in cfg.items() if v in (None, "")]
    if missing_fields:
        missing = ", ".join(missing_fields)
        raise ValueError(
            f"{daemon_name} is enabled but missing required RPC env vars: {missing}"
        )


DAEMON_CONFIGS = {}
for daemon_name in VTRC_DAEMONS:
    enabled = _env_bool(f"{daemon_name}_rpc_enabled", default=False)
    if not enabled:
        continue

    daemon_cfg = _build_daemon_config(daemon_name)
    _validate_daemon_config(daemon_name, daemon_cfg)
    DAEMON_CONFIGS[daemon_name] = daemon_cfg

ENABLED_DAEMONS = list(DAEMON_CONFIGS.keys())
