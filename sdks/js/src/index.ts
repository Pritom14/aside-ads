/**
 * aside-ads: client for Aside, the self-hosted ad server for AI chat apps.
 *
 * Use `AsideClient` on your server (it needs your publisher API key) to fetch a
 * sponsored ad for a conversation. Render the ad BESIDE the model's answer with its
 * "Sponsored" label, and call `trackImpression` (works in the browser without a key)
 * when it is shown. Ads are optional: on any error `getSponsored` resolves to null.
 *
 * MIT licensed. No runtime dependencies. Works in Node 18+, Deno, Bun and browsers.
 */

export type Role = "user" | "assistant" | "system" | (string & {});

export interface ChatMessage {
  role: Role;
  content: string;
}

export interface Geo {
  /** ISO 3166-1 alpha-2 country code, e.g. "IN". */
  country?: string;
  region?: string;
  lat?: number;
  lng?: number;
}

export interface GetSponsoredOptions {
  /** Recent conversation. Sent only to your own Aside server and never stored there. */
  context: string | ChatMessage[];
  /** Opaque end-user or session id. Hashed by the server; used for frequency caps. */
  userId?: string;
  /** Placement key configured for your publisher. Defaults to "default". */
  placement?: string;
  geo?: Geo;
  /** Topic hints from your app, e.g. ["finance"]. */
  categories?: string[];
  /** Force no ad for this request. */
  sensitive?: boolean;
  signal?: AbortSignal;
}

export interface SponsoredAd {
  label: "Sponsored";
  headline: string;
  body: string;
  cta: string;
  /** Signed click URL. Redirects to the advertiser. */
  url: string;
  /** Fetch (GET or POST) when the card is shown. */
  impressionUrl: string;
  advertiser: string;
  displayUrl: string;
  imageUrl: string | null;
  id: string;
  campaignId: string;
  requestId: string;
  relevance: number;
}

export interface FetchResponseLike {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}

export type FetchLike = (
  input: string,
  init?: {
    method?: string;
    headers?: Record<string, string>;
    body?: string;
    signal?: AbortSignal;
    keepalive?: boolean;
    mode?: "cors" | "no-cors" | "same-origin";
  },
) => Promise<FetchResponseLike>;

export interface AsideClientOptions {
  /** Base URL of your Aside server, e.g. "http://127.0.0.1:8000". */
  baseUrl: string;
  /** Publisher API key. Keep it on the server; never ship it to browsers. */
  apiKey?: string;
  /** Request timeout in milliseconds. Default 800. */
  timeoutMs?: number;
  /** Custom fetch implementation (defaults to globalThis.fetch). */
  fetch?: FetchLike;
  /** Called with errors that are otherwise swallowed. */
  onError?: (error: unknown) => void;
}

interface ServeResponseWire {
  request_id: string;
  reason?: string | null;
  ad: null | {
    id: string;
    campaign_id: string;
    label: "Sponsored";
    advertiser: string;
    headline: string;
    body: string;
    cta: string;
    url: string;
    display_url: string;
    image_url: string | null;
    impression_url: string;
    relevance: number;
  };
}

function defaultFetch(): FetchLike {
  const f = (globalThis as { fetch?: unknown }).fetch;
  if (typeof f !== "function") {
    throw new Error("aside-ads: no global fetch; pass options.fetch");
  }
  return f.bind(globalThis) as FetchLike;
}

function withTimeout(ms: number, outer?: AbortSignal): { signal: AbortSignal; done: () => void } {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  const onAbort = () => controller.abort();
  outer?.addEventListener("abort", onAbort, { once: true });
  return {
    signal: controller.signal,
    done: () => {
      clearTimeout(timer);
      outer?.removeEventListener("abort", onAbort);
    },
  };
}

