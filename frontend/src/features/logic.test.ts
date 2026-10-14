import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, parseError } from "@/api/client";
import type { Session } from "@/api/types";
import { breadcrumbs, containingRoot, formatSize, suggestSessionName } from "./files/paths";
import { initialWatch, observe } from "./preview/watcher";
import { EMPTY, loadTabs, matchesSession, saveTabs, STORAGE_KEY, tabsReducer } from "./tabs/tabs";
import { parseServerMessage, resizeMessage, shouldReconnect } from "./terminal/protocol";
import { backoffDelay, Reconnector } from "./terminal/reconnect";

const session = (id: string, name: string, created = "2026-10-14T10:00:00Z"): Session => ({
  id,
  name,
  created,
  last_activity: created,
  windows: 1,
  attached_clients: 0,
  path: "/home/u",
});

describe("terminal protocol", () => {
  it("parses control frames and ignores junk", () => {
    expect(parseServerMessage('{"type":"exit","reason":"session_ended"}')).toEqual({
      type: "exit",
      reason: "session_ended",
    });
    expect(parseServerMessage("not json")).toBeNull();
    expect(parseServerMessage('{"type":"other"}')).toBeNull();
  });

  it("encodes resize", () => {
    expect(JSON.parse(resizeMessage(120, 40))).toEqual({ type: "resize", cols: 120, rows: 40 });
  });

  it("decides when reconnecting helps", () => {
    expect(shouldReconnect(1006, null)).toBe(true);
    expect(shouldReconnect(1006, "transport_lost")).toBe(true);
    expect(shouldReconnect(1000, "session_ended")).toBe(false);
    expect(shouldReconnect(4404, null)).toBe(false);
    expect(shouldReconnect(4401, null)).toBe(false);
  });
});

describe("reconnect backoff", () => {
  afterEach(() => vi.useRealTimers());

  it("grows exponentially and caps", () => {
    expect([0, 1, 2, 3, 4, 5, 10].map(backoffDelay)).toEqual([500, 1000, 2000, 4000, 8000, 10000, 10000]);
  });

  it("schedules, resets on success, and cancels", () => {
    vi.useFakeTimers();
    const connect = vi.fn();
    const r = new Reconnector(connect);
    expect(r.schedule()).toBe(500);
    vi.advanceTimersByTime(499);
    expect(connect).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(connect).toHaveBeenCalledTimes(1);
    expect(r.schedule()).toBe(1000);
    r.cancel();
    vi.advanceTimersByTime(5000);
    expect(connect).toHaveBeenCalledTimes(1);
    r.succeeded();
    expect(r.schedule()).toBe(500);
  });
});

