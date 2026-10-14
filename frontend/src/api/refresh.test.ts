import { describe, expect, it } from "vitest";
import { FAST_REFRESH_MS, SLOW_REFRESH_MS, sessionsRefreshInterval } from "./refresh";
import type { Session, SessionList } from "./types";

const session = (agent: Session["agent"]): Session => ({
  id: "$1", name: "s", created: "", last_activity: "", windows: 1, attached_clients: 0, path: "/", agent,
});
const list = (...sessions: Session[]): SessionList => ({ sessions, fetched_at: "" });

describe("adaptive session refresh", () => {
  it("is fast while the window has focus", () => {
    expect(sessionsRefreshInterval(list(), true)).toBe(FAST_REFRESH_MS);
    expect(sessionsRefreshInterval(undefined, true)).toBe(FAST_REFRESH_MS);
  });

  it("stays fast in the background while an agent works", () => {
    const data = list(session(null), session({ id: "claude", label: "Claude Code", state: "working" }));
    expect(sessionsRefreshInterval(data, false)).toBe(FAST_REFRESH_MS);
  });

  it("slows down in the background when nothing is working", () => {
    expect(sessionsRefreshInterval(list(session({ id: "codex", label: "Codex", state: "waiting" })), false)).toBe(SLOW_REFRESH_MS);
    expect(sessionsRefreshInterval(list(session(null)), false)).toBe(SLOW_REFRESH_MS);
    expect(sessionsRefreshInterval(undefined, false)).toBe(SLOW_REFRESH_MS);
  });
});
