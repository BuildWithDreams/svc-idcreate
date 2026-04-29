export class IdCreateApiError extends Error {
  statusCode: number;
  body: unknown;

  constructor(statusCode: number, message: string, body: unknown = null) {
    super(`HTTP ${statusCode}: ${message}`);
    this.statusCode = statusCode;
    this.body = body;
  }
}

export type CreateIdentityRequest = {
  name: string;
  parent: string;
  native_coin: string;
  primary_raddress: string;
  webhook_url?: string;
  webhook_secret?: string;
};

export type CreateIdentityResponse = {
  request_id: string;
  status: string;
  daemon: string;
  native_coin: string;
  txid_rnc: string | null;
};

export type RegistrationStatusResponse = Record<string, unknown>;

export type FailuresResponse = {
  count: number;
  items: Array<Record<string, unknown>>;
};

export class IdCreateClient {
  private baseUrl: string;
  private apiKey?: string;
  private timeoutMs: number;

  constructor(baseUrl: string, apiKey?: string, timeoutMs = 10000) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.apiKey = apiKey;
    this.timeoutMs = timeoutMs;
  }

  private headers(): HeadersInit {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (this.apiKey) {
      headers["X-API-Key"] = this.apiKey;
    }
    return headers;
  }

  private async request<T>(method: string, path: string, payload?: unknown): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers: this.headers(),
        body: payload === undefined ? undefined : JSON.stringify(payload),
        signal: controller.signal,
      });

      const text = await response.text();
      const parsed = text ? JSON.parse(text) : null;

      if (!response.ok) {
        const message =
          parsed && typeof parsed === "object" && "detail" in parsed
            ? String((parsed as Record<string, unknown>).detail)
            : response.statusText;
        throw new IdCreateApiError(response.status, message, parsed);
      }

      return parsed as T;
    } catch (err) {
      if (err instanceof IdCreateApiError) {
        throw err;
      }
      throw new IdCreateApiError(0, `Transport error: ${String(err)}`);
    } finally {
      clearTimeout(timer);
    }
  }

  async health(nativeCoin?: string): Promise<Record<string, unknown>> {
    const query = nativeCoin ? `?native_coin=${encodeURIComponent(nativeCoin)}` : "";
    return this.request<Record<string, unknown>>("GET", `/health${query}`);
  }

  async createIdentity(input: CreateIdentityRequest): Promise<CreateIdentityResponse> {
    return this.request<CreateIdentityResponse>("POST", "/api/register", input);
  }

  async getIdentityRequestStatus(requestId: string): Promise<RegistrationStatusResponse> {
    return this.request<RegistrationStatusResponse>("GET", `/api/status/${encodeURIComponent(requestId)}`);
  }

  async listRecentIdentityFailures(limit = 20): Promise<FailuresResponse> {
    return this.request<FailuresResponse>("GET", `/api/registrations/failures?limit=${limit}`);
  }

  async requeueIdentityWebhook(requestId: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>("POST", `/api/webhook/requeue/${encodeURIComponent(requestId)}`, {});
  }
}
