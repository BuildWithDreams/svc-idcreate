import { IdCreateApiError, IdCreateClient } from "../src/client";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main() {
  const baseUrl = process.env.IDCREATE_BASE_URL ?? "http://localhost:5003";
  const apiKey = process.env.IDCREATE_API_KEY ?? "";

  const name = process.env.IDCREATE_NAME ?? "alice";
  const parent = process.env.IDCREATE_PARENT ?? "bitcoins.vrsc";
  const nativeCoin = process.env.IDCREATE_NATIVE_COIN ?? "VRSC";
  const primaryRaddress = process.env.IDCREATE_PRIMARY_RADDRESS ?? "RaliceAddress";

  const timeoutSeconds = Number(process.env.IDCREATE_WAIT_TIMEOUT_SECONDS ?? "300");
  const pollSeconds = Number(process.env.IDCREATE_WAIT_POLL_SECONDS ?? "5");

  const client = new IdCreateClient(baseUrl, apiKey);

  try {
    const created = await client.createIdentity({
      name,
      parent,
      native_coin: nativeCoin,
      primary_raddress: primaryRaddress,
    });

    const requestId = created.request_id;
    console.log(`Created request_id=${requestId}, status=${created.status}`);

    const deadline = Date.now() + timeoutSeconds * 1000;
    while (Date.now() < deadline) {
      const status = await client.getIdentityRequestStatus(requestId);
      const current = String(status.status ?? "unknown");
      console.log(`status=${current}`);

      if (current === "complete" || current === "failed") {
        console.log("Final response:");
        console.log(JSON.stringify(status, null, 2));
        return;
      }

      await sleep(pollSeconds * 1000);
    }

    console.error("Timed out waiting for terminal status");
    process.exitCode = 1;
  } catch (err: any) {
    if (err instanceof IdCreateApiError) {
      console.error(`API error: status=${err.statusCode} message=${err.message}`);
      console.error(err.body);
      process.exitCode = 1;
      return;
    }
    throw err;
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