describe("tabs", () => {
  it("opens, dedupes, activates and closes", () => {
    let s = tabsReducer(EMPTY, { type: "open", hostId: "h", session: session("$1", "a") });
    s = tabsReducer(s, { type: "open", hostId: "h", session: session("$2", "b") });
    expect(s.tabs).toHaveLength(2);
    const first = s.tabs[0].id;
    s = tabsReducer(s, { type: "open", hostId: "h", session: session("$1", "a") });
    expect(s.tabs).toHaveLength(2);
    expect(s.activeId).toBe(first);
    s = tabsReducer(s, { type: "close", id: first });
    expect(s.tabs.map((t) => t.sessionName)).toEqual(["b"]);
    expect(s.activeId).toBe(s.tabs[0].id);
  });

  it("persists previews and orientation but not stale flags", () => {
    let s = tabsReducer(EMPTY, { type: "open", hostId: "h", session: session("$1", "a") });
    const id = s.tabs[0].id;
    s = tabsReducer(s, {
      type: "setPreview",
      id,
      preview: { hostId: "h", path: "/p/fig.pdf", name: "fig.pdf", kind: "pdf" },
    });
    s = tabsReducer(s, { type: "setOrientation", id, orientation: "vertical" });
    s = tabsReducer(s, { type: "markStale", id, stale: true });
    const store = new Map<string, string>();
    saveTabs({ setItem: (k, v) => store.set(k, v) }, s);
    const restored = loadTabs({ getItem: (k) => store.get(k) ?? null });
    expect(restored.tabs[0].preview?.path).toBe("/p/fig.pdf");
    expect(restored.tabs[0].orientation).toBe("vertical");
    expect(restored.tabs[0].stale).toBe(false);
    expect(restored.activeId).toBe(id);
  });

  it("follows renames without duplicating tabs", () => {
    let s = tabsReducer(EMPTY, { type: "open", hostId: "h", session: session("$1", "0") });
    const id = s.tabs[0].id;
    s = tabsReducer(s, { type: "setName", id, name: "kim-bulk" });
    expect(s.tabs[0].sessionName).toBe("kim-bulk");
    expect(tabsReducer(s, { type: "setName", id, name: "kim-bulk" })).toBe(s);
    s = tabsReducer(s, { type: "open", hostId: "h", session: session("$1", "kim-bulk") });
    expect(s.tabs).toHaveLength(1);
  });

  it("drops corrupt storage", () => {
    expect(loadTabs({ getItem: () => "{nope" })).toEqual(EMPTY);
    const bad = JSON.stringify({ tabs: [{ id: 1 }, { id: "x", hostId: "h", sessionId: "3", sessionName: "a", createdAt: "t" }] });
    expect(loadTabs({ getItem: (k) => (k === STORAGE_KEY ? bad : null) }).tabs).toEqual([]);
  });

  it("detects session id reuse after a tmux server restart", () => {
    const s = tabsReducer(EMPTY, { type: "open", hostId: "h", session: session("$1", "a") });
    const tab = s.tabs[0];
    expect(matchesSession(tab, [session("$1", "a")])).toBe(true);
    expect(matchesSession(tab, [session("$1", "renamed")])).toBe(true); // renames keep the tab
    expect(matchesSession(tab, [session("$1", "a", "2026-10-15T00:00:00Z")])).toBe(false);
    expect(matchesSession(tab, [])).toBe(false);
  });
});

describe("preview watcher", () => {
  it("reloads only after two identical new versions", () => {
    let w = initialWatch("v1");
    w = observe(w, "v1");
    expect(w.shown).toBe("v1");
    w = observe(w, "v2");
    expect(w.shown).toBe("v1");
    w = observe(w, "v3"); // still being written
    expect(w.shown).toBe("v1");
    w = observe(w, "v3");
    expect(w.shown).toBe("v3");
  });

  it("ignores a flicker back to the shown version", () => {
    let w = initialWatch("v1");
    w = observe(w, "v2");
    w = observe(w, "v1");
    w = observe(w, "v2");
    expect(w.shown).toBe("v1");
  });

  it("adopts the first version when nothing is shown yet", () => {
    expect(observe(initialWatch(null), "v9").shown).toBe("v9");
  });
});

describe("paths", () => {
  it("builds breadcrumbs that stop at the root", () => {
    expect(breadcrumbs("/home/u/proj/plots", "/home/u/proj").map((c) => c.label)).toEqual(["proj", "plots"]);
    expect(breadcrumbs("/home/u/proj", "/home/u/proj")).toHaveLength(1);
  });

  it("finds the most specific root by path components", () => {
    expect(containingRoot("/a/b/c", ["/a", "/a/b"])).toBe("/a/b");
    expect(containingRoot("/a/bc", ["/a/b"])).toBeNull();
  });

  it("formats sizes and session names", () => {
    expect(formatSize(512)).toBe("512 B");
    expect(formatSize(1536)).toBe("1.5 KiB");
    expect(formatSize(5 * 1024 ** 3)).toBe("5.0 GiB");
    expect(suggestSessionName("claude", "/home/u/My Project!")).toBe("claude-My-Project");
    expect(suggestSessionName("shell", "/")).toBe("sh-home");
  });
});

describe("api errors", () => {
  it("parses the backend error envelope", async () => {
    const response = new Response(
      JSON.stringify({ error: { code: "session_exists", message: "dup", hint: "rename" } }),
      { status: 409 },
    );
    const err = await parseError(response);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("session_exists");
    expect(err.hint).toBe("rename");
  });

  it("explains 405s from an older backend", async () => {
    const err = await parseError(new Response(JSON.stringify({ detail: "Method Not Allowed" }), { status: 405 }));
    expect(err.code).toBe("unknown_endpoint");
    expect(err.hint).toMatch(/Restart muxroom/);
  });

  it("falls back for non-JSON errors", async () => {
    const err = await parseError(new Response("oops", { status: 401 }));
    expect(err.code).toBe("unauthorized");
  });
});
