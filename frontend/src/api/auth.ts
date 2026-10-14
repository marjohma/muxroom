/**
 * Access token handling. The token arrives in the URL fragment (#token=…),
 * which browsers never send to the server, and is kept in sessionStorage:
 * scoped to this origin (host and port) and this tab, never in a cookie.
 */
const KEY = "muxroom.token";
const TOKEN_RE = /^[A-Za-z0-9_-]{32,}$/;

export function captureTokenFromUrl(): void {
  const match = window.location.hash.match(/(?:^#|&)token=([A-Za-z0-9_-]+)/);
  if (!match) return;
  sessionStorage.setItem(KEY, match[1]);
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
}

export function getToken(): string | null {
  return sessionStorage.getItem(KEY);
}

/** Accepts a pasted access link or a bare token; returns false if neither. */
export function setTokenFromInput(text: string): boolean {
  const value = text.trim();
  const fromLink = value.match(/[#&?]token=([A-Za-z0-9_-]+)/)?.[1];
  const token = fromLink ?? value;
  if (!TOKEN_RE.test(token)) return false;
  sessionStorage.setItem(KEY, token);
  return true;
}

export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** WebSockets cannot send headers; the token travels as a subprotocol. */
export function webSocketProtocols(): string[] {
  const token = getToken();
  return token ? ["muxroom", `muxroom.token.${token}`] : ["muxroom"];
}