function toAd(data: ServeResponseWire): SponsoredAd | null {
  const a = data.ad;
  if (!a) return null;
  return {
    label: "Sponsored",
    headline: a.headline,
    body: a.body,
    cta: a.cta,
    url: a.url,
    impressionUrl: a.impression_url,
    advertiser: a.advertiser,
    displayUrl: a.display_url,
    imageUrl: a.image_url,
    id: a.id,
    campaignId: a.campaign_id,
    requestId: data.request_id,
    relevance: a.relevance,
  };
}

export class AsideClient {
  readonly baseUrl: string;
  private readonly apiKey?: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: FetchLike;
  private readonly onError?: (error: unknown) => void;
  /** Server's reason for the last empty result (debugging only). */
  lastReason: string | null = null;

  constructor(options: AsideClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.apiKey = options.apiKey;
    this.timeoutMs = options.timeoutMs ?? 800;
    this.fetchImpl = options.fetch ?? defaultFetch();
    this.onError = options.onError;
  }

  /** Returns a relevant sponsored ad for the conversation, or null. Never throws. */
  async getSponsored(options: GetSponsoredOptions): Promise<SponsoredAd | null> {
    if (!this.apiKey) {
      this.onError?.(new Error("aside-ads: getSponsored needs an apiKey (call it from your server)"));
      return null;
    }
    const body: Record<string, unknown> = {
      context: options.context,
      placement: options.placement ?? "default",
      sensitive: options.sensitive ?? false,
    };
    if (options.userId !== undefined) body.user_id = options.userId;
    if (options.geo) body.geo = options.geo;
    if (options.categories) body.categories = options.categories;

    const t = withTimeout(this.timeoutMs, options.signal);
    try {
      const res = await this.fetchImpl(`${this.baseUrl}/v1/serve`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${this.apiKey}` },
        body: JSON.stringify(body),
        signal: t.signal,
      });
      if (!res.ok) {
        this.lastReason = `http_${res.status}`;
        this.onError?.(new Error(`aside-ads: serve returned HTTP ${res.status}`));
        return null;
      }
      const data = (await res.json()) as ServeResponseWire;
      this.lastReason = data.reason ?? null;
      return toAd(data);
    } catch (error) {
      this.lastReason = "error";
      this.onError?.(error);
      return null;
    } finally {
      t.done();
    }
  }

  /**
   * Records that the ad was shown. With an apiKey this posts to /v1/impressions
   * (server-side, authenticated); without one it fetches the signed impression URL.
   */
  async trackImpression(ad: SponsoredAd | string): Promise<boolean> {
    const url = typeof ad === "string" ? ad : ad.impressionUrl;
    if (!this.apiKey) return trackImpression(url, this.fetchImpl);
    const t = withTimeout(this.timeoutMs);
    try {
      const res = await this.fetchImpl(`${this.baseUrl}/v1/impressions`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${this.apiKey}` },
        body: JSON.stringify({ token: url }),
        signal: t.signal,
      });
      return res.ok;
    } catch (error) {
      this.onError?.(error);
      return false;
    } finally {
      t.done();
    }
  }
}

/**
 * Browser-friendly impression beacon. Uses navigator.sendBeacon when available,
 * otherwise a keepalive fetch. Needs no API key: the URL carries a signed token.
 */
export async function trackImpression(ad: SponsoredAd | string, fetchImpl?: FetchLike): Promise<boolean> {
  const url = typeof ad === "string" ? ad : ad.impressionUrl;
  const nav = (globalThis as { navigator?: { sendBeacon?: (u: string) => boolean } }).navigator;
  if (!fetchImpl && nav && typeof nav.sendBeacon === "function") {
    try {
      if (nav.sendBeacon(url)) return true;
    } catch {
      // fall through to fetch
    }
  }
  try {
    const f = fetchImpl ?? defaultFetch();
    const res = await f(url, { method: "POST", keepalive: true, mode: "no-cors" });
    return res.ok || res.status === 0;
  } catch {
    return false;
  }
}

export function createClient(options: AsideClientOptions): AsideClient {
  return new AsideClient(options);
}
