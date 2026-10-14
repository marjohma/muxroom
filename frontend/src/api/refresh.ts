import type { SessionList } from "./types";

export const FAST_REFRESH_MS = 3_000;
export const SLOW_REFRESH_MS = 15_000;

/**
 * Refresh a host's sessions quickly while you are looking at muxroom or an
 * agent on that host is working; slowly otherwise. A working→waiting change
 * is therefore seen within seconds; a change you cause elsewhere while the
 * window is unfocused within 15 s (focusing the window refreshes at once).
 */
export function sessionsRefreshInterval(data: SessionList | undefined, focused: boolean): number {
  if (focused) return FAST_REFRESH_MS;
  const working = data?.sessions.some((s) => s.agent?.state === "working") ?? false;
  return working ? FAST_REFRESH_MS : SLOW_REFRESH_MS;
}

export function windowFocused(): boolean {
  return typeof document !== "undefined" && document.hasFocus();
}
