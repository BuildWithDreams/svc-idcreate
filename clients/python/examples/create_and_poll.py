import os
import time

from idcreate_client import IdCreateApiError, IdCreateClient


def main():
    base_url = os.getenv("IDCREATE_BASE_URL", "http://localhost:5003")
    api_key = os.getenv("IDCREATE_API_KEY", "")

    name = os.getenv("IDCREATE_NAME", "alice")
    parent = os.getenv("IDCREATE_PARENT", "bitcoins.vrsc")
    native_coin = os.getenv("IDCREATE_NATIVE_COIN", "VRSC")
    primary_raddress = os.getenv("IDCREATE_PRIMARY_RADDRESS", "RaliceAddress")

    timeout_seconds = int(os.getenv("IDCREATE_WAIT_TIMEOUT_SECONDS", "300"))
    poll_seconds = int(os.getenv("IDCREATE_WAIT_POLL_SECONDS", "5"))

    client = IdCreateClient(base_url=base_url, api_key=api_key)

    try:
        created = client.create_identity(
            name=name,
            parent=parent,
            native_coin=native_coin,
            primary_raddress=primary_raddress,
        )
        request_id = created["request_id"]
        print(f"Created request_id={request_id}, status={created.get('status')}")

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            status = client.get_identity_request_status(request_id)
            current = status.get("status")
            print(f"status={current}")

            if current in {"complete", "failed"}:
                print("Final response:")
                print(status)
                return

            time.sleep(poll_seconds)

        print("Timed out waiting for terminal status")

    except IdCreateApiError as exc:
        print(f"API error: status={exc.status_code} message={exc.message} body={exc.body}")
        raise


if __name__ == "__main__":
    main()
