from verus_node_rpc import NodeRpc
from SFConstants import DAEMON_CONFIGS
import logging

logger = logging.getLogger(__name__)

class VerusRPCManager:
    # This dictionary acts as a cache for our open connections
    _connections = {}

    @classmethod
    def get_connection(cls, daemon_name: str) -> NodeRpc:
        """
        Returns an existing RPC connection for the daemon, or creates a new one
        if it doesn't exist yet.
        """
        # 1. Check if we already have this connection open
        if daemon_name in cls._connections:
            logger.info("RPC connection cache hit daemon=%s", daemon_name)
            return cls._connections[daemon_name]

        # 2. If not, validate that we have config for it
        if daemon_name not in DAEMON_CONFIGS:
            error_msg = (
                f"Daemon '{daemon_name}' is not enabled or its configuration is unavailable. "
                f"Set {daemon_name}_rpc_enabled=true and provide RPC credentials."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        # 3. Create the new connection
        cfg = DAEMON_CONFIGS[daemon_name]
        logger.info(
            "Creating RPC connection daemon=%s host=%s port=%s user=%s",
            daemon_name,
            cfg.get("host"),
            cfg.get("port"),
            cfg.get("user"),
        )
        try:
            connection = NodeRpc(
                cfg["user"], 
                cfg["password"], 
                cfg["port"], 
                cfg["host"]
            )
            
            # Store it in our cache so we don't have to reconnect next time
            cls._connections[daemon_name] = connection
            logger.info(f"Created new RPC connection for {daemon_name}")
            
            return connection

        except Exception as e:
            logger.exception("Failed to create RPC connection daemon=%s error_type=%s", daemon_name, type(e).__name__)
            raise e
