/**
 * Access token handling for every call the UI makes to the agent.
 *
 * A shared link is the point of this: someone outside the lab is handed one URL
 * carrying a token. The token is taken out of the address bar immediately and
 * stored, because a token left in the URL ends up in browser history, server logs
 * and referrer headers, and would be copied along with any screenshot of the
 * address bar.
 *
 * Local rather than session storage: sessionStorage is scoped to a single tab, so
 * a reload or following the link into a second tab dropped the token and every
 * request started failing, which is indistinguishable from an outage.
 */

const TOKEN_KEY = "ptpbox.access-token";

/** Raised on 401/403 so callers can tell "not allowed" from "not reachable". */
export class AuthError extends Error {
  constructor(readonly status: number) {
    super(status === 403 ? "this token is read-only" : "an access token is required");
    this.name = "AuthError";
  }
}

const TOKEN_HEADER = "X-PTPBox-Token";

export type AgentRole = "operator" | "viewer" | null;

export function readToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    // Private browsing modes can refuse storage; the query token still works for
    // the life of the page.
    return null;
  }
}

export function storeToken(token: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* nothing to do; the in-memory fallback below still applies */
  }
  memoryToken = token;
}

let memoryToken: string | null = null;

/** Lift a token out of the URL, remember it, and clean the address bar. */
export function adoptTokenFromLocation(): string | null {
  if (typeof window === "undefined") return null;
  const url = new URL(window.location.href);
  const supplied = url.searchParams.get("token") ?? url.searchParams.get("access_token");
  if (supplied) {
    storeToken(supplied);
    url.searchParams.delete("token");
    url.searchParams.delete("access_token");
    window.history.replaceState({}, "", url.toString());
    return supplied;
  }
  return readToken() ?? memoryToken;
}

export async function agentFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const token = readToken() ?? memoryToken;
  const headers = new Headers(init.headers ?? {});
  if (token) headers.set(TOKEN_HEADER, token);
  const response = await fetch(url, { ...init, headers });
  // Raised rather than returned so no caller can mistake "refused" for
  // "unreachable" and quietly substitute simulated data for measurements.
  if (response.status === 401 || response.status === 403) throw new AuthError(response.status);
  return response;
}

export type AccessState = {
  role: AgentRole;
  needsToken: boolean;
  reason?: string;
};

/** Ask the agent who we are. A 401 means a token is required to see anything. */
export async function probeAccess(base: string): Promise<AccessState> {
  try {
    const response = await agentFetch(`${base}/api/access`);
    if (!response.ok) return { role: null, needsToken: false };
    const body = await response.json() as { role?: AgentRole };
    return { role: body.role ?? null, needsToken: false };
  } catch (error) {
    if (error instanceof AuthError) return { role: null, needsToken: true, reason: error.message };
    return { role: null, needsToken: false };
  }
}
