/**
 * Access token handling for every call the UI makes to the agent.
 *
 * A shared link is the point of this: someone outside the lab is handed one URL
 * carrying a token. The token is taken out of the address bar immediately and
 * kept in session storage, because a token left in the URL ends up in browser
 * history, server logs and referrer headers, and would be copied along with any
 * screenshot of the address bar.
 *
 * Session storage rather than local storage, so closing the tab ends the loan.
 */

const TOKEN_KEY = "ptpbox.access-token";
const TOKEN_HEADER = "X-PTPBox-Token";

export type AgentRole = "operator" | "viewer" | null;

export function readToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch {
    // Private browsing modes can refuse storage; the query token still works for
    // the life of the page.
    return null;
  }
}

export function storeToken(token: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (token) window.sessionStorage.setItem(TOKEN_KEY, token);
    else window.sessionStorage.removeItem(TOKEN_KEY);
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

export function agentFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const token = readToken() ?? memoryToken;
  const headers = new Headers(init.headers ?? {});
  if (token) headers.set(TOKEN_HEADER, token);
  return fetch(url, { ...init, headers });
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
    if (response.status === 401) {
      const body = await response.json().catch(() => ({}));
      return { role: null, needsToken: true, reason: body?.error };
    }
    if (!response.ok) return { role: null, needsToken: false };
    const body = await response.json() as { role?: AgentRole };
    return { role: body.role ?? null, needsToken: false };
  } catch {
    return { role: null, needsToken: false };
  }
}
